"""Read-only observability tools — the TUI's tabs, as queries."""

import cardano
from hydra_client import get_client
from tools.types import err, ok


def head_status(node: int = 1) -> dict:
    """Head state as the node reports it, plus what the WS client has seen."""
    try:
        client = get_client(node)
        head = client.get_head()
        utxos = client.get_utxos()
    except Exception as e:
        return err(str(e), node=node)
    contents = head.get("contents") or {}
    snapshot = ((contents.get("confirmedSnapshot") or {}).get("snapshot") or {})
    return ok(
        "ok",
        node=node,
        head_state=head.get("tag", "unknown"),
        ws_status=client.get_head_status(),
        utxo_count=len(utxos),
        total_lovelace=sum(o.get("value", {}).get("lovelace", 0) for o in utxos.values()),
        snapshot_number=snapshot.get("number"),
        version=contents.get("version"),
        contestation_deadline=contents.get("contestationDeadline"),
    )


def head_utxos(node: int = 1) -> dict:
    """The UTXO set inside the head, grouped by address."""
    try:
        utxos = get_client(node).get_utxos()
    except Exception as e:
        return err(str(e), node=node)
    by_address = {}
    for ref, out in utxos.items():
        addr = out.get("address", "?")
        by_address.setdefault(addr, []).append(
            {"ref": ref, "value": out.get("value", {})}
        )
    return ok("ok", node=node, utxo_count=len(utxos), by_address=by_address)


def l1_funds(party: str = "alice") -> dict:
    """The party's wallet on the L1 (the TUI's funds tab)."""
    try:
        utxos = cardano.l1_utxos(party)
        address = cardano.party_address(party)
    except Exception as e:
        return err(str(e), party=party)
    return ok(
        "ok",
        party=party,
        address=address,
        utxo_count=len(utxos),
        total_lovelace=sum(o["value"]["lovelace"] for o in utxos.values()),
        utxos={ref: o["value"] for ref, o in utxos.items()},
    )


def protocol_parameters(node: int = 1) -> dict:
    """The ledger parameters the head runs with (fee settings, min-UTXO, ...)."""
    try:
        params = get_client(node).get_protocol_parameters()
    except Exception as e:
        return err(str(e), node=node)
    keys = ("txFeeFixed", "txFeePerByte", "utxoCostPerByte", "maxTxSize",
            "maxValueSize", "collateralPercentage")
    return ok("ok", node=node,
              summary={k: params.get(k) for k in keys if k in params},
              parameters=params)


def pending_deposits(node: int = 1) -> dict:
    """Deposits awaiting absorption into the head (recover candidates)."""
    try:
        deposits = get_client(node).get_pending_deposits()
    except Exception as e:
        return err(str(e), node=node)
    return ok("ok", node=node, count=len(deposits), deposits=deposits)


def recent_events(node: int = 1, tag: str = None, limit: int = 25) -> dict:
    """Recent server outputs seen on this connection (the event-history tab).

    Only events since this client connected — the connection skips history.
    """
    try:
        events = get_client(node).recent_events(tag=tag, limit=limit)
    except Exception as e:
        return err(str(e), node=node)
    slim = []
    for e in events:
        entry = {"tag": e.get("tag")}
        for key in ("headId", "seq", "timestamp"):
            if key in e:
                entry[key] = e[key]
        slim.append(entry)
    return ok("ok", node=node, count=len(slim), events=slim)
