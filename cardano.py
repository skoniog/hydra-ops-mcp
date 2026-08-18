"""L1 operations via cardano-cli inside the devnet's cardano-node container.

Address derivation, UTXO queries, chain tip, and the sign-and-submit step of
the deposit (commit) flow. The container is where the credentials and the
node socket already live, so L1 work happens there rather than on the host.
"""

import json
import subprocess

from config import DEMO_DIR, FUNDS_KEYS, NETWORK_MAGIC


class CardanoError(Exception):
    pass


def ccli(*args) -> str:
    cmd = ["docker", "compose", "exec", "-T", "cardano-node", "cardano-cli", *args]
    r = subprocess.run(cmd, cwd=DEMO_DIR, capture_output=True, text=True)
    if r.returncode != 0:
        raise CardanoError(f"cardano-cli failed: {r.stderr[:600]}")
    return r.stdout


def party_address(party: str) -> str:
    if party not in FUNDS_KEYS:
        raise CardanoError(f"unknown party {party!r}; expected one of {sorted(FUNDS_KEYS)}")
    return ccli(
        "conway", "address", "build",
        "--payment-verification-key-file", FUNDS_KEYS[party]["vk"],
        "--testnet-magic", str(NETWORK_MAGIC),
    ).strip()


def l1_utxos(party: str) -> dict:
    addr = party_address(party)
    out = ccli(
        "conway", "query", "utxo", "--address", addr,
        "--testnet-magic", str(NETWORK_MAGIC),
        "--socket-path", "/devnet/node.socket",
        "--out-file", "/dev/stdout",
    )
    return json.loads(out)


def chain_tip() -> dict:
    out = ccli(
        "conway", "query", "tip",
        "--testnet-magic", str(NETWORK_MAGIC),
        "--socket-path", "/devnet/node.socket",
    )
    return json.loads(out)


def sign_and_submit(draft_tx: dict, party: str, label: str) -> None:
    """Write a draft tx into the devnet dir, sign with the party's funds key,
    and submit it to the L1 — the second half of the deposit (commit) flow."""
    tx_path = DEMO_DIR / "devnet" / f"ops-{label}.json"
    tx_path.write_text(json.dumps(draft_tx))
    ccli(
        "conway", "transaction", "sign",
        "--tx-file", f"/devnet/ops-{label}.json",
        "--signing-key-file", FUNDS_KEYS[party]["sk"],
        "--out-file", f"/devnet/ops-{label}.signed",
        "--testnet-magic", str(NETWORK_MAGIC),
    )
    ccli(
        "conway", "transaction", "submit",
        "--tx-file", f"/devnet/ops-{label}.signed",
        "--testnet-magic", str(NETWORK_MAGIC),
        "--socket-path", "/devnet/node.socket",
    )
