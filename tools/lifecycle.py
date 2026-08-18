"""Head lifecycle tools — every one gated on confirm=True.

Without confirm=True these describe exactly what they would do and change
nothing. That gate is what makes it safe to expose the full lifecycle to a
model: the human reads the plan before it runs.
"""

import time

import cardano
import tx_builder
from hydra_client import get_client
from tools.types import err, needs_confirmation, ok


def init_head(node: int = 1, confirm: bool = False) -> dict:
    """Initialize a new head (opens immediately on hydra-node 2.3.0)."""
    if not confirm:
        return needs_confirmation(
            f"send Init on node {node}, starting a new head for ALL configured "
            f"parties (alice, bob, carol)", node=node)
    try:
        client = get_client(node)
        status = client.get_head().get("tag")
        if status != "Idle":
            return err(f"head is {status}, not Idle — cannot init", node=node)
        event = client.init_head()
    except Exception as e:
        return err(str(e), node=node)
    return ok("initialized", node=node, event=event.get("tag"),
              head_state=get_client(node).get_head().get("tag"))


def commit_funds(party: str = "alice", node: int = 1, utxo_ref: str = "",
                 confirm: bool = False) -> dict:
    """Deposit one of the party's L1 UTXOs into the open head.

    Deposits exactly ONE UTXO (the largest, unless utxo_ref picks one):
    multi-UTXO deposits wedge the eventual fanout with H39 on 2.3.0.
    """
    try:
        utxos = cardano.l1_utxos(party)
    except Exception as e:
        return err(str(e), party=party)
    if not utxos:
        return err(f"{party} has no L1 UTXOs to commit", party=party)

    if utxo_ref:
        if utxo_ref not in utxos:
            return err(f"{utxo_ref} is not an L1 UTXO of {party}", party=party)
        ref, out = utxo_ref, utxos[utxo_ref]
    else:
        ref, out = max(utxos.items(), key=lambda kv: kv[1]["value"]["lovelace"])

    lovelace = out["value"]["lovelace"]
    if not confirm:
        return needs_confirmation(
            f"deposit {party}'s UTXO {ref} ({lovelace:,} lovelace) into the head "
            f"via node {node} (sign with {party}-funds.sk, submit to L1, wait for "
            f"absorption)", party=party, utxo_ref=ref, lovelace=lovelace)

    try:
        client = get_client(node)
        draft = client.draft_commit({ref: {"address": out["address"], "value": out["value"]}})
        cardano.sign_and_submit(draft, party, f"commit-{party}")
        # Wait for the deposit to be absorbed (deposit period ~10s on the demo).
        before = len(client.get_utxos())
        for _ in range(60):
            if len(client.get_utxos()) > before:
                break
            time.sleep(3)
        else:
            return err("deposit submitted but not absorbed into the head in time "
                       "(check pending_deposits; recover_deposit can pull it back)",
                       party=party, utxo_ref=ref)
    except Exception as e:
        return err(str(e), party=party, utxo_ref=ref)
    return ok("committed", party=party, utxo_ref=ref, lovelace=lovelace,
              head_utxo_count=len(client.get_utxos()))


def decommit(utxo_ref: str, node: int = 1, confirm: bool = False) -> dict:
    """Withdraw one head UTXO back to the L1 without closing the head."""
    try:
        utxos = get_client(node).get_utxos()
        envelope, tx_id, owner, lovelace = tx_builder.build_decommit(utxos, utxo_ref)
    except Exception as e:
        return err(str(e), utxo_ref=utxo_ref)
    if not confirm:
        return needs_confirmation(
            f"decommit UTXO {utxo_ref} ({lovelace:,} lovelace, owner {owner}) out "
            f"of the head to {owner}'s L1 address", utxo_ref=utxo_ref,
            owner=owner, lovelace=lovelace)
    try:
        event = get_client(node).decommit(envelope)
    except Exception as e:
        return err(str(e), utxo_ref=utxo_ref)
    return ok("decommitted", utxo_ref=utxo_ref, owner=owner, lovelace=lovelace,
              tx_id=tx_id, event=event.get("tag"),
              distributed=event.get("distributedUTxO"))


def close_head(node: int = 1, confirm: bool = False) -> dict:
    """Close the head — for ALL participants. Irreversible."""
    if not confirm:
        return needs_confirmation(
            f"CLOSE the head via node {node}. This ends the head for every "
            f"participant; after the contestation deadline it must be fanned out",
            node=node)
    try:
        event = get_client(node).close_head()
    except Exception as e:
        return err(str(e), node=node)
    return ok("closed", node=node,
              contestation_deadline=event.get("contestationDeadline"))


def fanout(node: int = 1, confirm: bool = False) -> dict:
    """Distribute the closed head's entire UTXO set back to the L1."""
    if not confirm:
        return needs_confirmation(
            f"fan out the closed head via node {node}, distributing all head "
            f"UTXOs to the L1 and finalizing the head", node=node)
    try:
        event = get_client(node).fanout()
    except Exception as e:
        return err(str(e), node=node)
    return ok("finalized", node=node, event=event.get("tag"),
              finalized_utxo_count=len(event.get("finalizedUTxO") or {}))


def partial_fanout(utxo_refs: list, node: int = 1, confirm: bool = False) -> dict:
    """Fan out only the chosen UTXOs; the head keeps waiting for the rest.

    Once partial fanout starts, plain fanout is no longer accepted — keep
    selecting until the head is drained, at which point the node finalizes
    on its own.
    """
    try:
        utxos = get_client(node).get_utxos()
    except Exception as e:
        return err(str(e), node=node)
    missing = [r for r in utxo_refs if r not in utxos]
    if missing:
        return err(f"not in the head UTXO set: {', '.join(missing)}", node=node)
    selected = {r: utxos[r] for r in utxo_refs}
    total = sum(o.get("value", {}).get("lovelace", 0) for o in selected.values())
    if not confirm:
        return needs_confirmation(
            f"partially fan out {len(selected)} UTXO(s) ({total:,} lovelace) via "
            f"node {node}; after this, only further partial fanouts can drain the "
            f"rest", utxo_refs=utxo_refs, total_lovelace=total)
    try:
        event = get_client(node).partial_fanout(selected)
    except Exception as e:
        message = str(e)
        if "expected tag field" in message and "PartialFanout" in message:
            message = ("this hydra-node does not support PartialFanout — the "
                       "command was added after the 2.3.0 release (hydra commit "
                       "a271cced2, PR #2750). Use fanout, or run a node built "
                       "from master.")
        return err(message, node=node)
    return ok("partially_fanned_out", node=node, event=event.get("tag"),
              distributed=event.get("distributedUTxO"),
              remaining=event.get("remainingUTxO"),
              fanout_mode=event.get("fanoutMode"))


def recover_deposit(tx_id: str, node: int = 1, confirm: bool = False) -> dict:
    """Recover a stuck (unabsorbed) deposit back to the L1."""
    if not confirm:
        return needs_confirmation(
            f"recover deposit {tx_id} on node {node}, returning its funds to the "
            f"L1 instead of the head", tx_id=tx_id)
    try:
        result = get_client(node).recover_deposit(tx_id)
    except Exception as e:
        return err(str(e), tx_id=tx_id)
    return ok("recovery_requested", tx_id=tx_id, node=node, result=result)
