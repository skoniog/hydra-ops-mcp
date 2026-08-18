# hydra-ops-mcp

An **operator-facing** MCP server for Hydra heads: everything you can do in
`hydra-tui`, done through Claude instead — plus the diagnosis layer the TUI
doesn't have.

This is the counterpart to [`hydra-mcp`](../hydra-mcp/), which is deliberately
narrow because its consumer is an autonomous agent. Here the consumer is a
**human driving Claude**, so full head-lifecycle coverage is safe: every
state-changing tool is confirmation-gated — called without `confirm=True` it
describes exactly what it would do and changes nothing, so the human reads the
plan before anything runs.

## TUI parity

| TUI | Tool | Gated |
|---|---|---|
| `i` init | `init_head` | ✔ |
| commit dialog | `commit_funds` (POST /commit → sign → submit L1 → wait for absorption) | ✔ |
| `n` new tx | `send_tx` | ✔ |
| `d` decommit | `decommit` (self-transfer of one head UTXO, per the TUI's flow) | ✔ |
| `c` close | `close_head` | ✔ |
| `f` fanout | `fanout` | ✔ |
| `p` partial fanout | `partial_fanout` | ✔ |
| `r` recover deposit | `recover_deposit` (DELETE /commits/{txid}) | ✔ |
| main tab | `head_status`, `head_utxos` | read-only |
| funds tab | `l1_funds` | read-only |
| event history tab | `recent_events` | read-only |
| — | `protocol_parameters`, `pending_deposits` | read-only |

**Beyond parity** — the reason to use this over the TUI:

- `node_logs(node, pattern, since)` — regex-filtered hydra-node container logs.
- `explain_error(code)` — decodes on-chain error codes (`H39`, `D01`, …) by
  parsing the local `hydra-plutus` source, with practical notes for the ones
  that bite (H39 carries the multi-UTXO-deposit / sub-min-UTXO story).
- Every tool takes `node: int` (1=alice, 2=bob, 3=carol) — the TUI attaches to
  one node; this can cross-examine all three plus the L1.

## Quick start

```bash
cd hydra-ops-mcp
python3 -m venv .venv                      # or: uv venv .venv
.venv/bin/pip install -r requirements.txt

# devnet up first (from hydra-mcp): ./reset_devnet.sh
.venv/bin/python test_ops.py               # offline: gating, shapes, error decode
.venv/bin/python test_ops_devnet.py        # live: full lifecycle from Idle to Idle
```

Connect from Claude Desktop / Claude Code the same way as hydra-mcp, pointing
at this directory's `server.py`. Then ask things like:

> "What state is the head in, and who holds what?"
> "Open a head and commit alice's funds."  *(Claude will show the plan; you confirm)*
> "Why did fanout fail? Check the logs and decode the error."

## What the live test proves

`test_ops_devnet.py` drives a real head through the entire TUI surface:
init → commit (deposit absorbed) → all six observability tools → two in-head
payments → **decommit** (carol's 3 ADA verified back on L1 while the head
stays open) → close → fanout → head back to Idle → logs + error decoding.

## Known limits

- **`partial_fanout` needs a hydra-node newer than 2.3.0.** The command was
  merged after the 2.3.0 release (hydra PR #2750, commit `a271cced2`); the
  pinned demo image rejects it. The tool detects this and says so — the code
  path is ready for a node built from master, but has only been exercised up
  to the version rejection on 2.3.0.
- Signing keys are the demo's three parties (alice/bob/carol), read from the
  devnet credentials directory. Point `HYDRA_DEMO_DIR` elsewhere to target a
  different deployment with the same layout.
- `Contest`, `SafeClose`, and `SideLoadSnapshot` are deliberately not exposed —
  the TUI doesn't expose them either, and they are protocol-response
  operations better handled by deterministic tooling.
- Fees are zero (demo protocol parameters); real networks need fee estimation.
- `recover_deposit` is implemented per the API (DELETE /commits/{txid}) but has
  not been exercised against a genuinely stuck deposit — the demo devnet
  absorbs deposits too reliably to produce one on demand.
