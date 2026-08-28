"""Provision and run hydra-nodes: keys, config, containers, health.

The lifecycle this enables, end to end from tool calls:

  generate_party_keys() -> fund the fuel address (human, faucet) ->
  build_head_protocol_parameters() -> exchange peer_info() with your
  counterparty -> plan_node() -> start() -> health() -> operate the head.

Nodes run as Docker containers named hydra-ops-<party>, with the workspace
mounted at /data. Everything the node needs (keys, params file, peer vks,
persistence) lives under config.WORKSPACE, owned by the host user.
"""

import json
import re
import subprocess
import time
from pathlib import Path

import httpx
from pycardano import PaymentSigningKey, PaymentVerificationKey

import config
import networks
from cardano import address_for_vk_file
from providers import get_provider


class NodeManagerError(Exception):
    pass


DOCKER_NETWORK = "hydra-ops-net"


def ensure_docker_network() -> None:
    r = subprocess.run(["docker", "network", "inspect", DOCKER_NETWORK],
                       capture_output=True, text=True)
    if r.returncode != 0:
        _run(["docker", "network", "create", DOCKER_NETWORK])


def _run(cmd, timeout=120, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    if r.returncode != 0:
        raise NodeManagerError(f"{cmd[0]} failed: {(r.stderr or r.stdout)[:500]}")
    return r


def _uid_gid() -> str:
    import os
    return f"{os.getuid()}:{os.getgid()}"


_image_help_cache: dict = {}


def _image_supports(flag: str) -> bool:
    """Whether the configured node image's CLI knows a flag (cached probe)."""
    image = config.HYDRA_NODE_IMAGE
    if image not in _image_help_cache:
        r = subprocess.run(["docker", "run", "--rm", image, "--", "--help"],
                           capture_output=True, text=True, timeout=120)
        _image_help_cache[image] = r.stdout + r.stderr
    return flag in _image_help_cache[image]


# ------------------------------------------------------------------ keys

def generate_party_keys(party: str, overwrite: bool = False) -> dict:
    """Create fuel + funds Cardano key pairs and Hydra keys for a party we
    operate. Registers the party in the workspace. Never returns secrets."""
    if not re.fullmatch(r"[a-z0-9-]{1,32}", party):
        raise NodeManagerError("party name must be lowercase alphanumeric/dash")
    keydir = config.WORKSPACE / "keys" / party
    if keydir.exists() and any(keydir.iterdir()) and not overwrite:
        raise NodeManagerError(f"keys for {party!r} already exist at {keydir}; "
                               f"pass overwrite=True to replace them")
    keydir.mkdir(parents=True, exist_ok=True)

    paths = {}
    for wallet in ("fuel", "funds"):
        sk = PaymentSigningKey.generate()
        vk = PaymentVerificationKey.from_signing_key(sk)
        sk_path, vk_path = keydir / f"{wallet}.sk", keydir / f"{wallet}.vk"
        sk.save(str(sk_path)) if not sk_path.exists() or overwrite else None
        vk.save(str(vk_path)) if not vk_path.exists() or overwrite else None
        sk_path.chmod(0o600)
        paths[f"{wallet}_sk"], paths[f"{wallet}_vk"] = str(sk_path), str(vk_path)

    # Hydra keys come from the node binary itself (Ed25519, snapshot signing).
    if (keydir / "hydra.sk").exists() and overwrite:
        (keydir / "hydra.sk").unlink()
        (keydir / "hydra.vk").unlink(missing_ok=True)
    _run(["docker", "run", "--rm", "--user", _uid_gid(),
          "-v", f"{keydir}:/keys", config.HYDRA_NODE_IMAGE,
          "--", "gen-hydra-key", "--output-file", "/keys/hydra"])
    (keydir / "hydra.sk").chmod(0o600)
    paths["hydra_sk"], paths["hydra_vk"] = str(keydir / "hydra.sk"), str(keydir / "hydra.vk")

    parties = config.reload_parties()
    parties[party] = {**paths, "ours": True}
    config.save_parties(parties)

    return {
        "party": party,
        "fuel_address": address_for_vk_file(paths["fuel_vk"]),
        "funds_address": address_for_vk_file(paths["funds_vk"]),
        "hydra_vk_hash": json.loads(Path(paths["hydra_vk"]).read_text())["cborHex"][:24] + "…",
        "keys_dir": str(keydir),
    }


# ------------------------------------------------------------------ head params

# Parameters that are SAFE to zero for the head ledger: strictly
# transaction-scoped. Anything reflected in the UTxO (utxoCostPerByte and
# friends) must stay at L1 values or the head can produce unfanoutable
# outputs (docs faqs.md; the H39 failure class).
def build_head_protocol_parameters() -> Path:
    params = dict(get_provider().protocol_parameters())
    from_blockfrost = params.pop("_blockfrost_raw", None) is not None
    if from_blockfrost:
        # The node requires the COMPLETE cardano-cli parameter shape
        # (maxBlockBodySize, cost models, governance params, ...). Blockfrost
        # covers the consensus-relevant fields; take the full structure from
        # the hydra repo's template and overlay every live value we have.
        template_path = (config.HYDRA_REPO / "hydra-cluster" / "config"
                         / "protocol-parameters.json")
        if not template_path.exists():
            raise NodeManagerError(
                f"parameter template not found at {template_path}; a full "
                f"cardano-cli-shaped file is required for blockfrost mode")
        template = json.loads(template_path.read_text())
        template.update(params)
        params = template
    params["txFeeFixed"] = 0
    params["txFeePerByte"] = 0
    if isinstance(params.get("executionUnitPrices"), dict):
        params["executionUnitPrices"]["priceMemory"] = 0
        params["executionUnitPrices"]["priceSteps"] = 0

    if config.NETWORK_NAME != "devnet" and not params.get("utxoCostPerByte"):
        raise NodeManagerError(
            "refusing to write head parameters with utxoCostPerByte=0 on a "
            "real network: outputs created under it can never be fanned out")

    config.WORKSPACE.mkdir(parents=True, exist_ok=True)
    out = config.WORKSPACE / "protocol-parameters.json"
    out.write_text(json.dumps(params, indent=2, sort_keys=True))
    return out


# ------------------------------------------------------------------ peers

def peer_info(party: str, host: str, port: int) -> dict:
    """Everything a counterparty needs to add us as a peer."""
    info = config.reload_parties().get(party) or {}
    for key in ("fuel_vk", "hydra_vk"):
        if not info.get(key) or not Path(info[key]).exists():
            raise NodeManagerError(f"{party} has no {key}; run generate_keys first")
    return {
        "name": party,
        "address": f"{host}:{port}",
        "cardano_verification_key": json.loads(Path(info["fuel_vk"]).read_text()),
        "hydra_verification_key": json.loads(Path(info["hydra_vk"]).read_text()),
        "network": config.NETWORK_NAME,
        "contestation_period_seconds": config.CONTESTATION_PERIOD,
        "hydra_node_version": config.HYDRA_NODE_VERSION,
    }


def _write_peer_keys(peers: list) -> list:
    """Persist counterparties' vks; returns [(address, cardano_vk_path,
    hydra_vk_path)]."""
    peer_dir = config.WORKSPACE / "peers"
    peer_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for peer in peers:
        name = re.sub(r"[^a-z0-9-]", "-", peer["name"].lower())
        cvk = peer_dir / f"{name}-cardano.vk"
        hvk = peer_dir / f"{name}-hydra.vk"
        cvk.write_text(json.dumps(peer["cardano_verification_key"]))
        hvk.write_text(json.dumps(peer["hydra_verification_key"]))
        rows.append((peer["address"], str(cvk), str(hvk)))
    return rows


# ------------------------------------------------------------------ node planning

def _scripts_tx_id() -> str:
    # An explicit override always wins — required whenever the node image
    # doesn't match the published scripts (e.g. an `unstable` build, whose
    # validators differ from the release the network's scripts came from).
    import os
    override = os.environ.get("HYDRA_SCRIPTS_TX_ID")
    if override:
        return override
    if config.NETWORK_NAME == "devnet":
        env = (config.DEMO_DIR / ".env").read_text()
        m = re.search(r"HYDRA_SCRIPTS_TX_ID=([0-9a-f,]+)", env)
        if not m:
            raise NodeManagerError("devnet scripts not published — run reset_devnet.sh")
        return m.group(1)
    ids = networks.script_tx_ids(config.NETWORK_NAME, config.HYDRA_NODE_VERSION)
    if not ids:
        raise NodeManagerError(
            f"no published scripts for hydra-node {config.HYDRA_NODE_VERSION} on "
            f"{config.NETWORK_NAME}; publish with `hydra-node publish-scripts` "
            f"(~50 ada) and set HYDRA_SCRIPTS_TX_ID")
    return ids


def _to_container(path: str) -> str:
    """Map a workspace host path to its /data path inside the container."""
    return str(Path("/data") / Path(path).resolve().relative_to(config.WORKSPACE.resolve()))


def plan_node(party: str, peers: list, api_port: int, listen_port: int,
              metrics_port: int, advertise_host: str = "127.0.0.1") -> dict:
    """The docker command and flag set that would run this party's node."""
    info = config.reload_parties().get(party)
    if not info or not info.get("ours"):
        raise NodeManagerError(f"{party} is not a party we operate")
    params_file = config.WORKSPACE / "protocol-parameters.json"
    if not params_file.exists():
        raise NodeManagerError("no protocol-parameters.json — run "
                               "build_protocol_parameters first")

    persistence = config.WORKSPACE / "persistence" / party
    persistence.mkdir(parents=True, exist_ok=True)

    flags = [
        "--node-id", f"{party}-node",
        "--persistence-dir", _to_container(str(persistence)),
        "--cardano-signing-key", _to_container(info["fuel_sk"]),
        "--hydra-signing-key", _to_container(info["hydra_sk"]),
        "--hydra-scripts-tx-id", _scripts_tx_id(),
        "--ledger-protocol-parameters", _to_container(str(params_file)),
        "--contestation-period", f"{config.CONTESTATION_PERIOD}s",
        "--deposit-period", f"{config.DEPOSIT_PERIOD}s",
        # master split absorption timing out of deposit-period into
        # --deposit-activation (default 3600s!); without this, deposits on
        # unstable builds sit inactive for an hour regardless of the period.
        *(["--deposit-activation", f"{config.DEPOSIT_PERIOD}s"]
          if _image_supports("--deposit-activation") else []),
        *(["--unsynced-period", f"{config.UNSYNCED_PERIOD}s"]
          if config.UNSYNCED_PERIOD else []),
        "--api-host", "0.0.0.0", "--api-port", str(api_port),
        "--listen", f"0.0.0.0:{listen_port}",
        "--advertise", f"{advertise_host}:{listen_port}",
        "--monitoring-port", str(metrics_port),
    ]
    for address, cvk, hvk in _write_peer_keys(peers):
        flags += ["--peer", address,
                  "--cardano-verification-key", _to_container(cvk),
                  "--hydra-verification-key", _to_container(hvk)]

    # All provisioned nodes share a user-defined docker network so containers
    # on one machine reach each other by container name (127.0.0.1 inside a
    # container is the container itself). For remote peers, publish the ports
    # and advertise a reachable host instead.
    network_args = ["--network", DOCKER_NETWORK]
    mounts = ["-v", f"{config.WORKSPACE}:/data"]
    if config.NETWORK_NAME == "devnet":
        # Direct backend against the devnet cardano-node's socket.
        flags += ["--testnet-magic", str(config.NETWORK_MAGIC),
                  "--node-socket", "/devnet/node.socket"]
        mounts += ["-v", f"{config.DEMO_DIR / 'devnet'}:/devnet"]
    elif config.PROVIDER == "blockfrost":
        bf = Path(config.BLOCKFROST_PROJECT_FILE)
        if not bf.exists():
            raise NodeManagerError(f"blockfrost project file missing: {bf}")
        flags += ["--blockfrost", _to_container(str(bf))] \
            if str(bf).startswith(str(config.WORKSPACE)) else \
            ["--blockfrost", "/data/blockfrost-project.txt"]
        if not str(bf).startswith(str(config.WORKSPACE)):
            mounts += ["-v", f"{bf}:/data/blockfrost-project.txt:ro"]
    else:
        if not config.NODE_SOCKET:
            raise NodeManagerError("CARDANO_NODE_SOCKET_PATH not set for direct backend")
        net = ["--mainnet"] if config.IS_MAINNET else \
            ["--testnet-magic", str(config.NETWORK_MAGIC)]
        flags += [*net, "--node-socket", "/ipc/node.socket"]
        mounts += ["-v", f"{Path(config.NODE_SOCKET).parent}:/ipc"]

    command = [
        "docker", "run", "-d", "--name", f"hydra-ops-{party}",
        "--user", _uid_gid(), "--restart", "unless-stopped",
        *network_args, *mounts,
        "-p", f"{api_port}:{api_port}",
        "-p", f"{listen_port}:{listen_port}",
        "-p", f"{metrics_port}:{metrics_port}",
        # No "--" separator here: it ends option parsing, which is right for
        # subcommands (gen-hydra-key) but rejects run-mode flags.
        config.HYDRA_NODE_IMAGE, *flags,
    ]
    return {
        "party": party,
        "container": f"hydra-ops-{party}",
        "image": config.HYDRA_NODE_IMAGE,
        "api_port": api_port,
        "listen_port": listen_port,
        "metrics_port": metrics_port,
        "flags": flags,
        "command": command,
    }


# ------------------------------------------------------------------ run / stop

def start(plan: dict) -> dict:
    """Run the planned container and register it as an operable node."""
    ensure_docker_network()
    if config.NETWORK_NAME == "devnet":
        # The demo cardano-node runs as root and its unix socket is 0755;
        # our containers run as the host user, so open the socket up.
        # Devnet-only glue — Blockfrost-backed nodes have no socket at all.
        subprocess.run(
            ["docker", "compose", "exec", "-T", "cardano-node",
             "chmod", "666", "/devnet/node.socket"],
            cwd=config.DEMO_DIR, capture_output=True, text=True,
        )
    _run(plan["command"], timeout=300)
    import hydra_client
    nodes = config.reload_nodes()
    index = max(nodes, default=100) + 1
    hydra_client.drop_client(index)  # a stale client may exist for a reused index
    nodes[index] = {
        "ws": f"ws://127.0.0.1:{plan['api_port']}",
        "http": f"http://127.0.0.1:{plan['api_port']}",
        "name": plan["party"],
        "metrics": f"http://127.0.0.1:{plan['metrics_port']}/metrics",
        "container": plan["container"],
    }
    config.save_nodes(nodes)
    return {"node": index, "container": plan["container"]}


def stop(party: str, remove: bool = True) -> None:
    name = f"hydra-ops-{party}"
    subprocess.run(["docker", "stop", name], capture_output=True, text=True)
    if remove:
        subprocess.run(["docker", "rm", name], capture_output=True, text=True)
    import hydra_client
    for index, entry in config.reload_nodes().items():
        if entry.get("container") == name:
            hydra_client.drop_client(index)
    nodes = {k: v for k, v in config.reload_nodes().items()
             if v.get("container") != name}
    config.save_nodes(nodes)


def container_state(party: str) -> str:
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}",
                        f"hydra-ops-{party}"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "absent"


def metrics(url: str) -> dict:
    """Parse the node's Prometheus metrics into {name: value}."""
    out = {}
    text = httpx.get(url, timeout=10.0).text
    for line in text.splitlines():
        if line and not line.startswith("#"):
            parts = line.split()
            if len(parts) == 2:
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return out


def wait_api(http_url: str, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{http_url}/head", timeout=5.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False
