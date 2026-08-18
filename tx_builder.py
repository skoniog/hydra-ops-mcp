"""Builds and signs in-head Cardano transactions (PyCardano).

Fees are zero inside the demo head (its protocol parameters zero them); on a
real network this needs actual fee estimation and coin selection.
"""

from pathlib import Path

from pycardano import (
    Address,
    Network,
    PaymentSigningKey,
    PaymentVerificationKey,
    Transaction,
    TransactionBody,
    TransactionInput,
    TransactionOutput,
    TransactionWitnessSet,
    VerificationKeyWitness,
)

from config import FUNDS_KEYS, HOST_CREDENTIALS


class TxBuildError(Exception):
    pass


def load_signing_key(party: str) -> PaymentSigningKey:
    name = Path(FUNDS_KEYS[party]["sk"]).name
    return PaymentSigningKey.load(str(HOST_CREDENTIALS / name))


def party_address(party: str) -> str:
    name = Path(FUNDS_KEYS[party]["vk"]).name
    vk = PaymentVerificationKey.load(str(HOST_CREDENTIALS / name))
    return str(Address(payment_part=vk.hash(), network=Network.TESTNET))


def address_to_party(address: str) -> str | None:
    for party in FUNDS_KEYS:
        if party_address(party) == address:
            return party
    return None


def _parse_ref(ref: str) -> TransactionInput:
    tx_id, ix = ref.split("#")
    return TransactionInput.from_primitive([tx_id, int(ix)])


def lovelace_at(utxos: dict, address: str) -> list:
    """[(ref, lovelace)] of pure-ADA UTXOs at `address`, largest first."""
    held = [
        (ref, out["value"]["lovelace"])
        for ref, out in utxos.items()
        if out.get("address") == address and set(out.get("value", {})) == {"lovelace"}
    ]
    return sorted(held, key=lambda t: -t[1])


def _sign(body: TransactionBody, signing_key: PaymentSigningKey) -> tuple:
    vk = PaymentVerificationKey.from_signing_key(signing_key)
    witness = VerificationKeyWitness(vk, signing_key.sign(body.hash()))
    tx = Transaction(body, TransactionWitnessSet(vkey_witnesses=[witness]))
    envelope = {"type": "Tx ConwayEra", "description": "", "cborHex": tx.to_cbor_hex()}
    return envelope, str(tx.id)


def build_transfer(utxos: dict, sender: str, receiver_address: str,
                   amount_lovelace: int) -> tuple:
    """Build+sign an in-head transfer from `sender` (a party name)."""
    sender_address = party_address(sender)
    held = lovelace_at(utxos, sender_address)
    if not held:
        raise TxBuildError(f"no spendable UTXO at {sender_address} ({sender})")

    inputs, gathered = [], 0
    for ref, lovelace in held:
        inputs.append(_parse_ref(ref))
        gathered += lovelace
        if gathered >= amount_lovelace:
            break
    if gathered < amount_lovelace:
        raise TxBuildError(f"insufficient funds: have {gathered}, need {amount_lovelace}")

    outputs = [TransactionOutput.from_primitive([receiver_address, amount_lovelace])]
    change = gathered - amount_lovelace
    if change > 0:
        outputs.append(TransactionOutput.from_primitive([sender_address, change]))

    body = TransactionBody(inputs=inputs, outputs=outputs, fee=0)
    return _sign(body, load_signing_key(sender))


def build_decommit(utxos: dict, utxo_ref: str) -> tuple:
    """Build+sign the decommit tx for one head UTXO.

    Mirrors the TUI: consume the chosen UTXO and send its full value back to
    its owner's own address. The owner is derived from the UTXO's address,
    which must belong to a party whose signing key we hold.
    """
    if utxo_ref not in utxos:
        raise TxBuildError(f"UTXO {utxo_ref} not found in the head")
    out = utxos[utxo_ref]
    owner = address_to_party(out.get("address", ""))
    if owner is None:
        raise TxBuildError(f"UTXO {utxo_ref} belongs to an address with no known signing key")
    if set(out.get("value", {})) != {"lovelace"}:
        raise TxBuildError("only pure-ADA UTXOs are supported for decommit here")

    lovelace = out["value"]["lovelace"]
    body = TransactionBody(
        inputs=[_parse_ref(utxo_ref)],
        outputs=[TransactionOutput.from_primitive([out["address"], lovelace])],
        fee=0,
    )
    envelope, tx_id = _sign(body, load_signing_key(owner))
    return envelope, tx_id, owner, lovelace
