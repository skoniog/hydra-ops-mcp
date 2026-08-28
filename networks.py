"""Network registry: magic numbers and pre-published Hydra script tx ids.

Script ids are per hydra-node release, copied from hydra-node/networks.json
(the same data `--network <name>` resolves from inside released binaries).
An unreleased (master/`unstable`) node has no entry here — its scripts must
be published with `hydra-node publish-scripts` and passed explicitly.
"""

DEVNET_MAGIC = 42

NETWORKS = {
    "devnet": {
        "magic": DEVNET_MAGIC,
        "mainnet": False,
        # Devnet scripts are published locally by reset_devnet.sh into
        # hydra/demo/.env; resolved at runtime, not pinned here.
        "script_tx_ids": {},
        "explorer": None,
    },
    "preview": {
        "magic": 2,
        "mainnet": False,
        "script_tx_ids": {
            "2.3.0": "4797dc5e1c497d7dce0e591e6322855c836f9eac6a253d1342e58778962931e7,"
                     "68c0f7527e9c8ddb5f76cb3b020faa60a623cee6abaf74fb33334c803f906a97",
        },
        "explorer": "https://preview.cexplorer.io/tx/",
    },
    "preprod": {
        "magic": 1,
        "mainnet": False,
        "script_tx_ids": {
            "2.3.0": "b88df0c62f9734f0a6dba0faa7636ed51699cbe21706ce4a9736684daf418d67,"
                     "40ab074125b4734939cd45a00b2cfe1b20d679b2a1c52d6472aca193f637a2bc",
        },
        "explorer": "https://preprod.cexplorer.io/tx/",
    },
    "mainnet": {
        "magic": None,  # mainnet has no magic flag; hydra-node takes --mainnet
        "mainnet": True,
        "script_tx_ids": {
            "2.3.0": "f72df33dbc1001c9e65e454e97558b34afe254a77d6fd29270ad45a2328e06aa,"
                     "d7adaca74e78536dfa7beb5c550d97675513e9b004afb1d9aba479b8972cdd8f",
        },
        "explorer": "https://cexplorer.io/tx/",
    },
}


class NetworkError(Exception):
    pass


def network(name: str) -> dict:
    if name not in NETWORKS:
        raise NetworkError(f"unknown network {name!r}; expected one of {sorted(NETWORKS)}")
    return NETWORKS[name]


def script_tx_ids(network_name: str, hydra_version: str) -> str | None:
    """Comma-separated script tx ids for a released hydra-node version, or
    None when the version has no published scripts on that network (an
    unreleased build, or the devnet where scripts are locally published)."""
    return network(network_name)["script_tx_ids"].get(hydra_version)
