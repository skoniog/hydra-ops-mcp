"""Preflight: every operational hazard from the v2 plan, checked in one call.

Each check returns pass / warn / fail / skip with a detail line; the overall
verdict is the worst individual result. Read-only.
"""

import json
import time

import httpx

import config
import networks
from providers import get_provider
from tools.types import ok

UNCLOSABLE_CP_FLOOR = 30       # below this, Close txs expire before a block
MAINNET_CP_FLOOR = 43_200      # docs: >= 12h on mainnet


def _check(name, status, detail):
    return {"check": name, "status": status, "detail": detail}


def preflight(node: int = 1) -> dict:
    checks = []

    # 1. Node API reachable + head state.
    nodes = config.reload_nodes()
    entry = nodes.get(node)
    head_state = None
    if entry is None:
        checks.append(_check("api", "fail", f"node {node} is not registered"))
    else:
        try:
            r = httpx.get(f"{entry['http']}/head", timeout=5.0)
            head_state = r.json().get("tag") if r.status_code == 200 else None
            checks.append(_check("api", "pass" if head_state else "fail",
                                 f"{entry['http']} -> head {head_state}"))
        except Exception as e:
            checks.append(_check("api", "fail",
                                 f"{entry['http']} unreachable: {type(e).__name__}"))

    # 2. Chain liveness: the tip must advance between two samples. A node
    #    unsynced for CP/2 stops signing snapshots, and a stalled chain is
    #    this devnet's classic wedge.
    try:
        provider = get_provider()
        slot_a = provider.tip().get("slot")
        time.sleep(3)
        slot_b = provider.tip().get("slot")
        if slot_a is None:
            checks.append(_check("chain", "fail", "no tip from the L1 provider"))
        elif slot_b > slot_a:
            checks.append(_check("chain", "pass",
                                 f"tip advancing (slot {slot_a} -> {slot_b})"))
        else:
            checks.append(_check("chain", "fail",
                                 f"tip NOT advancing (slot {slot_a}); a node "
                                 f"unsynced for CP/2 stops signing snapshots"))
    except Exception as e:
        checks.append(_check("chain", "fail", f"tip query failed: {e}"))

    # 3. Peer connectivity (needs a metrics endpoint — provisioned nodes have one).
    if entry and entry.get("metrics"):
        try:
            import node_manager
            peers = node_manager.metrics(entry["metrics"]).get(
                "hydra_head_peers_connected")
            expected = len([p for p in config.reload_parties() if True]) - 1
            status = "pass" if (peers or 0) >= 1 else "warn"
            checks.append(_check("peers", status,
                                 f"hydra_head_peers_connected={peers}"))
        except Exception as e:
            checks.append(_check("peers", "warn", f"metrics unreadable: {e}"))
    else:
        checks.append(_check("peers", "skip", "no metrics endpoint registered"))

    # 4. Fuel for every party whose node we operate.
    from cardano import address_for_vk_file
    fueled = []
    for party, info in config.reload_parties().items():
        if info.get("ours") and info.get("fuel_vk"):
            try:
                total = get_provider().total_lovelace(
                    address_for_vk_file(info["fuel_vk"]))
                fueled.append((party, total))
            except Exception:
                pass
    if fueled:
        low = [(p, t) for p, t in fueled if t < config.FUEL_THRESHOLD_LOVELACE]
        if low:
            checks.append(_check(
                "fuel", "warn" if config.NETWORK_NAME == "devnet" else "fail",
                "below threshold: " + ", ".join(f"{p}={t:,}" for p, t in low)))
        else:
            checks.append(_check("fuel", "pass",
                                 ", ".join(f"{p}={t:,}" for p, t in fueled)))
    else:
        checks.append(_check("fuel", "skip", "no fuel keys readable"))

    # 5. Contestation period sanity.
    cp = config.CONTESTATION_PERIOD
    if cp < UNCLOSABLE_CP_FLOOR and config.NETWORK_NAME != "devnet":
        checks.append(_check("contestation_period", "fail",
                             f"{cp}s risks an UNCLOSABLE head: the Close tx's "
                             f"validity window expires within one ~20s block"))
    elif config.IS_MAINNET and cp < MAINNET_CP_FLOOR:
        checks.append(_check("contestation_period", "warn",
                             f"{cp}s < the 12h mainnet guidance"))
    else:
        checks.append(_check("contestation_period", "pass", f"{cp}s"))

    # 6. Head ledger parameters: the unfanoutable-output trap.
    params_file = config.WORKSPACE / "protocol-parameters.json"
    if params_file.exists():
        params = json.loads(params_file.read_text())
        if not params.get("utxoCostPerByte") and config.NETWORK_NAME != "devnet":
            checks.append(_check("head_params", "fail",
                                 "utxoCostPerByte=0: outputs created under "
                                 "these parameters can NEVER be fanned out"))
        else:
            checks.append(_check("head_params", "pass",
                                 f"utxoCostPerByte={params.get('utxoCostPerByte')}"))
    else:
        checks.append(_check("head_params", "skip",
                             "no workspace parameters file (devnet demo nodes "
                             "bring their own)"))

    # 7. Hydra scripts published for this network + node version.
    try:
        import node_manager
        ids = node_manager._scripts_tx_id()
        checks.append(_check("scripts", "pass", ids[:40] + "…"))
    except Exception as e:
        checks.append(_check("scripts", "fail", str(e)))

    order = {"fail": 0, "warn": 1, "skip": 2, "pass": 3}
    worst = min((c["status"] for c in checks), key=lambda s: order[s])
    verdict = {"fail": "NOT SAFE to operate",
               "warn": "operable with warnings",
               "skip": "operable (some checks skipped)",
               "pass": "all checks passed"}[worst]
    return ok("ok", network=config.NETWORK_NAME, node=node,
              head_state=head_state, verdict=verdict, checks=checks)


def list_parties() -> dict:
    """Every known party: addresses, and whether we hold their keys."""
    from cardano import address_for_vk_file
    from pathlib import Path
    rows = {}
    for party, info in config.reload_parties().items():
        row = {"ours": bool(info.get("ours"))}
        for wallet in ("funds", "fuel"):
            vk = info.get(f"{wallet}_vk")
            if vk and Path(vk).exists():
                row[f"{wallet}_address"] = address_for_vk_file(vk)
            row[f"can_sign_{wallet}"] = bool(
                info.get(f"{wallet}_sk") and Path(info[f"{wallet}_sk"]).exists())
        rows[party] = row
    return ok("ok", network=config.NETWORK_NAME, parties=rows)
