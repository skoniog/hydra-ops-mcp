"""hydra-ops-mcp — operator MCP server for Hydra heads, with hydra-tui parity.

Read tools run immediately. Every state-changing tool is gated: called
without confirm=True it returns a description of what it would do and
changes nothing. Run with `python server.py` or `fastmcp dev server.py`.
"""

from fastmcp import FastMCP

from tools import diagnose, lifecycle, observe, preflight as preflight_mod, provision, transact

mcp = FastMCP("hydra-ops")


# --- provisioning (spin up a node, connect to peers) ---


@mcp.tool
def generate_keys(party: str, overwrite: bool = False, confirm: bool = False) -> dict:
    """Create fuel + funds + Hydra key pairs for a party. confirm=True to execute."""
    return provision.generate_keys(party, overwrite, confirm)


@mcp.tool
def fuel_status(party: str) -> dict:
    """The party's node-wallet (fuel) address and balance, and whether it can operate."""
    return provision.fuel_status(party)


@mcp.tool
def build_protocol_parameters(confirm: bool = False) -> dict:
    """Write the head ledger parameters from live network values, zeroing only fees."""
    return provision.build_protocol_parameters(confirm)


@mcp.tool
def share_peer_info(party: str, host: str, port: int = 5001) -> dict:
    """Emit what a counterparty needs to add this party as a peer."""
    return provision.share_peer_info(party, host, port)


@mcp.tool
def node_plan(party: str, peers: list = None, api_port: int = 4101,
              listen_port: int = 5101, metrics_port: int = 6101,
              advertise_host: str = "127.0.0.1") -> dict:
    """Preview the exact hydra-node container command that would run. Read-only."""
    return provision.node_plan(party, peers, api_port, listen_port,
                               metrics_port, advertise_host)


@mcp.tool
def start_node(party: str, peers: list = None, api_port: int = 4101,
               listen_port: int = 5101, metrics_port: int = 6101,
               advertise_host: str = "127.0.0.1", confirm: bool = False) -> dict:
    """Start this party's hydra-node container. confirm=True to execute."""
    return provision.start_node(party, peers, api_port, listen_port,
                                metrics_port, advertise_host, confirm)


@mcp.tool
def stop_node(party: str, confirm: bool = False) -> dict:
    """Stop and remove this party's node container. confirm=True to execute."""
    return provision.stop_node(party, confirm)


@mcp.tool
def node_health(node: int = 1) -> dict:
    """Container, API, head, peer-connectivity and fuel health for one node."""
    return provision.node_health(node)


# --- observability (read-only) ---


@mcp.tool
def head_status(node: int = 1) -> dict:
    """Current head state on a node: status, UTXO count, total value, snapshot."""
    return observe.head_status(node)


@mcp.tool
def head_utxos(node: int = 1) -> dict:
    """The UTXO set inside the head, grouped by address."""
    return observe.head_utxos(node)


@mcp.tool
def l1_funds(party: str = "alice") -> dict:
    """A party's L1 wallet: address, UTXOs, total lovelace."""
    return observe.l1_funds(party)


@mcp.tool
def protocol_parameters(node: int = 1) -> dict:
    """The ledger parameters the head runs with (fees, min-UTXO, sizes)."""
    return observe.protocol_parameters(node)


@mcp.tool
def pending_deposits(node: int = 1) -> dict:
    """Deposits submitted but not yet absorbed into the head."""
    return observe.pending_deposits(node)


@mcp.tool
def recent_events(node: int = 1, tag: str = None, limit: int = 25) -> dict:
    """Recent server events observed on this connection, optionally by tag."""
    return observe.recent_events(node, tag, limit)


# --- lifecycle (confirm-gated) ---


@mcp.tool
def init_head(node: int = 1, confirm: bool = False) -> dict:
    """Initialize a new head. Requires confirm=True to execute."""
    return lifecycle.init_head(node, confirm)


@mcp.tool
def commit_funds(party: str = "alice", node: int = 1, utxo_ref: str = "",
                 confirm: bool = False) -> dict:
    """Deposit one of a party's L1 UTXOs into the head. confirm=True to execute."""
    return lifecycle.commit_funds(party, node, utxo_ref, confirm)


@mcp.tool
def decommit(utxo_ref: str, node: int = 1, confirm: bool = False) -> dict:
    """Withdraw one head UTXO to the L1 without closing. confirm=True to execute."""
    return lifecycle.decommit(utxo_ref, node, confirm)


@mcp.tool
def close_head(node: int = 1, confirm: bool = False) -> dict:
    """Close the head for ALL participants. confirm=True to execute."""
    return lifecycle.close_head(node, confirm)


@mcp.tool
def fanout(node: int = 1, confirm: bool = False) -> dict:
    """Distribute the closed head's UTXOs to the L1. confirm=True to execute."""
    return lifecycle.fanout(node, confirm)


@mcp.tool
def partial_fanout(utxo_refs: list, node: int = 1, confirm: bool = False) -> dict:
    """Fan out only selected UTXOs of a closed head. confirm=True to execute."""
    return lifecycle.partial_fanout(utxo_refs, node, confirm)


@mcp.tool
def recover_deposit(tx_id: str, node: int = 1, confirm: bool = False) -> dict:
    """Recover a stuck deposit back to the L1. confirm=True to execute."""
    return lifecycle.recover_deposit(tx_id, node, confirm)


@mcp.tool
def wait_for_event(tags: list, node: int = 1, timeout_seconds: int = 120) -> dict:
    """Wait (bounded, max 600s) for one of the named server events, e.g.
    ReadyToFanout after a short contestation period."""
    return lifecycle.wait_for_event(tags, node, timeout_seconds)


# --- transactions (confirm-gated) ---


@mcp.tool
def send_tx(sender: str, receiver: str, amount_lovelace: int,
            node: int = 1, confirm: bool = False) -> dict:
    """Send ADA inside the head (party name or address). confirm=True to execute."""
    return transact.send_tx(sender, receiver, amount_lovelace, node, confirm)


# --- diagnosis (read-only) ---


@mcp.tool
def node_logs(node: int = 1, pattern: str = "", since: str = "10m",
              limit: int = 40) -> dict:
    """Recent hydra-node container logs, optionally regex-filtered."""
    return diagnose.node_logs(node, pattern, since, limit)


@mcp.tool
def explain_error(code: str) -> dict:
    """Decode a Hydra on-chain error code (e.g. H39) from the Plutus source."""
    return diagnose.explain_error(code)


@mcp.tool
def sideload_snapshot(from_node: int, to_node: int, confirm: bool = False) -> dict:
    """Recover a forked head by side-loading one node's confirmed snapshot
    into a diverged peer. confirm=True to execute."""
    return lifecycle.sideload_snapshot(from_node, to_node, confirm)


@mcp.tool
def preflight(node: int = 1) -> dict:
    """Run every operational safety check in one call: API, chain liveness,
    peers, fuel, contestation-period sanity, head-parameter traps, scripts."""
    return preflight_mod.preflight(node)


@mcp.tool
def list_parties() -> dict:
    """Every known party: addresses and whether this server holds their keys."""
    return preflight_mod.list_parties()


if __name__ == "__main__":
    mcp.run()
