"""L1 access via cardano-cli inside the devnet's cardano-node container.

The v1 path, preserved as a first-class provider so the demo devnet remains
the zero-cost regression bed.
"""

import json
import subprocess
from pathlib import Path

from providers.base import L1Provider, ProviderError

MAGIC = "42"
SOCKET = "/devnet/node.socket"


class DockerCliProvider(L1Provider):
    name = "docker"

    def __init__(self, demo_dir):
        self.demo_dir = Path(demo_dir)

    def _ccli(self, *args) -> str:
        cmd = ["docker", "compose", "exec", "-T", "cardano-node", "cardano-cli", *args]
        r = subprocess.run(cmd, cwd=self.demo_dir, capture_output=True, text=True)
        if r.returncode != 0:
            raise ProviderError(f"cardano-cli (docker) failed: {r.stderr[:600]}")
        return r.stdout

    def address_utxos(self, address: str) -> dict:
        out = self._ccli(
            "conway", "query", "utxo", "--address", address,
            "--testnet-magic", MAGIC, "--socket-path", SOCKET,
            "--out-file", "/dev/stdout",
        )
        return json.loads(out)

    def protocol_parameters(self) -> dict:
        out = self._ccli(
            "conway", "query", "protocol-parameters",
            "--testnet-magic", MAGIC, "--socket-path", SOCKET,
            "--out-file", "/dev/stdout",
        )
        return json.loads(out)

    def tip(self) -> dict:
        out = self._ccli(
            "conway", "query", "tip",
            "--testnet-magic", MAGIC, "--socket-path", SOCKET,
        )
        d = json.loads(out)
        return {"slot": d.get("slot"), "block": d.get("block"), "hash": d.get("hash")}

    def sign_tx(self, draft_envelope: dict, signing_key_path: str) -> dict:
        # The container mounts the demo dir at /devnet; keys and scratch files
        # must live under it. Accept either a host path inside demo_dir/devnet
        # or an already-container path starting with /devnet.
        skey = signing_key_path
        if not skey.startswith("/devnet"):
            host = Path(signing_key_path).resolve()
            devnet_root = (self.demo_dir / "devnet").resolve()
            try:
                skey = "/devnet/" + str(host.relative_to(devnet_root))
            except ValueError:
                raise ProviderError(
                    f"docker provider can only sign with keys under "
                    f"{devnet_root}, got {signing_key_path}")
        scratch = self.demo_dir / "devnet" / "ops-sign.json"
        scratch.write_text(json.dumps(draft_envelope))
        self._ccli(
            "conway", "transaction", "sign",
            "--tx-file", "/devnet/ops-sign.json",
            "--signing-key-file", skey,
            "--out-file", "/devnet/ops-sign.signed",
            "--testnet-magic", MAGIC,
        )
        # The container writes as root; read the file back through it too.
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "cardano-node",
             "cat", "/devnet/ops-sign.signed"],
            cwd=self.demo_dir, capture_output=True, text=True,
        )
        if out.returncode != 0:
            raise ProviderError(f"reading signed tx failed: {out.stderr[:300]}")
        return json.loads(out.stdout)

    def submit_tx(self, tx_envelope: dict) -> str:
        scratch = self.demo_dir / "devnet" / "ops-submit.json"
        scratch.write_text(json.dumps(tx_envelope))
        self._ccli(
            "conway", "transaction", "submit",
            "--tx-file", "/devnet/ops-submit.json",
            "--testnet-magic", MAGIC, "--socket-path", SOCKET,
        )
        out = self._ccli(
            "conway", "transaction", "txid", "--tx-file", "/devnet/ops-submit.json",
        )
        return out.strip()
