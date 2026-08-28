"""L1 access via the Blockfrost REST API — no local cardano-node required.

The network is implied by the project id's prefix (preview.../preprod.../
mainnet...), matching how hydra-node's --blockfrost flag behaves.

Signing happens locally with PyCardano (Blockfrost never sees a key);
submission posts raw CBOR to /tx/submit.

Protocol parameters come back in Blockfrost's own field names; they are
mapped to the cardano-cli JSON shape so every consumer sees one format.
The mapping covers the fields this server actually reads — documented
below — and keeps the Blockfrost original under "_blockfrost_raw".
"""

import json
import time
from pathlib import Path

import httpx

from providers.base import L1Provider, ProviderError

_NETWORK_PREFIXES = ("preview", "preprod", "mainnet")


class BlockfrostProvider(L1Provider):
    name = "blockfrost"

    def __init__(self, project_file: str):
        path = Path(project_file)
        if not path.exists():
            raise ProviderError(f"blockfrost project file not found: {project_file}")
        self.project_id = path.read_text().strip()
        prefix = next((p for p in _NETWORK_PREFIXES if self.project_id.startswith(p)), None)
        if prefix is None:
            raise ProviderError(
                "blockfrost project id must start with preview/preprod/mainnet")
        self.network_name = prefix
        self.base = f"https://cardano-{prefix}.blockfrost.io/api/v0"

    def _get(self, path: str, params: dict = None):
        r = httpx.get(f"{self.base}{path}", params=params,
                      headers={"project_id": self.project_id}, timeout=30.0)
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            raise ProviderError("blockfrost rate limit hit; retry later")
        r.raise_for_status()
        return r.json()

    def address_utxos(self, address: str) -> dict:
        utxos, page = {}, 1
        while True:
            rows = self._get(f"/addresses/{address}/utxos",
                             params={"page": page, "count": 100})
            if not rows:  # 404 = address never seen = no UTXOs
                break
            for row in rows:
                value = {}
                for amt in row.get("amount", []):
                    unit = amt["unit"]
                    qty = int(amt["quantity"])
                    if unit == "lovelace":
                        value["lovelace"] = qty
                    else:
                        value[unit] = qty
                utxos[f"{row['tx_hash']}#{row['output_index']}"] = {
                    "address": address,
                    "value": value,
                    "datum": None,
                    "datumhash": row.get("data_hash"),
                    "inlineDatum": None,
                    "referenceScript": None,
                }
            if len(rows) < 100:
                break
            page += 1
        return utxos

    def protocol_parameters(self) -> dict:
        raw = self._get("/epochs/latest/parameters")
        if raw is None:
            raise ProviderError("blockfrost returned no protocol parameters")
        # Blockfrost -> cardano-cli field mapping for everything we consume.
        mapped = {
            "txFeePerByte": int(raw["min_fee_a"]),
            "txFeeFixed": int(raw["min_fee_b"]),
            "utxoCostPerByte": int(raw["coins_per_utxo_size"]),
            "maxTxSize": int(raw["max_tx_size"]),
            "maxValueSize": int(raw["max_val_size"]),
            "collateralPercentage": int(raw["collateral_percent"]),
            "maxCollateralInputs": int(raw["max_collateral_inputs"]),
            "maxTxExecutionUnits": {
                "memory": int(raw["max_tx_ex_mem"]),
                "steps": int(raw["max_tx_ex_steps"]),
            },
            "executionUnitPrices": {
                "priceMemory": float(raw["price_mem"]),
                "priceSteps": float(raw["price_step"]),
            },
            "_blockfrost_raw": raw,
        }
        if raw.get("cost_models_raw"):
            mapped["costModels"] = raw["cost_models_raw"]
        return mapped

    def tip(self) -> dict:
        d = self._get("/blocks/latest")
        return {"slot": d.get("slot"), "block": d.get("height"), "hash": d.get("hash")}

    def sign_tx(self, draft_envelope: dict, signing_key_path: str) -> dict:
        # Local signing that never re-serializes the draft body — see
        # tx_builder.sign_envelope for the InvalidWitnessesUTXOW trap.
        import tx_builder
        return tx_builder.sign_envelope(draft_envelope, signing_key_path)

    def submit_tx(self, tx_envelope: dict) -> str:
        cbor = bytes.fromhex(tx_envelope["cborHex"])
        r = httpx.post(f"{self.base}/tx/submit", content=cbor,
                       headers={"project_id": self.project_id,
                                "Content-Type": "application/cbor"},
                       timeout=60.0)
        if r.status_code != 200:
            raise ProviderError(f"blockfrost submit failed: {r.status_code} "
                                f"{r.text[:400]}")
        return r.json() if r.headers.get("content-type", "").startswith("application/json") \
            else r.text.strip().strip('"')

    def await_tx(self, tx_id: str, address: str = "", timeout: float = 600.0) -> bool:
        """Blockfrost can confirm by tx id directly — no address needed."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get(f"/txs/{tx_id}") is not None:
                return True
            time.sleep(10)
        return False
