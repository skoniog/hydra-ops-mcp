"""Configuration for hydra-ops-mcp v2.

Everything is environment-overridable. Three axes:

  HYDRA_OPS_NETWORK   devnet (default) | preview | preprod | mainnet
  HYDRA_OPS_PROVIDER  docker (default on devnet) | cli | blockfrost
  HYDRA_OPS_WORKSPACE where generated keys, configs and node state live

On the devnet everything defaults to v1 behavior (docker provider, demo
parties alice/bob/carol with keys in the demo credentials directory). On a
real network, parties and keys come from the workspace, created by the
provisioning tools (generate_keys, node_plan, start_node).
"""

import json
import os
from pathlib import Path

from networks import network

# ---------------------------------------------------------------- axes

NETWORK_NAME = os.environ.get("HYDRA_OPS_NETWORK", "devnet")
NET = network(NETWORK_NAME)
NETWORK_MAGIC = NET["magic"]
IS_MAINNET = NET["mainnet"]

PROVIDER = os.environ.get(
    "HYDRA_OPS_PROVIDER", "docker" if NETWORK_NAME == "devnet" else "blockfrost"
)

WORKSPACE = Path(os.environ.get(
    "HYDRA_OPS_WORKSPACE", str(Path.home() / ".hydra-ops" / NETWORK_NAME)
))

# ---------------------------------------------------------------- provider inputs

# blockfrost provider / node backend
BLOCKFROST_PROJECT_FILE = os.environ.get(
    "BLOCKFROST_PROJECT_FILE", str(WORKSPACE / "blockfrost-project.txt")
)

# cli provider
CARDANO_CLI = os.environ.get("CARDANO_CLI", "cardano-cli")
NODE_SOCKET = os.environ.get("CARDANO_NODE_SOCKET_PATH", "")

# docker provider (devnet)
DEMO_DIR = Path(os.environ.get("HYDRA_DEMO_DIR", "/home/dev/claudecode/hydra/demo"))

# Local hydra checkout, for decoding on-chain abort codes.
HYDRA_REPO = Path(os.environ.get("HYDRA_REPO", "/home/dev/claudecode/hydra"))

# hydra-node docker image for provisioned nodes. 2.3.0 is the latest release;
# "unstable" (master builds) adds PartialFanout but needs self-published scripts
# on real networks.
HYDRA_NODE_IMAGE = os.environ.get(
    "HYDRA_NODE_IMAGE", "ghcr.io/cardano-scaling/hydra-node:2.3.0"
)
HYDRA_NODE_VERSION = os.environ.get("HYDRA_NODE_VERSION", "2.3.0")

# ---------------------------------------------------------------- head parameters

# Contestation period (seconds). Protocol parameter: ALL participants must
# configure the same value or Init is ignored. <30s risks an unclosable head
# (Close validity expires inside one block); mainnet guidance is >= 43200.
CONTESTATION_PERIOD = int(os.environ.get(
    "HYDRA_OPS_CONTESTATION_PERIOD",
    "3" if NETWORK_NAME == "devnet" else "60" if not IS_MAINNET else "43200",
))
DEPOSIT_PERIOD = int(os.environ.get(
    "HYDRA_OPS_DEPOSIT_PERIOD",
    "10" if NETWORK_NAME == "devnet" else "600",
))

# Recommended fuel for the node's internal wallet (docs suggest ~30 ada).
FUEL_THRESHOLD_LOVELACE = 30_000_000

# ---------------------------------------------------------------- participants

def _devnet_parties() -> dict:
    creds = DEMO_DIR / "devnet" / "credentials"
    return {
        name: {
            "funds_sk": str(creds / f"{name}-funds.sk"),
            "funds_vk": str(creds / f"{name}-funds.vk"),
            "fuel_sk": str(creds / f"{name}.sk"),
            "fuel_vk": str(creds / f"{name}.vk"),
            "hydra_sk": None,  # devnet hydra keys live inside the compose setup
            "hydra_vk": None,
            "ours": True,
        }
        for name in ("alice", "bob", "carol")
    }


def _workspace_parties() -> dict:
    """Parties provisioned into the workspace by generate_keys."""
    registry = WORKSPACE / "parties.json"
    if registry.exists():
        return json.loads(registry.read_text())
    return {}


def _all_parties() -> dict:
    """Workspace-provisioned parties, plus the demo trio on devnet."""
    base = _devnet_parties() if NETWORK_NAME == "devnet" else {}
    return {**base, **_workspace_parties()}


PARTIES = _all_parties()

_DEVNET_BUILTINS = ("alice", "bob", "carol")


def save_parties(parties: dict) -> None:
    """Persist the workspace party registry (devnet's built-in demo parties
    are derived, not stored)."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    to_store = {k: v for k, v in parties.items()
                if not (NETWORK_NAME == "devnet" and k in _DEVNET_BUILTINS)}
    (WORKSPACE / "parties.json").write_text(json.dumps(to_store, indent=2))


def reload_parties() -> dict:
    global PARTIES
    PARTIES = _all_parties()
    return PARTIES


# ---------------------------------------------------------------- nodes

def _default_nodes() -> dict:
    """Provisioned nodes from the workspace, plus the demo trio on devnet."""
    base = {}
    if NETWORK_NAME == "devnet":
        base = {
            1: {"ws": "ws://127.0.0.1:4001", "http": "http://127.0.0.1:4001",
                "name": "alice", "metrics": None},
            2: {"ws": "ws://127.0.0.1:4002", "http": "http://127.0.0.1:4002",
                "name": "bob", "metrics": None},
            3: {"ws": "ws://127.0.0.1:4003", "http": "http://127.0.0.1:4003",
                "name": "carol", "metrics": None},
        }
    registry = WORKSPACE / "nodes.json"
    if registry.exists():
        base.update({int(k): v for k, v in json.loads(registry.read_text()).items()})
    return base


NODES = _default_nodes()


_DEVNET_NODE_INDICES = (1, 2, 3)


def save_nodes(nodes: dict) -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    to_store = {k: v for k, v in nodes.items()
                if not (NETWORK_NAME == "devnet" and k in _DEVNET_NODE_INDICES)}
    (WORKSPACE / "nodes.json").write_text(
        json.dumps({str(k): v for k, v in to_store.items()}, indent=2))


def reload_nodes() -> dict:
    global NODES
    NODES = _default_nodes()
    return NODES


# ---------------------------------------------------------------- safety floors

# Head outputs below the L1 min-UTXO can never be fanned out. On devnet the
# live params are zeroed so we keep the proven 1 ADA floor; on real networks
# tools derive the floor from live utxoCostPerByte at call time and this
# value is the fallback.
MIN_OUTPUT_LOVELACE = 1_000_000
