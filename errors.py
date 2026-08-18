"""Decode Hydra on-chain error codes (H39, D01, ...) from the local source.

The Plutus validators abort with short codes; the mapping from code to
constructor lives in hydra-plutus. Parsing the local checkout keeps this
table in sync with the exact version being run instead of a hardcoded copy.
"""

import re
from functools import lru_cache

from config import HYDRA_REPO

ERROR_MODULES = [
    "hydra-plutus/src/Hydra/Contract/HeadError.hs",
    "hydra-plutus/src/Hydra/Contract/DepositError.hs",
    "hydra-plutus/src/Hydra/Contract/HeadTokensError.hs",
    "hydra-plutus/src/Hydra/Contract/CommitError.hs",
    "hydra-plutus/src/Hydra/Contract/InitialError.hs",
    "hydra-plutus/src/Hydra/Contract/UtilError.hs",
]

_LINE = re.compile(r'^\s*([A-Z][A-Za-z0-9]*)\s*->\s*"([A-Z]+[0-9]+)"')

# Practical context for the codes an operator is most likely to actually hit,
# learned the hard way on the local devnet.
NOTES = {
    "H39": ("Fanout rejected: the UTxO being fanned out does not hash to what the "
            "closed head committed to. Seen on hydra-node 2.3.0 after multi-UTXO "
            "deposits, and when the head contains outputs below the L1 min-UTXO "
            "(e.g. a 500,000-lovelace output) that cannot be recreated on L1. "
            "Once a head is wedged like this its funds are stuck — on a devnet, "
            "reset; on a real network, avoid sub-1-ADA outputs and single-UTXO "
            "deposits prevent it."),
    "H29": ("Close rejected: signature verification failed on the closing snapshot."),
}


@lru_cache(maxsize=1)
def error_table() -> dict:
    table = {}
    for rel in ERROR_MODULES:
        path = HYDRA_REPO / rel
        if not path.exists():
            continue
        module = path.stem  # e.g. HeadError
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _LINE.match(line)
            if m:
                constructor, code = m.groups()
                table[code] = {"constructor": constructor, "module": module}
    return table


def explain(code: str) -> dict | None:
    entry = error_table().get(code.strip().upper())
    if entry is None:
        return None
    result = dict(entry)
    result["code"] = code.strip().upper()
    note = NOTES.get(result["code"])
    if note:
        result["note"] = note
    return result
