#!/usr/bin/env bash
# Reset the local Hydra demo devnet to a clean, seeded state.
#
# Tears down the containers, wipes chain state, re-seeds the faucet and the
# three actors, republishes the Hydra reference scripts, and brings the
# cardano-node and three hydra-nodes back up. Takes a couple of minutes.
#
# Use it for a fresh start, and whenever the devnet's block producer stalls
# (symptom: `cardano-cli query tip` returns the same slot twice in a row and
# head operations hang).
#
# Set HYDRA_DEMO_DIR if your hydra checkout is not at the default path.
set -euo pipefail

DEMO_DIR="${HYDRA_DEMO_DIR:-/home/dev/claudecode/hydra/demo}"
HYDRA_NODE_IMAGE="${HYDRA_NODE_IMAGE:-ghcr.io/cardano-scaling/hydra-node:2.3.0}"

if [[ ! -d "$DEMO_DIR" ]]; then
  echo "error: hydra demo directory not found at $DEMO_DIR" >&2
  echo "clone https://github.com/cardano-scaling/hydra and set HYDRA_DEMO_DIR" >&2
  exit 1
fi

cd "$DEMO_DIR"

echo "[1/5] stopping containers..."
docker compose down --remove-orphans

echo "[2/5] wiping devnet state (root-owned files, so via a container)..."
docker run --rm -v "$DEMO_DIR":/demo alpine sh -c "rm -rf /demo/devnet"

echo "[3/5] preparing devnet..."
./prepare-devnet.sh

echo "[4/5] starting cardano-node and seeding actors..."
docker compose up -d cardano-node
sleep 10
# seed-devnet.sh publishes reference scripts with `docker run -it`, which
# fails without a TTY. Let it seed, ignore that last step, publish manually.
bash seed-devnet.sh || true

TXIDS=$(docker run --rm -v "$DEMO_DIR/devnet":/devnet \
  "$HYDRA_NODE_IMAGE" -- publish-scripts \
  --testnet-magic 42 --node-socket /devnet/node.socket \
  --cardano-signing-key /devnet/credentials/faucet.sk 2>/dev/null | tr -d '\r\n')
echo "HYDRA_SCRIPTS_TX_ID=$TXIDS" > .env
echo "published reference scripts: $TXIDS"

echo "[5/5] starting hydra nodes..."
docker compose up -d hydra-node-1 hydra-node-2 hydra-node-3
sleep 8
docker compose ps --format '{{.Name}} {{.Status}}'
echo
echo "devnet ready — the head is Idle. Ask Claude to initialize a head,"
echo "or run: .venv/bin/python test_ops_devnet.py"
