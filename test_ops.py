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

    def fanout_readiness(self):
        # Read-only; report ready so gated tools proceed to their gate.
        return {"ready": True, "head_state": "FanoutPossible",
                "contestation_deadline": None, "seconds_remaining": 0.0}

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


def test_fanout_is_nonblocking_before_deadline():
    """Called before the contestation deadline, fanout must report the wait
    instead of hanging or asking for confirmation."""
    client = RecordingClient()
    client.fanout_readiness = lambda: {
        "ready": False, "head_state": "Closed",
        "contestation_deadline": "2099-01-01T00:00:00Z",
        "seconds_remaining": 4321.0,
    }
    patch_client(client)
    r = lifecycle.fanout(node=1, confirm=True)
    assert r["status"] == "not_ready", r
    assert r["seconds_remaining"] == 4321.0, r


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


def test_head_status_reads_snapshot_in_both_head_shapes():
    """An Open head nests confirmedSnapshot under coordinatedHeadState; a
    Closed one puts it directly on contents. Both must resolve."""
    import tools.observe as ob

    class Stub:
        def __init__(self, head):
            self._head = head

        def get_head(self):
            return self._head

        def get_utxos(self):
            return {}

        def get_head_status(self):
            return "open"

    open_head = {"tag": "Open", "contents": {"coordinatedHeadState": {
        "version": 1, "confirmedSnapshot": {"snapshot": {"number": 7}}}}}
    closed_head = {"tag": "Closed", "contents": {
        "version": 2, "confirmedSnapshot": {"snapshot": {"number": 9}},
        "contestationDeadline": "2026-01-01T00:00:00Z"}}

    original = ob.get_client
    try:
        ob.get_client = lambda node=1: Stub(open_head)
        r = ob.head_status()
        assert r["snapshot_number"] == 7 and r["version"] == 1, r

        ob.get_client = lambda node=1: Stub(closed_head)
        r = ob.head_status()
        assert r["snapshot_number"] == 9 and r["version"] == 2, r
        assert r["contestation_deadline"] == "2026-01-01T00:00:00Z", r
    finally:
        ob.get_client = original


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


def test_sign_envelope_preserves_hashed_bytes():
    """sign_envelope must never re-serialize the body or aux data: it signs
    blake2b of the body's exact byte span and stitches original bytes back.
    Neither PyCardano nor cbor2 round-trips node drafts faithfully."""
    import cbor2
    import hashlib
    import tempfile
    import tx_builder
    from pycardano import PaymentSigningKey, PaymentVerificationKey

    with tempfile.TemporaryDirectory() as td:
        sk = PaymentSigningKey.generate()
        sk_path = td + "/k.sk"
        sk.save(sk_path)

        # A synthetic draft with a deliberately NON-canonical body encoding
        # (indefinite-length map) that cbor2/pycardano would rewrite.
        body = bytes.fromhex("bf") + cbor2.dumps(0) + cbor2.dumps(
            [[b"\x01" * 32, 0]]) + cbor2.dumps(1) + cbor2.dumps([]) + \
            cbor2.dumps(2) + cbor2.dumps(7) + bytes.fromhex("ff")
        raw = bytes.fromhex("84") + body + cbor2.dumps({}) + \
            cbor2.dumps(True) + bytes.fromhex("f6")
        draft = {"type": "Tx ConwayEra", "cborHex": raw.hex()}

        signed = tx_builder.sign_envelope(draft, sk_path)
        out = bytes.fromhex(signed["cborHex"])

        # Body bytes preserved verbatim.
        assert body in out, "body was re-serialized"
        # The witness signs the hash of those exact bytes.
        decoded = cbor2.loads(out)
        vk_bytes, sig = decoded[1][0][0]
        vk = PaymentVerificationKey.from_signing_key(sk)
        assert vk_bytes == vk.to_primitive()
        expected = hashlib.blake2b(body, digest_size=32).digest()
        # Ed25519 verify via pycardano's key object
        from nacl.signing import VerifyKey
        VerifyKey(bytes(vk_bytes)).verify(expected, sig)  # raises if invalid


def test_scripts_override_beats_devnet_env():
    """HYDRA_SCRIPTS_TX_ID must override the devnet's published scripts —
    required to run an unstable image whose validators differ."""
    import os
    import node_manager

    os.environ["HYDRA_SCRIPTS_TX_ID"] = "ab" * 32
    try:
        assert node_manager._scripts_tx_id() == "ab" * 32
    finally:
        del os.environ["HYDRA_SCRIPTS_TX_ID"]


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
        # v2
        "generate_keys", "fuel_status", "build_protocol_parameters",
        "share_peer_info", "node_plan", "start_node", "stop_node",
        "node_health", "wait_for_event", "sideload_snapshot",
        "preflight", "list_parties",
    }
    assert expected <= names, f"missing: {expected - names}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n{len(tests)} tests passed")
