"""Offline tests for hydra-ops-mcp: no devnet required.

Covers the confirmation gate on every state-changing tool (each must return
requires_confirmation and touch nothing without confirm=True), request
payload shapes, and the error decoder.

Run with: .venv/bin/python test_ops.py
"""

import errors as error_tables
from tools import diagnose, lifecycle, transact
from tools.types import needs_confirmation


class RecordingClient:
    """Stands in for HydraClient; records calls, fails if commands are sent."""

    def __init__(self):
        self.calls = []
        self.utxos = {
            "aa" * 32 + "#0": {"address": "addr_test1_owner",
                               "value": {"lovelace": 5_000_000}},
            "bb" * 32 + "#1": {"address": "addr_test1_other",
                               "value": {"lovelace": 3_000_000}},
        }

    def get_head(self):
        return {"tag": "Open", "contents": {}}

    def get_utxos(self):
        return self.utxos

    def __getattr__(self, name):
        def _fail(*a, **kw):
            raise AssertionError(f"state-changing call {name}() reached the client "
                                 f"without confirm=True")
        self.calls.append(name)
        return _fail


def patch_client(monkey):
    import tools.lifecycle as lc
    import tools.transact as tx
    lc.get_client = lambda node=1: monkey
    tx.get_client = lambda node=1: monkey


def test_all_lifecycle_tools_are_gated():
    client = RecordingClient()
    patch_client(client)

    gated_calls = [
        ("init_head", lambda: lifecycle.init_head(node=1)),
        ("close_head", lambda: lifecycle.close_head(node=1)),
        ("fanout", lambda: lifecycle.fanout(node=1)),
        ("partial_fanout", lambda: lifecycle.partial_fanout([list(client.utxos)[0]])),
        ("recover_deposit", lambda: lifecycle.recover_deposit("ff" * 32)),
    ]
    for name, call in gated_calls:
        result = call()
        assert result["status"] == "requires_confirmation", (name, result)
        assert "confirm=True" in result["message"], (name, result)


def test_commit_funds_gated_without_touching_l1():
    import tools.lifecycle as lc
    original = lc.cardano.l1_utxos
    lc.cardano.l1_utxos = lambda party: {
        "cc" * 32 + "#0": {"address": "addr_test1_x", "value": {"lovelace": 9_000_000}}
    }
    try:
        result = lifecycle.commit_funds("alice")
        assert result["status"] == "requires_confirmation", result
        assert result["lovelace"] == 9_000_000, result
    finally:
        lc.cardano.l1_utxos = original


def test_send_tx_rejects_sub_min_utxo_amounts():
    result = transact.send_tx("alice", "bob", 500_000)
    assert result["status"] == "error", result
    assert "fanned out" in result["error"], result


def test_partial_fanout_validates_refs():
    patch_client(RecordingClient())
    result = lifecycle.partial_fanout(["nonexistent#0"])
    assert result["status"] == "error", result
    assert "not in the head" in result["error"], result


def test_needs_confirmation_shape():
    r = needs_confirmation("do the thing", extra=1)
    assert r["status"] == "requires_confirmation"
    assert r["error"] is None
    assert r["extra"] == 1


def test_error_table_parses_local_source():
    table = error_tables.error_table()
    assert len(table) >= 60, f"expected 60+ codes, got {len(table)}"
    assert table["H39"]["constructor"] == "FanoutUTxOHashMismatch", table["H39"]
    assert table["H1"]["constructor"] == "InvalidHeadStateTransition", table["H1"]


def test_explain_error_tool():
    r = diagnose.explain_error("h39")  # case-insensitive
    assert r["status"] == "ok" and r["constructor"] == "FanoutUTxOHashMismatch", r
    assert "note" in r, "H39 should carry the practical note"

    r = diagnose.explain_error("H9999")
    assert r["status"] == "error", r


def test_decommit_requires_known_owner():
    import tx_builder
    utxos = {"dd" * 32 + "#0": {"address": "addr_test1_unknown",
                                "value": {"lovelace": 2_000_000}}}
    try:
        tx_builder.build_decommit(utxos, "dd" * 32 + "#0")
    except tx_builder.TxBuildError as e:
        assert "no known signing key" in str(e), e
    else:
        raise AssertionError("decommit of an unknown-owner UTXO must fail")


def test_server_registers_all_tools():
    import asyncio
    from fastmcp import Client
    import server

    async def _list():
        async with Client(server.mcp) as c:
            return await c.list_tools()

    names = {t.name for t in asyncio.run(_list())}
    expected = {
        "head_status", "head_utxos", "l1_funds", "protocol_parameters",
        "pending_deposits", "recent_events",
        "init_head", "commit_funds", "decommit", "close_head",
        "fanout", "partial_fanout", "recover_deposit",
        "send_tx", "node_logs", "explain_error",
    }
    assert expected <= names, f"missing: {expected - names}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n{len(tests)} tests passed")
