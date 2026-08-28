"""In-head transactions — gated on confirm=True."""

import tx_builder
from hydra_client import get_client
from tools.types import err, needs_confirmation, ok


def _head_params(node: int) -> dict:
    try:
        return get_client(node).get_protocol_parameters() or {}
    except Exception:
        return {}


def send_tx(sender: str, receiver: str, amount_lovelace: int,
            node: int = 1, confirm: bool = False) -> dict:
    """Send ADA inside the head.

    `sender` is a party name whose funds key we hold; `receiver` is a party
    name or a bech32 address. Fees come from the head's own ledger parameters
    (normally zero). Outputs below the L1 min-UTXO floor are refused — the
    head would accept them, but fanout to L1 would then fail and wedge the
    head.
    """
    params = _head_params(node)
    floor = tx_builder.min_output_lovelace(params)
    if amount_lovelace < floor:
        return err(f"amount must be at least {floor} lovelace: sub-min-UTXO "
                   f"head outputs cannot be fanned out to L1")
    try:
        receiver_address = (tx_builder.party_address(receiver)
                            if not receiver.startswith("addr") else receiver)
        utxos = get_client(node).get_utxos()
        envelope, tx_id = tx_builder.build_transfer(
            utxos, sender, receiver_address, amount_lovelace, head_params=params)
    except Exception as e:
        return err(str(e), sender=sender, receiver=receiver)
    fee = tx_builder.fee_for(params, len(envelope["cborHex"]) // 2)
    if not confirm:
        return needs_confirmation(
            f"send {amount_lovelace:,} lovelace from {sender} to {receiver} "
            f"inside the head (fee {fee:,}, tx {tx_id[:16]}…)",
            sender=sender, receiver=receiver, amount_lovelace=amount_lovelace,
            fee_lovelace=fee, tx_id=tx_id)
    try:
        # Rebuild against the latest UTXO set in case it moved since the preview.
        utxos = get_client(node).get_utxos()
        envelope, tx_id = tx_builder.build_transfer(
            utxos, sender, receiver_address, amount_lovelace, head_params=params)
        result = get_client(node).submit_tx(envelope, tx_id)
    except Exception as e:
        return err(str(e), sender=sender, receiver=receiver)
    return ok("confirmed", tx_id=result["tx_id"], sender=sender,
              receiver=receiver, amount_lovelace=amount_lovelace)
