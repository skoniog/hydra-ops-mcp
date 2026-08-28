"""L1 provider interface: how this server reads and writes the Cardano chain.

Three implementations, one contract:

- docker  — cardano-cli inside the devnet's cardano-node container (v1 path)
- cli     — a local cardano-cli binary against a local node socket
- blockfrost — the Blockfrost REST API; no local node at all

The hydra-node has its own, separate backend choice (--node-socket vs
--blockfrost). This interface is only about *our* access for wallet queries,
deposit signing/submission, and protocol parameters.

UTXO dicts use hydra's shape throughout: {"txid#ix": {"address", "value", ...}}.
"""

from abc import ABC, abstractmethod


class ProviderError(Exception):
    pass


class L1Provider(ABC):
    name = "abstract"

    @abstractmethod
    def address_utxos(self, address: str) -> dict:
        """UTXOs at an address, hydra-shaped."""

    @abstractmethod
    def protocol_parameters(self) -> dict:
        """Current protocol parameters, cardano-cli JSON shape."""

    @abstractmethod
    def tip(self) -> dict:
        """{"slot": int, "block": int|None, "hash": str|None}"""

    @abstractmethod
    def submit_tx(self, tx_envelope: dict) -> str:
        """Submit a signed TextEnvelope transaction; return its tx id."""

    @abstractmethod
    def sign_tx(self, draft_envelope: dict, signing_key_path: str) -> dict:
        """Sign a draft TextEnvelope with the key at a host path."""

    def await_tx(self, tx_id: str, address: str, timeout: float = 300.0) -> bool:
        """Wait until tx_id appears in the address's UTXO set (default impl:
        poll address_utxos)."""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(ref.startswith(tx_id) for ref in self.address_utxos(address)):
                return True
            time.sleep(5)
        return False

    def total_lovelace(self, address: str) -> int:
        return sum(
            o.get("value", {}).get("lovelace", 0)
            for o in self.address_utxos(address).values()
        )


_provider = None


def get_provider() -> L1Provider:
    """The configured provider, constructed lazily from config.PROVIDER."""
    global _provider
    if _provider is None:
        import config
        kind = config.PROVIDER
        if kind == "blockfrost":
            from providers.blockfrost import BlockfrostProvider
            _provider = BlockfrostProvider(config.BLOCKFROST_PROJECT_FILE)
        elif kind == "cli":
            from providers.cli import CliProvider
            _provider = CliProvider(config.CARDANO_CLI, config.NODE_SOCKET,
                                    config.NETWORK_NAME)
        elif kind == "docker":
            from providers.docker_cli import DockerCliProvider
            _provider = DockerCliProvider(config.DEMO_DIR)
        else:
            raise ProviderError(f"unknown provider {kind!r}; "
                                f"expected blockfrost, cli, or docker")
    return _provider


def reset_provider():
    """Testing hook: drop the cached instance so config changes take effect."""
    global _provider
    _provider = None
