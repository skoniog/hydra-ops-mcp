"""Provisioning tools: keys, head parameters, peers, and node lifecycle.

The 'spin up a node and connect to peers' surface. State-changing tools are
confirmation-gated like everything else; key generation is gated too since
overwriting keys is destructive.
"""

import node_manager
from cardano import address_for_vk_file
from providers import get_provider
import config
from tools.types import err, needs_confirmation, ok


def generate_keys(party: str, overwrite: bool = False, confirm: bool = False) -> dict:
    """Create fuel + funds + Hydra key pairs for a party we will operate."""
    if not confirm:
        note = " OVERWRITING existing keys (destructive!)" if overwrite else ""
        return needs_confirmation(
            f"generate Cardano fuel + funds key pairs and Hydra keys for "
            f"{party!r} on {config.NETWORK_NAME}, stored under "
            f"{config.WORKSPACE}/keys/{party}{note}", party=party)
    try:
        result = node_manager.generate_party_keys(party, overwrite=overwrite)
    except Exception as e:
        return err(str(e), party=party)
    return ok("generated", **result,
              next_step=(f"fund the fuel address with ~30 tADA "
                         f"({result['fuel_address']}) before starting the node"
                         if config.NETWORK_NAME != "devnet" else None))


def fuel_status(party: str) -> dict:
    """The party's node-wallet balance and whether it can operate."""
    parties = config.reload_parties()
    info = parties.get(party)
    if not info or not info.get("fuel_vk"):
        return err(f"no fuel key for {party!r}; run generate_keys", party=party)
    try:
        address = address_for_vk_file(info["fuel_vk"])
        total = get_provider().total_lovelace(address)
    except Exception as e:
        return err(str(e), party=party)
    return ok("ok", party=party, address=address, lovelace=total,
              sufficient=total >= config.FUEL_THRESHOLD_LOVELACE,
              threshold=config.FUEL_THRESHOLD_LOVELACE)


def build_protocol_parameters(confirm: bool = False) -> dict:
    """Write the head's ledger parameters from live network values, zeroing
    only what is safe to zero."""
    if not confirm:
        return needs_confirmation(
            f"query {config.NETWORK_NAME} protocol parameters via the "
            f"{config.PROVIDER} provider, zero the transaction-fee fields "
            f"(and only those), and write "
            f"{config.WORKSPACE}/protocol-parameters.json")
    try:
        path = node_manager.build_head_protocol_parameters()
        import json
        params = json.loads(path.read_text())
    except Exception as e:
        return err(str(e))
    return ok("written", path=str(path),
              tx_fee_fixed=params.get("txFeeFixed"),
              utxo_cost_per_byte=params.get("utxoCostPerByte"))


def share_peer_info(party: str, host: str, port: int = 5001) -> dict:
    """What a counterparty needs to add us as a peer. Send them this; paste
    theirs into start_node's peers argument."""
    try:
        info = node_manager.peer_info(party, host, port)
    except Exception as e:
        return err(str(e), party=party)
    return ok("ok", peer_info=info,
              note="send peer_info to your counterparty; both sides must use "
                   "the same contestation period or Init is ignored")


def node_plan(party: str, peers: list = None, api_port: int = 4101,
              listen_port: int = 5101, metrics_port: int = 6101,
              advertise_host: str = "127.0.0.1") -> dict:
    """Preview the exact container command a start_node would run. Read-only."""
    try:
        plan = node_manager.plan_node(party, peers or [], api_port,
                                      listen_port, metrics_port, advertise_host)
    except Exception as e:
        return err(str(e), party=party)
    return ok("ok", **{k: v for k, v in plan.items() if k != "command"},
              docker_command=" ".join(plan["command"]))


def start_node(party: str, peers: list = None, api_port: int = 4101,
               listen_port: int = 5101, metrics_port: int = 6101,
               advertise_host: str = "127.0.0.1", confirm: bool = False) -> dict:
    """Run this party's hydra-node as a container and register it."""
    try:
        plan = node_manager.plan_node(party, peers or [], api_port,
                                      listen_port, metrics_port, advertise_host)
    except Exception as e:
        return err(str(e), party=party)
    if not confirm:
        return needs_confirmation(
            f"start hydra-node container {plan['container']} "
            f"({plan['image']}) on {config.NETWORK_NAME} — API :{api_port}, "
            f"p2p :{listen_port}, metrics :{metrics_port}, "
            f"{len(peers or [])} peer(s)",
            party=party, flags=plan["flags"])
    try:
        result = node_manager.start(plan)
        api_up = node_manager.wait_api(f"http://127.0.0.1:{api_port}")
    except Exception as e:
        return err(str(e), party=party)
    return ok("started", **result, api_ready=api_up,
              api=f"http://127.0.0.1:{api_port}")


def stop_node(party: str, confirm: bool = False) -> dict:
    """Stop and remove this party's node container (persistence is kept)."""
    state = node_manager.container_state(party)
    if not confirm:
        return needs_confirmation(
            f"stop and remove container hydra-ops-{party} (currently {state}); "
            f"head state persists on disk and the node can be started again",
            party=party)
    try:
        node_manager.stop(party)
    except Exception as e:
        return err(str(e), party=party)
    return ok("stopped", party=party, was=state)


def node_health(node: int = 1) -> dict:
    """Container, API, chain-sync, peer, and fuel health for one node."""
    nodes = config.reload_nodes()
    entry = nodes.get(node)
    if entry is None:
        return err(f"unknown node {node}; known: {sorted(nodes)}", node=node)

    health: dict = {"node": node, "name": entry.get("name")}
    if entry.get("container"):
        health["container"] = node_manager.container_state(entry["name"])

    try:
        import httpx
        head = httpx.get(f"{entry['http']}/head", timeout=5.0)
        health["api"] = "up" if head.status_code == 200 else f"http {head.status_code}"
        health["head_state"] = head.json().get("tag") if head.status_code == 200 else None
    except Exception as e:
        health["api"] = f"unreachable ({type(e).__name__})"

    if entry.get("metrics"):
        try:
            m = node_manager.metrics(entry["metrics"])
            health["peers_connected"] = m.get("hydra_head_peers_connected")
        except Exception:
            health["peers_connected"] = None

    party = entry.get("name")
    parties = config.reload_parties()
    if party in parties and parties[party].get("fuel_vk"):
        try:
            addr = address_for_vk_file(parties[party]["fuel_vk"])
            fuel = get_provider().total_lovelace(addr)
            health["fuel_lovelace"] = fuel
            health["fuel_sufficient"] = fuel >= config.FUEL_THRESHOLD_LOVELACE
        except Exception:
            health["fuel_lovelace"] = None

    return ok("ok", **health)
