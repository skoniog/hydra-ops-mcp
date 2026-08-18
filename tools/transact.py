"""In-head transactions — gated on confirm=True."""

import tx_builder
from config import MIN_OUTPUT_LOVELACE
from hydra_client import get_client
from tools.types import err, needs_confirmation, ok


def send_tx(sender: str, receiver: str, amount_lovelace: int,
            node: int = 1, confirm: bool = False) -> dict:
    """Send ADA inside the head.

    `sender` is a party name whose funds key we hold (alice/bob/carol);
    `receiver` is a party name or a bech32 address. Outputs below 1 ADA are
    refused — the head would accept them, but fanout to L1 would then fail
    on min-UTXO and wedge the head.
    """
    if amount_lovelace < MIN_OUTPUT_LOVELACE:
        return err(f"amount must be at least {MIN_OUTPUT_LOVELACE} lovelace: "
                   f"sub-min-UTXO head outputs cannot be fanned out to L1")
    try:
        receiver_address = (tx_builder.party_address(receiver)
                            if not receiver.startswith("addr") else receiver)
        utxos = get_client(node).get_utxos()
        envelope, tx_id = tx_builder.build_transfer(
            utxos, sender, receiver_address, amount_lovelace)
    except Exception as e:
        return err(str(e), sender=sender, receiver=receiver)
    if not confirm:
        return needs_confirmation(
            f"send {amount_lovelace:,} lovelace from {sender} to {receiver} "
            f"inside the head (tx {tx_id[:16]}…)",
            sender=sender, receiver=receiver, amount_lovelace=amount_lovelace,
            tx_id=tx_id)
    try:
        # Rebuild against the latest UTXO set in case it moved since the preview.
        utxos = get_client(node).get_utxos()
        envelope, tx_id = tx_builder.build_transfer(
            utxos, sender, receiver_address, amount_lovelace)
        result = get_client(node).submit_tx(envelope, tx_id)
    except Exception as e:
        return err(str(e), sender=sender, receiver=receiver)
    return ok("confirmed", tx_id=result["tx_id"], sender=sender,
              receiver=receiver, amount_lovelace=amount_lovelace)
