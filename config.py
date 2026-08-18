"""Configuration for hydra-ops-mcp.

An operator-facing MCP server for Hydra heads. Targets the local demo devnet
by default; every URL/path here is overridable via environment variables so
the same server can point at other nodes.
"""

import os
from pathlib import Path

# The three demo hydra-nodes (alice, bob, carol). Tools take `node: int`
# and resolve it against this table.
NODES = {
    1: {"ws": "ws://127.0.0.1:4001", "http": "http://127.0.0.1:4001", "name": "alice"},
    2: {"ws": "ws://127.0.0.1:4002", "http": "http://127.0.0.1:4002", "name": "bob"},
    3: {"ws": "ws://127.0.0.1:4003", "http": "http://127.0.0.1:4003", "name": "carol"},
}

# Where the hydra demo devnet lives (docker compose project + credentials).
DEMO_DIR = Path(os.environ.get("HYDRA_DEMO_DIR", "/home/dev/claudecode/hydra/demo"))

# The local hydra source checkout, used by explain_error to decode on-chain
# error codes from the Plutus source.
HYDRA_REPO = Path(os.environ.get("HYDRA_REPO", "/home/dev/claudecode/hydra"))

NETWORK_MAGIC = 42

# Operator signing keys, per party, as paths inside the cardano-node container
# (credentials are mounted at /devnet). The -funds keys hold spendable ADA.
CONTAINER_CREDENTIALS = "/devnet/credentials"
FUNDS_KEYS = {
    "alice": {"sk": f"{CONTAINER_CREDENTIALS}/alice-funds.sk",
              "vk": f"{CONTAINER_CREDENTIALS}/alice-funds.vk"},
    "bob": {"sk": f"{CONTAINER_CREDENTIALS}/bob-funds.sk",
            "vk": f"{CONTAINER_CREDENTIALS}/bob-funds.vk"},
    "carol": {"sk": f"{CONTAINER_CREDENTIALS}/carol-funds.sk",
              "vk": f"{CONTAINER_CREDENTIALS}/carol-funds.vk"},
}

# Host-side copies of the same keys (for PyCardano signing of in-head txs).
HOST_CREDENTIALS = DEMO_DIR / "devnet" / "credentials"

# Below the L1 min-UTXO (~0.857 ADA with default params), an in-head output
# can never be fanned out — it would wedge the head. Enforce 1 ADA.
MIN_OUTPUT_LOVELACE = 1_000_000
