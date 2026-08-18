"""Live lifecycle test against the local devnet — full hydra-tui parity.

Drives a head from Idle through every TUI-reachable operation:
  init → commit → observe → send_tx → decommit → close →
  partial_fanout → (auto-finalize) → back on L1

Requires the devnet to be up with the head Idle (run reset_devnet.sh /
open_head.py world's reset first if needed — but NOT open_head.py, this
test opens the head itself).

Run with: .venv/bin/python test_ops_devnet.py
"""

import sys
import time

import httpx


def head_tag() -> str:
    try:
        return httpx.get("http://127.0.0.1:4001/head", timeout=5.0).json().get("tag", "?")
    except Exception:
        return "unreachable"


tag = head_tag()
if tag == "unreachable":
    sys.exit("SKIP: no hydra-node on 127.0.0.1:4001 — start the devnet first")
if tag != "Idle":
    sys.exit(f"SKIP: head is {tag}, not Idle — this test opens its own head "
             f"(close/fanout or reset the devnet first)")


from tools import diagnose, lifecycle, observe, transact  # noqa: E402


def check(label, result, *statuses):
    assert result["status"] in statuses, (label, result)
    print(f"PASS {label}: {result['status']}")
    return result


def main():
    # 1. Gate sanity against the real node: no confirm -> nothing happens.
    check("init gate", lifecycle.init_head(), "requires_confirmation")
    assert head_tag() == "Idle", "gated init must not touch the head"

    # 2. Init for real.
    check("init_head", lifecycle.init_head(confirm=True), "initialized")

    # 3. Commit alice's largest L1 UTXO.
    r = check("commit_funds", lifecycle.commit_funds("alice", confirm=True), "committed")
    committed = r["lovelace"]

    # 4. Observability.
    r = check("head_status", observe.head_status(), "ok")
    assert r["head_state"] == "Open" and r["total_lovelace"] == committed, r
    check("head_utxos", observe.head_utxos(), "ok")
    check("l1_funds", observe.l1_funds("alice"), "ok")
    r = check("protocol_parameters", observe.protocol_parameters(), "ok")
    assert r["summary"].get("txFeeFixed") == 0, r["summary"]
    check("pending_deposits", observe.pending_deposits(), "ok")
    r = check("recent_events", observe.recent_events(limit=10), "ok")
    assert r["count"] > 0, r

    # 5. Two payments: one to keep in the head, one to decommit.
    check("send_tx to bob", transact.send_tx("alice", "bob", 2_000_000, confirm=True),
          "confirmed")
    check("send_tx to carol", transact.send_tx("alice", "carol", 3_000_000, confirm=True),
          "confirmed")

    utxos = observe.head_utxos()["by_address"]
    import tx_builder
    carol_addr = tx_builder.party_address("carol")
    carol_refs = [u["ref"] for u in utxos.get(carol_addr, [])]
    assert carol_refs, f"carol should hold a head UTXO: {utxos.keys()}"

    # 6. Decommit carol's UTXO back to L1 while the head stays open.
    l1_before = observe.l1_funds("carol")["total_lovelace"]
    check("decommit gate", lifecycle.decommit(carol_refs[0]), "requires_confirmation")
    check("decommit", lifecycle.decommit(carol_refs[0], confirm=True), "decommitted")
    for _ in range(30):
        if observe.l1_funds("carol")["total_lovelace"] == l1_before + 3_000_000:
            break
        time.sleep(3)
    else:
        raise AssertionError("carol's decommitted 3 ADA did not appear on L1")
    print("PASS decommit landed on L1 (+3,000,000 lovelace for carol)")
    assert observe.head_status()["head_state"] == "Open", "head must stay open"

    # 7. Close, then drain — via PARTIAL fanout when the node supports it.
    # PartialFanout was added after the 2.3.0 release (hydra PR #2750); the
    # pinned demo image rejects it. The tool must fail with a clear version
    # message on 2.3.0, and drive the real flow on newer nodes.
    check("close_head", lifecycle.close_head(confirm=True), "closed")

    utxos = observe.head_utxos()["by_address"]
    bob_addr = tx_builder.party_address("bob")
    bob_refs = [u["ref"] for u in utxos.get(bob_addr, [])]
    assert bob_refs, "bob should hold a head UTXO to partially fan out"

    r = lifecycle.partial_fanout(bob_refs, confirm=True)
    if r["status"] == "partially_fanned_out":
        remaining = r.get("remaining") or {}
        print(f"PASS partial_fanout (bob): {r['status']} "
              f"({len(remaining)} UTXO(s) remaining)")
        rest = list(remaining)
        assert rest, "alice's change should still be in the head"
        check("partial_fanout (rest)", lifecycle.partial_fanout(rest, confirm=True),
              "partially_fanned_out")
        from hydra_client import get_client
        get_client(1).wait_for({"HeadIsFinalized"}, timeout=180.0)
        print("PASS head finalized after partial draining")
    elif r["status"] == "error" and "does not support PartialFanout" in r["error"]:
        print("PASS partial_fanout: correctly reports the 2.3.0 version gap")
        check("fanout (fallback)", lifecycle.fanout(confirm=True), "finalized")
    else:
        raise AssertionError(("partial_fanout", r))

    for _ in range(20):
        if head_tag() in ("Idle", "Final"):
            break
        time.sleep(3)
    print(f"PASS head state settled: {head_tag()}")

    # 9. Diagnosis tools against the real system.
    r = check("node_logs", diagnose.node_logs(pattern="HeadIsFinalized|Fanout",
                                              since="10m", limit=5), "ok")
    assert r["matched"] > 0, "expected fanout-related log lines"
    r = check("explain_error", diagnose.explain_error("H39"), "ok")
    assert r["constructor"] == "FanoutUTxOHashMismatch", r

    print("\nFull TUI-parity lifecycle passed against the live devnet")


if __name__ == "__main__":
    main()
