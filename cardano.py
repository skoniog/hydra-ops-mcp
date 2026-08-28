"""L1 helpers, provider-backed.

v1 shelled into the devnet container directly; v2 routes everything through
the configured L1 provider (docker / cli / blockfrost) and keeps this module
as the stable API the tools import. Address derivation is done locally with
PyCardano from verification-key files, so it works identically under every
provider.
"""

from pathlib import Path

from pycardano import Address, Network, PaymentVerificationKey

import config
from providers import get_provider


class CardanoError(Exception):
    pass


_pyc_network = Network.MAINNET if config.IS_MAINNET else Network.TESTNET


def address_for_vk_file(vk_path: str) -> str:
    vk = PaymentVerificationKey.load(str(vk_path))
    return str(Address(payment_part=vk.hash(), network=_pyc_network))


def _party(party: str) -> dict:
    parties = config.reload_parties()
    if party not in parties:
        raise CardanoError(
            f"unknown party {party!r}; known: {sorted(parties) or '(none — run generate_keys)'}")
    return parties[party]


def party_address(party: str, wallet: str = "funds") -> str:
    """A party's L1 address. wallet is 'funds' (committable) or 'fuel'
    (the node's internal wallet paying protocol fees)."""
    info = _party(party)
    vk = info.get(f"{wallet}_vk")
    if not vk or not Path(vk).exists():
        raise CardanoError(f"no {wallet} verification key on disk for {party}")
    return address_for_vk_file(vk)


def l1_utxos(party: str, wallet: str = "funds") -> dict:
    return get_provider().address_utxos(party_address(party, wallet))


def chain_tip() -> dict:
    return get_provider().tip()


def protocol_parameters() -> dict:
    return get_provider().protocol_parameters()


def sign_and_submit(draft_envelope: dict, party: str, wallet: str = "funds") -> str:
    """Sign a draft tx with the party's key and submit to L1; returns tx id."""
    info = _party(party)
    sk = info.get(f"{wallet}_sk")
    if not sk:
        raise CardanoError(f"we do not hold {party}'s {wallet} signing key")
    provider = get_provider()
    signed = provider.sign_tx(draft_envelope, sk)
    return provider.submit_tx(signed)
