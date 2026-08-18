"""hydra-ops-mcp — operator MCP server for Hydra heads, with hydra-tui parity.

Read tools run immediately. Every state-changing tool is gated: called
without confirm=True it returns a description of what it would do and
changes nothing. Run with `python server.py` or `fastmcp dev server.py`.
"""

from fastmcp import FastMCP

from tools import diagnose, lifecycle, observe, transact

mcp = FastMCP("hydra-ops")


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


if __name__ == "__main__":
    mcp.run()
