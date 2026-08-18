# Learning Hydra by Driving One — a Runbook

A hands-on playbook for someone new to Hydra. Instead of reading the protocol
docs first, you'll operate a real Hydra head through Claude and learn each
concept at the moment you use it. Everything runs on a local devnet — real
Cardano node, real hydra-nodes, real transactions — where mistakes cost
nothing and reset takes a minute.

**How to use this:** work through the sessions in order; each one teaches one
concept. The *"Say to Claude"* lines are literal prompts to type. Claude will
show you what each operation would do before doing it — every state-changing
tool requires explicit confirmation, so read the plan, then say "yes, confirm".

---

## The mental model (read this once, refer back often)

Hydra is a **layer 2**: a small group of participants locks funds on Cardano
(layer 1), then transacts among themselves off-chain at high speed and zero
fees, and finally settles the end result back to layer 1. The thing they share
is called a **head**, and it moves through a lifecycle:

```
             init                     close            fanout
   Idle  ─────────▶  Open  ───────────────▶  Closed ─────────▶  Idle
                      │  ▲                      (contestation      (funds
        deposits ─────┘  │                       deadline)          back
        (commit funds)   └── decommit                               on L1)
                             (withdraw without closing)
```

Key facts to hold onto:

- **Inside an open head, a transaction is final in under a second and costs
  nothing.** Every participant's node signs off on each snapshot of the
  ledger, so finality is unanimous agreement, not probabilistic settlement.
- **The L1 is touched only at the edges**: locking funds in, withdrawing
  funds out, and final settlement. That's the entire scaling argument.
- **The head is only as available as its participants** — all of them must be
  online for the head to make progress. This devnet runs three: alice, bob,
  and carol, each with their own hydra-node.

You'll see every arrow in that diagram fire for real in the sessions below.

---

## Session 0 — Setup (10 minutes)

