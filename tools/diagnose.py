"""Diagnosis tools — the step beyond TUI parity.

When a head misbehaves, the answer is usually in the node logs and the
Plutus error tables, neither of which the TUI surfaces. These are read-only.
"""

import re
import subprocess

import errors as error_tables
from config import DEMO_DIR
from tools.types import err, ok


def node_logs(node: int = 1, pattern: str = "", since: str = "10m",
              limit: int = 40) -> dict:
    """Recent log lines from a hydra-node container, optionally filtered.

    `pattern` is a regex applied per line; `since` is a docker duration
    (e.g. 10m, 2h). Lines are truncated to keep responses readable.
    """
    service = f"hydra-node-{node}"
    cmd = ["docker", "compose", "logs", service, "--since", since, "--no-log-prefix"]
    try:
        r = subprocess.run(cmd, cwd=DEMO_DIR, capture_output=True, text=True, timeout=30)
    except Exception as e:
        return err(str(e), node=node)
    if r.returncode != 0:
        return err(f"docker compose logs failed: {r.stderr[:400]}", node=node)

    lines = r.stdout.splitlines()
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return err(f"bad regex: {e}", pattern=pattern)
        lines = [ln for ln in lines if rx.search(ln)]
    tail = [ln[:400] for ln in lines[-limit:]]
    return ok("ok", node=node, since=since, matched=len(lines),
              returned=len(tail), lines=tail)


def explain_error(code: str) -> dict:
    """Decode a Hydra on-chain error code (H39, D01, ...) from the local
    hydra-plutus source, with practical notes for the ones that bite."""
    entry = error_tables.explain(code)
    if entry is None:
        known = sorted(error_tables.error_table())
        return err(f"unknown error code {code!r}; known codes range "
                   f"{known[0]}–{known[-1]} across {len(known)} entries" if known
                   else "error tables not found — is the hydra repo checked out?",
                   code=code)
    return ok("ok", **entry)
