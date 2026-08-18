# hydra-ops-mcp

**Operate a Hydra head by talking to it.** An MCP server that exposes a
running head — lifecycle, ledger, L1 wallets, node logs, and on-chain error
codes — as tools an LLM client can call, so you can drive and debug a head
in plain language instead of switching between a TUI, `curl`, `cardano-cli`,
and `docker logs`.

It covers the full operational surface: `init`, deposits, in-head
transactions, `decommit`, `close`, `fanout`, partial fanout, and deposit
recovery, plus read-only views of head state and the L1. Every operation that
changes state describes what it would do and waits for your explicit
confirmation before doing it.

---

## Contents

- [Why](#why) · [Quick start](#quick-start) · [Architecture](#architecture)
- [The confirmation model](#the-confirmation-model)
- [Tool reference](#tool-reference) — [observability](#observability-read-only),
  [lifecycle](#lifecycle-confirmation-gated),
  [transactions](#transactions-confirmation-gated),
  [diagnosis](#diagnosis-read-only)
- [Compared with hydra-tui](#compared-with-hydra-tui)
- [Configuration](#configuration) · [Testing](#testing)
- [Operational notes](#operational-notes) · [Limitations](#limitations)
- [Extending](#extending)

---

## Why

Operating a head means holding several tools at once. The TUI shows you head
state but not why a transaction was rejected. The WebSocket API gives you
events but you're parsing JSON by hand. When something goes wrong the answer
is usually in `docker compose logs`, correlated against head state, and
decoded against error codes that live in the Plutus source.

This server puts all of that behind one conversational interface:

> *"The head won't fan out. What's wrong?"*

Claude can check head state, pull the failing transaction from the node logs,
decode the `H39` abort code to `FanoutUTxOHashMismatch`, and tell you the two
things that actually cause it — in one turn, because it has the head API, the
container logs, and the error tables all in reach.

It's also useful for the routine parts: opening and funding a head, moving
funds, and settling out, with each step explained and confirmed before it
runs. And unlike a TUI session bound to a single node, every tool takes a
`node` argument, so you can compare what alice, bob and carol each believe
about the same head.

Currently targets the **hydra demo devnet** (three nodes, three parties). The
API layer is not devnet-specific; the L1 helpers and key handling are (see
[Limitations](#limitations)).

---

## Quick start

**Prerequisites** — Docker, Python 3.10+, and a checkout of
[cardano-scaling/hydra](https://github.com/cardano-scaling/hydra) (for the
demo devnet and the Plutus error tables).

```bash
git clone https://github.com/skoniog/hydra-ops-mcp && cd hydra-ops-mcp
python3 -m venv .venv                      # or: uv venv .venv
.venv/bin/pip install -r requirements.txt

./reset_devnet.sh                          # cardano-node + 3 hydra-nodes, seeded
```

Register the server with your MCP client. Claude Code:

```bash
claude mcp add hydra-ops -- /absolute/path/to/hydra-ops-mcp/.venv/bin/python \
    /absolute/path/to/hydra-ops-mcp/server.py
```

Claude Desktop — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hydra-ops": {
      "command": "/absolute/path/to/hydra-ops-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/hydra-ops-mcp/server.py"]
    }
  }
}
```

MCP launchers spawn the server with a stripped environment, so pass any
overrides (`HYDRA_DEMO_DIR`, `HYDRA_REPO`) in an `"env"` block rather than
exporting them in your shell.

Then ask:

> *"What state is the head in, and what does alice hold on L1?"*
> *"Open a head and commit alice's funds."*
> *"Send 5 ADA from alice to bob, then show me the head UTXO set."*

New to operating a head this way? **[RUNBOOK.md](RUNBOOK.md)** walks the whole
lifecycle — open, fund, transact, decommit, close, settle, and break it on
purpose — as a series of guided sessions.

---

## Architecture

```
  MCP client (Claude Code / Claude Desktop / anything speaking MCP)
        │  stdio
        ▼
  server.py                 FastMCP registration; thin wrappers only
        │
  tools/                    one module per domain, plain functions
   ├── observe.py           head state, UTXOs, L1 funds, params, events
   ├── lifecycle.py         init, commit, decommit, close, fanout, recover
   ├── transact.py          in-head transfers
   ├── diagnose.py          node logs, error-code decoding
   └── types.py             ok() / err() / needs_confirmation()
        │
        ├──▶ hydra_client.py    WebSocket + HTTP to hydra-node
        │                       async core, sync facade, event buffer
        ├──▶ tx_builder.py      PyCardano: build + sign in-head txs
        ├──▶ cardano.py         cardano-cli in the node container (L1)
        └──▶ errors.py          parses hydra-plutus for abort codes
```

**`hydra_client.py`** holds a WebSocket connection per node, running an async
event loop on a daemon thread behind a synchronous facade — so tool functions
stay simple while still awaiting protocol events. It buffers every server
output for `recent_events`, tracks head status, and correlates confirmed
transactions. Commands wait for their specific outcome event
(`Decommit` → `DecommitFinalized`, `Fanout` → `HeadIsFinalized`) rather than
returning optimistically, so a tool call that succeeds means the protocol
step actually completed.

**`tx_builder.py`** builds and signs transactions with PyCardano — no
`cardano-cli` round-trip per transaction. **`cardano.py`** handles the L1 side
(address derivation, UTXO queries, signing and submitting deposit
transactions) by exec'ing `cardano-cli` inside the running cardano-node
container, which is also where the keys live.

**`errors.py`** parses `HeadError.hs`, `DepositError.hs`, `HeadTokensError.hs`
and friends out of your local hydra checkout at call time, so decoded codes
always match the version you're running rather than a table that drifts.

---

## The confirmation model

Every tool that changes state takes `confirm: bool = False`. Called without
it, the tool validates everything it can, resolves what it would actually do,
and returns a description — having changed nothing:

```json
{
  "status": "requires_confirmation",
  "action": "deposit alice's UTXO 4a3f…#0 (100,000,000,000 lovelace) into the head via node 1",
  "message": "This would deposit… Nothing has been done. Retry with confirm=True to execute.",
  "party": "alice", "utxo_ref": "4a3f…#0", "lovelace": 100000000000
}
```

In practice this means Claude proposes, you approve, and only then does
anything happen on-chain. It matters most for the operations that are
unilateral and irreversible: `close_head` affects every participant in the
head, and `fanout` settles the head's final state.

The preview is resolved, not hypothetical — `commit_funds` names the exact
UTXO it selected, `decommit` names the owner and amount it derived from the
head's UTXO set, `send_tx` reports the transaction id it built. Validation
runs *before* the gate, so you're never asked to confirm something that would
have failed anyway. Read-only tools have no gate and run immediately.

---

## Tool reference

All tools return `{status, error, ...}`; failures are
`{"status": "error", "error": "<message>", ...}` rather than exceptions. Every
tool accepts `node: int = 1` (1 = alice, 2 = bob, 3 = carol) except
`l1_funds` and `explain_error`.

### Observability (read-only)

| Tool | Signature | Returns |
|---|---|---|
| `head_status` | `(node=1)` | Head tag, WS-observed status, UTXO count, total lovelace, snapshot number, head version, contestation deadline |
| `head_utxos` | `(node=1)` | The head's UTxO set grouped by address, each with ref and value |
| `l1_funds` | `(party="alice")` | A party's L1 address, UTXO count, total lovelace, and per-UTXO values |
| `protocol_parameters` | `(node=1)` | The head's ledger parameters — full set plus a summary of the ones that bite (fees, min-UTXO, sizes) |
| `pending_deposits` | `(node=1)` | Deposits observed but not yet absorbed — the recovery candidates |
| `recent_events` | `(node=1, tag=None, limit=25)` | Server outputs seen on this connection, optionally filtered by tag |

`recent_events` covers events since the server connected — the WS connection
requests no history, so it's a live tail rather than the full log. For
anything older, use `node_logs`.

### Lifecycle (confirmation-gated)

| Tool | Signature | Notes |
|---|---|---|
| `init_head` | `(node=1, confirm=False)` | Refuses unless the head is `Idle`. On 2.3.0 the head opens immediately and empty; funds follow via deposits |
| `commit_funds` | `(party="alice", node=1, utxo_ref="", confirm=False)` | Drafts the deposit via `POST /commit`, signs with the party's funds key, submits to L1, then waits for absorption. Deposits **one** UTXO — the largest unless `utxo_ref` names another |
| `decommit` | `(utxo_ref, node=1, confirm=False)` | Withdraws one head UTxO to L1 with the head still open. Derives the owner from the UTxO's address and builds a full-value self-transfer as the decommit tx |
| `close_head` | `(node=1, confirm=False)` | Posts the latest confirmed snapshot and starts the contestation period. Affects all participants |
| `fanout` | `(node=1, confirm=False)` | Waits for `ReadyToFanout` if needed, then distributes the whole UTxO set to L1 |
| `partial_fanout` | `(utxo_refs, node=1, confirm=False)` | Settles a chosen subset; reports what was distributed and what remains. See [Limitations](#limitations) — needs a node newer than 2.3.0 |
| `recover_deposit` | `(tx_id, node=1, confirm=False)` | `DELETE /commits/{txid}` — returns a stuck deposit to L1 |

`commit_funds` deliberately deposits a single UTXO per call: multi-UTXO
deposits are what wedge fanout with `H39` on 2.3.0
(see [Operational notes](#operational-notes)).

### Transactions (confirmation-gated)

| Tool | Signature | Notes |
|---|---|---|
| `send_tx` | `(sender, receiver, amount_lovelace, node=1, confirm=False)` | In-head transfer. `sender` is a party whose signing key is available; `receiver` is a party name or a bech32 address |

Amounts below **1 ADA are refused**. The head zeroes min-UTXO, so such an
output is valid on L2 and then impossible to recreate on L1 — it would wedge
fanout permanently. The transaction is rebuilt against the current UTxO set at
confirmation time, so a preview that sat around doesn't spend stale inputs.
The call returns once the transaction appears in a confirmed snapshot, not
merely when it's accepted.

### Diagnosis (read-only)

| Tool | Signature | Notes |
|---|---|---|
| `node_logs` | `(node=1, pattern="", since="10m", limit=40)` | Container logs, optionally regex-filtered. Returns how many lines matched and the last `limit` of them |
| `explain_error` | `(code)` | Decodes an abort code (`H39`, `D01`, …) to its constructor and module from your local hydra checkout, with practical notes on the ones that actually come up |

---

## Compared with hydra-tui

The tool surface deliberately matches what `hydra-tui` exposes, so anything
you can do in the TUI you can do here:

| hydra-tui | here |
|---|---|
| `i` — init | `init_head` |
| commit dialog | `commit_funds` (drafts, signs, submits, waits for absorption) |
| `n` — new transaction | `send_tx` |
| `d` — decommit | `decommit` |
| `c` — close | `close_head` |
| `f` — fanout | `fanout` |
| `p` — partial fanout | `partial_fanout` |
| `r` — recover deposit | `recover_deposit` |
| main tab | `head_status`, `head_utxos` |
| funds tab | `l1_funds` |
| event history tab | `recent_events` |
| — | `protocol_parameters`, `pending_deposits` |

Like the TUI, this doesn't expose `Contest`, `SafeClose` or
`SideLoadSnapshot`. Those are protocol responses to specific on-chain
conditions where one action is correct and timing is load-bearing; they
belong in deterministic tooling with alerting, not behind a prompt.

**Where this goes further:**

- **Diagnosis.** `node_logs` and `explain_error` have no TUI equivalent. This
  is the biggest practical gain — a wedged head goes from "the TUI says it
  failed" to a decoded abort code and the matching log lines.
- **Cross-node.** A TUI session attaches to one node. Here every tool takes
  `node`, so you can ask what alice, bob and carol each believe about the same
  head — the fastest way to spot a node that has fallen behind.
- **L1 and L2 together.** `l1_funds` queries the chain directly, so
  "did that decommit actually land?" is one question rather than a context
  switch to `cardano-cli`.
- **Guardrails.** Sub-min-UTXO outputs and multi-UTXO deposits are refused by
  construction, because both silently wedge fanout later.
- **Composition.** Multi-step operations happen in one request:
  *"close the head, wait out contestation, fan out, and show me everyone's
  final L1 balances"* is a single ask.

**Where the TUI still wins:** it's a live dashboard. MCP is request/response,
so you get snapshots rather than a continuously updating view — for watching
a head over time, keep the TUI open. Keystrokes also beat a model round-trip
for repetitive work, and the TUI's UTxO pickers are visual where here you
list then select.

---

## Configuration

Everything is in `config.py`, with environment overrides:

| Setting | Default | Meaning |
|---|---|---|
| `NODES` | `4001`, `4002`, `4003` on localhost | Node index → WS/HTTP endpoints and party name |
| `HYDRA_DEMO_DIR` | `/home/dev/claudecode/hydra/demo` | Demo devnet: docker compose project and credentials |
| `HYDRA_REPO` | `/home/dev/claudecode/hydra` | Hydra checkout, for decoding abort codes |
| `NETWORK_MAGIC` | `42` | Devnet magic |
| `MIN_OUTPUT_LOVELACE` | `1_000_000` | Refusal threshold for in-head outputs |

Signing keys are the demo's `{alice,bob,carol}-funds` pairs. Container-side
paths are used for `cardano-cli` (signing and submitting on L1); host-side
copies of the same keys are read by PyCardano for in-head transactions.
Pointing at a different deployment with the same layout is a config change;
pointing at a different *topology* is not (see [Limitations](#limitations)).

---

## Testing

```bash
.venv/bin/python test_ops.py           # offline — no devnet needed
.venv/bin/python test_ops_devnet.py    # live — needs a devnet with the head Idle
```

**`test_ops.py`** asserts that every state-changing tool returns
`requires_confirmation` and reaches no client without `confirm=True` (the
stub client raises if a command escapes the gate), that request payloads match
the API, that the min-UTXO refusal and UTxO validation fire, that the error
table parses and decodes, and that all 16 tools register with the server.

**`test_ops_devnet.py`** drives a real head through the whole lifecycle and
asserts observability at each stage: gate check → `init` → `commit` →
six read tools → two in-head payments → **decommit, verified by the funds
appearing on L1 while the head stays open** → `close` → `fanout` → back to
`Idle` → logs and error decoding. It skips with a clear message if the devnet
isn't up or the head isn't `Idle`.

---

## Operational notes

Things worth knowing before they cost you a head.

**`H39` / `FanoutUTxOHashMismatch` wedges a head permanently.** Fanout can't
reproduce what the closed head committed to, so the head cannot settle and its
funds are stuck. Two causes, both preventable and both guarded against here:
multi-UTXO deposits on 2.3.0, and any head output below the L1 min-UTXO. Ask
`explain_error("H39")` for the details.

**The head zeroes min-UTXO; L1 does not.** A 0.5 ADA output transacts happily
on L2 and then cannot be recreated on L1. `send_tx` refuses below 1 ADA for
this reason.

**Deposits are absorbed after a deposit period**, not instantly.
`commit_funds` waits and reports if absorption doesn't happen; a deposit that
never lands shows up in `pending_deposits` and comes back with
`recover_deposit`.

**Close is unilateral and affects everyone.** Any participant can close, and
the whole head must then settle. The gate exists mostly for this.

**A head needs every participant online.** If a payment hangs, check
`docker compose ps` before suspecting the tooling.

**The demo devnet's block producer can stall** after long idle periods —
`cardano-cli query tip` returns the same slot twice and everything hangs.
`./reset_devnet.sh` fixes it; the devnet is disposable by design.

**Unparseable WebSocket input returns no `tag`.** A command a node doesn't
recognize comes back as a bare `{"input", "reason"}` object rather than a
tagged event — worth knowing if you script against the API directly, since a
client waiting on tagged events will hang. The client here handles it.

---

## Limitations

**`partial_fanout` needs a node newer than 2.3.0.** The command postdates the
release (hydra PR #2750, commit `a271cced2`), and the pinned demo image
rejects it — the node lists the commands it knows and `PartialFanout` isn't
among them. The tool detects this precisely and reports the version gap. The
code path is ready for a node built from master but has only been exercised up
to that rejection.

**Fees are zero.** `tx_builder.py` hardcodes `fee=0`, which is correct for the
demo's protocol parameters and wrong everywhere else. Real fee estimation and
coin selection are needed before this points at preview/preprod or mainnet.

**Devnet-shaped assumptions.** Three parties with known key names, keys
readable inside the cardano-node container, `docker compose` available for L1
queries and logs. The head API layer is general; the L1 helpers are not.

**ADA only.** Transaction building handles pure-lovelace UTXOs — no native
tokens, scripts, datums, or minting.

**`recover_deposit` is untested against a genuinely stuck deposit.** It
follows the API, but the demo devnet absorbs deposits too reliably to produce
one on demand.

**No auth.** Anyone who can reach the server can operate the head. That's
appropriate for a local operator tool and would not be for anything exposed.

---

## Extending

**Adding a tool:** write a plain function in the relevant `tools/` module
returning `ok()` / `err()` / `needs_confirmation()`, then register a thin
wrapper in `server.py`. Tool modules don't import FastMCP, so they're directly
callable from tests — which is how both suites drive them.

**Adding a protocol command:** add a method to `HydraClient` using
`_command_and_wait(command, ok_tags)`, which sends and waits for the outcome
event while treating both `CommandFailed` and untagged parse rejections as
errors.

**Targeting another deployment:** point `NODES` at the endpoints and
`HYDRA_DEMO_DIR` / `HYDRA_REPO` at the right paths. Anything beyond the demo's
three-party layout means revisiting key handling in `cardano.py` and
`tx_builder.py`, and fees.

## Further reading

The [Hydra documentation](https://hydra.family) for the protocol itself, and
[RUNBOOK.md](RUNBOOK.md) for the guided tour of operating a head with these
tools.