**You need:** Docker running, the [hydra repo](https://github.com/cardano-scaling/hydra)
checked out (default path `/home/dev/claudecode/hydra`, or set `HYDRA_DEMO_DIR`),
and this project's venv installed — see the README.

Start the devnet — a private Cardano chain plus three hydra-nodes:

```bash
cd hydra-ops-mcp
./reset_devnet.sh
```

Connect the server to Claude (Claude Code shown; Claude Desktop config is in
the README):

```bash
claude mcp add hydra-ops -- "$PWD/.venv/bin/python" "$PWD/server.py"
```

**Checkpoint** — *say to Claude:*
> "What state is the Hydra head in, and what funds does alice have on layer 1?"

You should hear: head **Idle** (no head exists yet), and alice holding
~100,000 ADA on L1 (freshly seeded by the devnet). If the head is not Idle,
someone left a head open — jump to Session 5 to close it, or just re-run
`reset_devnet.sh`.

---

## Session 1 — Look around before touching anything

**Concept: the L1/L2 boundary.** Right now everything lives on layer 1.

*Say to Claude:*
> "Show me the L1 funds of alice, bob, and carol, and the protocol parameters
> the head would run with. How do the fees compare to Cardano mainnet?"

**What to notice:**
- Each party has an L1 address and UTXOs — ordinary Cardano.
- In the protocol parameters, `txFeeFixed` and `txFeePerByte` are **0**.
  A Hydra head runs its own ledger with its own parameters; this one is
  configured for free transactions. (Mainnet L1 charges ~0.17 ADA per simple
  transaction.)
- `head_status` says `Idle`: the three hydra-nodes are running and watching
  the chain, but no head exists.

**Question to test yourself:** where do the *rules* of the head's ledger come
from, if not from mainnet? (Answer: the participants agree on them at setup —
they're literally a config file the nodes share.)

---

## Session 2 — Open a head and put money in it

**Concepts: init, and deposits (incremental commits).**

*Say to Claude:*
> "Initialize a Hydra head."

Claude will show you what `init_head` would do and ask you to confirm. After
you confirm, watch what happened:

*Say to Claude:*
> "What just happened on layer 1? Check the recent events and the head status."

**What to notice:**
- The init is a real L1 transaction — the head's existence is anchored
  on-chain from the first moment.
- On this node version the head opens **immediately, empty**. Funds enter via
  **deposits**: you park a UTXO at a deposit address on L1, and the head
  absorbs it after a short deposit period. (Older versions required everyone
  to commit before the head opened; deposits made funding incremental.)

Now fund it:

*Say to Claude:*
> "Commit alice's funds into the head."

Claude will name the exact UTXO and amount before you confirm. This runs the
full flow: draft the deposit with the hydra-node, sign it with alice's key,
submit to L1, wait for absorption.

**Checkpoint:** `head_status` should show the head **Open** with ~100,000 ADA
inside, and alice's L1 wallet correspondingly lighter.

**A rule you'll thank later:** deposit **one UTXO at a time**. On hydra-node
2.3.0, multi-UTXO deposits can permanently wedge the head's final settlement
(you'll meet the error code for this in Session 6).

---

## Session 3 — Feel the speed

**Concepts: snapshots, instant finality, and the head's own ledger rules.**

*Say to Claude:*
> "Send 5 ADA from alice to bob inside the head, then 3 ADA from alice to
> carol. Time how the confirmations feel, then show me who holds what."

**What to notice:**
- Each transfer confirms in well under a second. That is not a UI trick: the
  tool waits for a **snapshot** — a new version of the head's ledger signed by
  *all three* nodes — before reporting success. When it returns, the payment
  is as final as it will ever be. There are no confirmations to wait for and
  no reorgs.
- No fees were deducted. Compare balances before and after: the sums are
  exact.

Now trigger the head's ledger rules on purpose:

*Say to Claude:*
> "Try to send 0.5 ADA from alice to bob."

The tool refuses, and the reason teaches a real constraint: the head zeroes
the minimum-UTXO rule, but **layer 1 does not** — an output below ~1 ADA could
live happily in the head and then make final settlement impossible, because
L1 would reject recreating it. Good L2 design means never creating state the
L1 can't accept back.

**Question to test yourself:** why does finality here not need proof-of-work
or stake? (Answer: the head is a closed group — unanimous signatures from all
participants *are* the consensus.)

---

## Session 4 — Take money out without closing anything

**Concept: incremental decommit.** The naive view of an L2 is "lock everything
in, do stuff, close." Decommit breaks that: one UTXO leaves, the head keeps
running.

*Say to Claude:*
> "Show me carol's UTXOs inside the head, then decommit her 3 ADA back to
> layer 1. Afterwards prove it: her L1 wallet should grow by exactly 3 ADA,
> and the head must still be open."

**What to notice:**
- The decommit is itself a head transaction (spending carol's UTXO to a
  special output) followed by an L1 transaction that releases the funds.
  Claude can show you both in `recent_events`.
- The head stays **Open** and alice/bob's balances are untouched. Other
  participants are not interrupted by carol's exit.

This is the primitive that makes long-lived heads practical: participants can
realize gains without dissolving the group.

---

## Session 5 — Close, contest, settle

**Concepts: close, the contestation period, and fanout.**

*Say to Claude:*
> "Close the head. Tell me what the contestation deadline is and why it
> exists, then fan out and show me everyone's final L1 balances."

**What to notice:**
- **Close is unilateral** — any participant can do it, and it affects
  everyone. Claude will warn you about exactly that before you confirm.
- Closing posts the *latest agreed snapshot* to L1. Then comes the
  **contestation period** (3 seconds on this devnet, hours in production):
  a window in which any participant can post a *newer* signed snapshot if the
  closer tried to settle on stale state. This is the security heart of the
  protocol — you don't have to trust the closer, only watch the chain.
- After the deadline, **fanout** distributes the head's final UTXO set back
  to layer 1, exactly as the last snapshot recorded it. Check bob's L1
  wallet: the 5 ADA from Session 3 is now real L1 money.

**Also ask:**
> "Could we have fanned out only some UTXOs?"

Claude will explain **partial fanout** — selectively settling a subset — and,
on this devnet, demonstrate something equally instructive: the pinned 2.3.0
node *rejects* the command because the feature is newer than the release. The
error message tells you exactly that. Version awareness is part of operating
any protocol.

**The full circle:** head status is back to **Idle**. Everything the group did
off-chain compressed into a handful of L1 transactions: init, one deposit,
one decommit, close, fanout. That ratio — thousands of L2 transactions to ~5
L1 footprints — is the whole point of Hydra.

---

## Session 6 — Break things and diagnose them

**Concept: operating a head when it misbehaves.** This is where this setup
beats reading docs — you have the diagnosis tools the TUI doesn't have.

*Say to Claude:*
> "Explain Hydra error code H39. When would I hit it, and what would I do
> about it?"

H39 is `FanoutUTxOHashMismatch`: the settlement transaction doesn't match
what the closed head committed to. You'll get the two real-world causes (both
discovered the hard way on this very devnet): multi-UTXO deposits on 2.3.0,
and sub-1-ADA outputs that L1 refuses to recreate. A head wedged this way
cannot settle — its funds are stuck. Every guardrail you met in Sessions 2–3
exists to prevent exactly this.

*Then say:*
> "Grep node 1's logs from the last hour for anything fanout-related and walk
> me through what each line means."

**Other things worth trying while you're here:**
- "Are there any pending deposits right now?" (`pending_deposits` — and if one
  ever gets stuck, `recover_deposit` pulls it back to L1.)
- Stop one hydra-node container (`docker compose stop hydra-node-2`) and try
  to send a payment. Watch it hang — **the head needs every participant
  online**. Restart the node and watch it recover. This is Hydra's
  availability trade-off, experienced rather than read about.
- If the devnet itself wedges (the chain stops advancing — it happens after
  long idle periods): `./reset_devnet.sh`. The devnet is disposable by design.

---

## Session 7 — Three nodes, one head

**Concept: a head is replicated, not hosted.** Every session so far talked to
node 1. But there is no server here — alice, bob and carol each run a
hydra-node that independently validates every transaction and signs every
snapshot. "The head" is what all three agree on.

Open and fund a head again (Sessions 2's prompts), then:

*Say to Claude:*
> "Compare the head status on nodes 1, 2 and 3. Do they agree on the snapshot
> number and the UTXO set?"

**What to notice:**
- All three report the same snapshot number and the same UTXO set. That
  agreement is not replication-after-the-fact — no transaction is confirmed
  until every node has signed the snapshot containing it.
- Each node reports the head from its own perspective. Ask for
  `recent_events` on node 2 and you'll see the same protocol milestones
  arriving there independently.

Now make one node do the work:

*Say to Claude:*
> "Send 2 ADA from alice to bob, but submit it through node 3. Then check
> whether nodes 1 and 2 see it."

Carol's node relays a transaction that spends alice's funds — and alice's and
bob's nodes both end up with it, because the transaction carries alice's
signature and the snapshot carries everyone's. **Which node you talk to is an
operational choice, not a trust decision.** That's the property that makes a
head genuinely peer-to-peer rather than a service one participant runs for
the others.

*Worth asking as a closer:*
> "Given everything we did, how many L1 transactions did this head actually
> require, and what would the same activity have cost on L1?"

---

## Quick reference

**State machine:** Idle → (init) → Open → (close) → Closed → (fanout) → Idle.
Deposits add funds to an open head; decommits remove them without closing.

**The 16 tools** — ask Claude "what hydra-ops tools do you have?" any time:

| Read (instant) | Write (asks first) |
|---|---|
| `head_status`, `head_utxos` | `init_head`, `commit_funds` |
| `l1_funds`, `protocol_parameters` | `send_tx`, `decommit` |
| `pending_deposits`, `recent_events` | `close_head`, `fanout`, `partial_fanout` |
| `node_logs`, `explain_error` | `recover_deposit` |

**Troubleshooting:**

| Symptom | Do this |
|---|---|
| Head not Idle at start | Session 5 to settle it, or `reset_devnet.sh` |
| Chain frozen (init/commit hangs) | `reset_devnet.sh` — known devnet wedge after idle |
| Payment hangs | A hydra-node is down; `docker compose ps` in `hydra/demo` |
| Fanout keeps failing | "Explain error H39" — the head may be wedged; reset |
| Deposit never absorbed | "Show pending deposits", then `recover_deposit` |

**Glossary:** *head* — a group's shared off-chain ledger · *snapshot* — a
ledger version signed by all participants (this is what makes L2 txs final) ·
*commit/deposit* — moving funds L1 → head · *decommit* — moving funds head →
L1 without closing · *contestation period* — the window after close where a
newer snapshot can override a stale one · *fanout* — the final settlement
distributing head state back to L1.

**Where to go next:** the [Hydra docs](https://hydra.family) for the protocol
paper and the parts this devnet doesn't exercise (multi-machine heads, real
networks, contested closes), and `hydra-ops-mcp/README.md` for the tool
reference.
