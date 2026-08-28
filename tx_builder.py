"""Builds and signs in-head Cardano transactions (PyCardano).

v2 change: fees are computed from the head's live protocol parameters
instead of hardcoded 0. Heads this server provisions still zero their fees
(that is safe to zero, unlike UTxO-shaping parameters), so the computed fee
is normally 0 — but a head configured with nonzero fees now gets a correct
transaction instead of a rejected one.
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

import config


class TxBuildError(Exception):
    pass


_pyc_network = Network.MAINNET if config.IS_MAINNET else Network.TESTNET


def _party(party: str) -> dict:
    parties = config.reload_parties()
    if party not in parties:
        raise TxBuildError(f"unknown party {party!r}")
    return parties[party]


def load_signing_key(party: str) -> PaymentSigningKey:
    sk = _party(party).get("funds_sk")
    if not sk or not Path(sk).exists():
        raise TxBuildError(f"we do not hold {party}'s funds signing key")
    return PaymentSigningKey.load(sk)


def party_address(party: str) -> str:
    vk = _party(party).get("funds_vk")
    if not vk or not Path(vk).exists():
        raise TxBuildError(f"no funds verification key on disk for {party}")
    pvk = PaymentVerificationKey.load(vk)
    return str(Address(payment_part=pvk.hash(), network=_pyc_network))


def address_to_party(address: str) -> str | None:
    for party, info in config.reload_parties().items():
        vk = info.get("funds_vk")
        if vk and Path(vk).exists() and party_address(party) == address:
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


def fee_for(params: dict | None, tx_size_bytes: int) -> int:
    """Linear fee from protocol parameters; 0 when params are absent/zeroed."""
    if not params:
        return 0
    return int(params.get("txFeeFixed", 0)) + \
        int(params.get("txFeePerByte", 0)) * tx_size_bytes


def _sign(body: TransactionBody, signing_key: PaymentSigningKey) -> tuple:
    vk = PaymentVerificationKey.from_signing_key(signing_key)
    witness = VerificationKeyWitness(vk, signing_key.sign(body.hash()))
    tx = Transaction(body, TransactionWitnessSet(vkey_witnesses=[witness]))
    envelope = {"type": "Tx ConwayEra", "description": "", "cborHex": tx.to_cbor_hex()}
    return envelope, str(tx.id)


def build_transfer(utxos: dict, sender: str, receiver_address: str,
                   amount_lovelace: int, head_params: dict | None = None) -> tuple:
    """Build+sign a transfer inside the head.

    Fee comes from head_params (the head's own ledger parameters). The fee is
    paid out of change; the receiver gets exactly amount_lovelace.
    """
    sender_address = party_address(sender)
    held = lovelace_at(utxos, sender_address)
    if not held:
        raise TxBuildError(f"no spendable UTXO at {sender_address} ({sender})")

    def _build(fee: int):
        inputs, gathered = [], 0
        need = amount_lovelace + fee
        for ref, lovelace in held:
            inputs.append(_parse_ref(ref))
            gathered += lovelace
            if gathered >= need:
                break
        if gathered < need:
            raise TxBuildError(
                f"insufficient funds: have {gathered}, need {need} "
                f"(amount {amount_lovelace} + fee {fee})")
        outputs = [TransactionOutput.from_primitive([receiver_address, amount_lovelace])]
        change = gathered - need
        if change > 0:
            outputs.append(TransactionOutput.from_primitive([sender_address, change]))
        return TransactionBody(inputs=inputs, outputs=outputs, fee=fee)

    sk = load_signing_key(sender)
    # First pass at fee 0 to measure the size, then rebuild with the real fee.
    body = _build(0)
    envelope, _ = _sign(body, sk)
    fee = fee_for(head_params, len(envelope["cborHex"]) // 2)
    if fee > 0:
        body = _build(fee)
    return _sign(body, sk)


def build_decommit(utxos: dict, utxo_ref: str) -> tuple:
    """Build+sign the decommit tx for one head UTXO: consume it, send its
    full value back to its owner's own address (decommit txs pay no fee —
    the L1 decrement is fueled by the node)."""
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


def _cbor_skip(data: bytes, offset: int) -> int:
    """Return the offset just past the CBOR item starting at `offset`."""
    initial = data[offset]
    major, info = initial >> 5, initial & 0x1F
    offset += 1

    if info < 24:
        arg = info
    elif info == 24:
        arg = data[offset]; offset += 1
    elif info == 25:
        arg = int.from_bytes(data[offset:offset + 2]); offset += 2
    elif info == 26:
        arg = int.from_bytes(data[offset:offset + 4]); offset += 4
    elif info == 27:
        arg = int.from_bytes(data[offset:offset + 8]); offset += 8
    elif info == 31:
        arg = None  # indefinite length
    else:
        raise TxBuildError(f"malformed CBOR at {offset - 1}")

    if major in (0, 1, 7):        # ints / simple / floats: no payload beyond arg
        return offset
    if major in (2, 3):           # byte/text string
        if arg is None:           # indefinite: chunks until break
            while data[offset] != 0xFF:
                offset = _cbor_skip(data, offset)
            return offset + 1
        return offset + arg
    if major == 4:                # array
        if arg is None:
            while data[offset] != 0xFF:
                offset = _cbor_skip(data, offset)
            return offset + 1
        for _ in range(arg):
            offset = _cbor_skip(data, offset)
        return offset
    if major == 5:                # map
        if arg is None:
            while data[offset] != 0xFF:
                offset = _cbor_skip(data, offset)
                offset = _cbor_skip(data, offset)
            return offset + 1
        for _ in range(arg * 2):
            offset = _cbor_skip(data, offset)
        return offset
    if major == 6:                # tag
        return _cbor_skip(data, offset)
    raise TxBuildError("unreachable CBOR major type")


def sign_envelope(draft_envelope: dict, signing_key_path: str) -> dict:
    """Append our vkey witness to a draft TextEnvelope tx WITHOUT
    re-serializing anything the ledger hashes.

    Neither PyCardano nor cbor2 round-trips node-built drafts byte-exactly,
    and a re-serialized body means the signature is over the wrong hash
    (InvalidWitnessesUTXOW). So: locate the exact byte spans of the tx's
    four parts with a CBOR scanner, sign blake2b-256 of the body span as-is,
    re-encode only the witness set (its encoding is not committed to
    anywhere for plain-payment deposits), and stitch the original bytes back
    together around it.
    """
    import cbor2
    import hashlib

    sk = PaymentSigningKey.load(signing_key_path)
    vk = PaymentVerificationKey.from_signing_key(sk)

    raw = bytes.fromhex(draft_envelope["cborHex"])
    if raw[0] != 0x84:
        raise TxBuildError("expected a 4-element transaction array")
    body_start = 1
    body_end = _cbor_skip(raw, body_start)
    ws_end = _cbor_skip(raw, body_end)
    valid_end = _cbor_skip(raw, ws_end)
    aux_end = _cbor_skip(raw, valid_end)
    if aux_end != len(raw):
        raise TxBuildError("trailing bytes after transaction")

    body_bytes = raw[body_start:body_end]
    body_hash = hashlib.blake2b(body_bytes, digest_size=32).digest()
    witness = [vk.to_primitive(), sk.sign(body_hash)]

    ws = cbor2.loads(raw[body_end:ws_end])
    if not isinstance(ws, dict):
        ws = {}
    existing = ws.get(0)
    if existing is None:
        ws[0] = [witness]
    elif isinstance(existing, cbor2.CBORTag):  # Conway set tag (258)
        ws[0] = cbor2.CBORTag(existing.tag, list(existing.value) + [witness])
    else:
        ws[0] = list(existing) + [witness]

    stitched = (raw[:body_end] + cbor2.dumps(ws) + raw[ws_end:])
    return {
        "type": draft_envelope.get("type", "Tx ConwayEra"),
        "description": "",
        "cborHex": stitched.hex(),
    }


def min_output_lovelace(l1_params: dict | None) -> int:
    """The floor below which a head output can never be fanned out to L1.

    Derived from the L1's utxoCostPerByte (approx: cost per byte × ~160 bytes
    for a simple output), floored at the configured fallback. On the devnet
    the live value is zeroed, so the fallback carries the guard.
    """
    fallback = config.MIN_OUTPUT_LOVELACE
    if not l1_params:
        return fallback
    per_byte = int(l1_params.get("utxoCostPerByte", 0) or 0)
    return max(fallback, per_byte * 160)
