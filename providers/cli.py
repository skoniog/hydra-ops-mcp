"""L1 access via a local cardano-cli binary and node socket."""

import json
import subprocess
import tempfile
from pathlib import Path

from networks import network
from providers.base import L1Provider, ProviderError


class CliProvider(L1Provider):
    name = "cli"

    def __init__(self, cli_path: str, node_socket: str, network_name: str):
        self.cli = cli_path
        self.socket = node_socket
        net = network(network_name)
        self.net_args = (["--mainnet"] if net["mainnet"]
                         else ["--testnet-magic", str(net["magic"])])

    def _ccli(self, *args) -> str:
        cmd = [self.cli, *args]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise ProviderError(f"cardano-cli failed: {r.stderr[:600]}")
        return r.stdout

    def address_utxos(self, address: str) -> dict:
        out = self._ccli(
            "conway", "query", "utxo", "--address", address,
            *self.net_args, "--socket-path", self.socket,
            "--out-file", "/dev/stdout",
        )
        return json.loads(out)

    def protocol_parameters(self) -> dict:
        out = self._ccli(
            "conway", "query", "protocol-parameters",
            *self.net_args, "--socket-path", self.socket,
            "--out-file", "/dev/stdout",
        )
        return json.loads(out)

    def tip(self) -> dict:
        out = self._ccli(
            "conway", "query", "tip", *self.net_args, "--socket-path", self.socket,
        )
        d = json.loads(out)
        return {"slot": d.get("slot"), "block": d.get("block"), "hash": d.get("hash")}

    def sign_tx(self, draft_envelope: dict, signing_key_path: str) -> dict:
        with tempfile.TemporaryDirectory() as td:
            draft = Path(td) / "draft.json"
            signed = Path(td) / "signed.json"
            draft.write_text(json.dumps(draft_envelope))
            self._ccli(
                "conway", "transaction", "sign",
                "--tx-file", str(draft),
                "--signing-key-file", signing_key_path,
                "--out-file", str(signed),
                *self.net_args,
            )
            return json.loads(signed.read_text())

    def submit_tx(self, tx_envelope: dict) -> str:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tx.json"
            path.write_text(json.dumps(tx_envelope))
            self._ccli(
                "conway", "transaction", "submit",
                "--tx-file", str(path),
                *self.net_args, "--socket-path", self.socket,
            )
            return self._ccli("conway", "transaction", "txid",
                              "--tx-file", str(path)).strip()
