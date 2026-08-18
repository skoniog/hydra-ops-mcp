"""Extended WebSocket + HTTP client for the hydra-node API (operator surface).

Derived from hydra-mcp's client (devnet-proven in Projects 1-3) and extended
with the full TUI-parity command set: Decommit, PartialFanout, deposit
recovery, and the read endpoints the TUI's tabs are built on.

Async core with a synchronous facade: the client runs its own event loop in a
daemon thread so tool functions can stay synchronous. One client per node;
`get_client(node)` caches instances.
"""

import asyncio
import json
import threading

import httpx
import websockets

from config import NODES


class HydraClientError(Exception):
    pass


class HydraClient:
    def __init__(self, node: int = 1):
        if node not in NODES:
            raise HydraClientError(f"unknown node {node}; expected one of {sorted(NODES)}")
        self.node = node
        self.ws_url = NODES[node]["ws"]
        self.http_url = NODES[node]["http"]
        self._loop = None
        self._thread = None
        self._ws = None
        self._events = []
        self._head_status = "unknown"
        self._confirmed_tx_ids = set()
        self._confirmed_txs = {}
        self._event_cond = None
        self._listener_task = None

    # ------------------------------------------------------------------ sync facade

    def start(self, timeout: float = 10.0):
        if self._thread:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._call(self._connect(), timeout)

    def stop(self):
        if not self._loop:
            return
        try:
            self._call(self._disconnect(), 5.0)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5.0)
            self._loop, self._thread = None, None

    def _call(self, coro, timeout: float):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    # -- HTTP reads ----------------------------------------------------------

    def _get(self, path: str):
        r = httpx.get(f"{self.http_url}{path}", timeout=10.0)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_head(self) -> dict:
        return self._get("/head") or {}

    def get_utxos(self) -> dict:
        return self._get("/snapshot/utxo") or {}

    def get_protocol_parameters(self) -> dict:
        return self._get("/protocol-parameters") or {}

    def get_pending_deposits(self):
        return self._get("/commits") or []

    def draft_commit(self, utxo: dict, timeout: float = 60.0) -> dict:
        """POST /commit — returns a draft deposit tx to sign and submit on L1."""
        r = httpx.post(f"{self.http_url}/commit", json=utxo, timeout=timeout)
        if r.status_code != 200:
            raise HydraClientError(f"commit draft failed: {r.status_code} {r.text[:400]}")
        return r.json()

    def recover_deposit(self, tx_id: str) -> dict:
        """DELETE /commits/{txid} — recover a stuck deposit back to L1."""
        r = httpx.delete(f"{self.http_url}/commits/{tx_id}", timeout=60.0)
        if r.status_code != 200:
            raise HydraClientError(f"recover failed: {r.status_code} {r.text[:400]}")
        try:
            return r.json()
        except ValueError:
            return {"response": r.text[:400]}

    # -- state / events ------------------------------------------------------

    def get_head_status(self) -> str:
        return self._head_status

    def recent_events(self, tag: str = None, limit: int = 50) -> list:
        events = self._events
        if tag:
            events = [e for e in events if e.get("tag") == tag]
        return events[-limit:]

    # -- WebSocket commands ----------------------------------------------------

    def submit_tx(self, tx_envelope: dict, tx_id: str, timeout: float = 30.0) -> dict:
        return self._call(self._submit_tx(tx_envelope, tx_id), timeout)

    def init_head(self, timeout: float = 120.0) -> dict:
        # hydra 2.3.0 opens the head directly after Init.
        return self._call(
            self._command_and_wait({"tag": "Init"}, {"HeadIsInitializing", "HeadIsOpen"}),
            timeout,
        )

    def close_head(self, timeout: float = 120.0) -> dict:
        return self._call(self._command_and_wait({"tag": "Close"}, {"HeadIsClosed"}), timeout)

    def decommit(self, tx_envelope: dict, timeout: float = 120.0) -> dict:
        return self._call(
            self._command_and_wait(
                {"tag": "Decommit", "decommitTx": tx_envelope},
                {"DecommitFinalized", "DecommitInvalid"},
            ),
            timeout,
        )

    def fanout(self, timeout: float = 300.0) -> dict:
        return self._call(self._fanout(), timeout)

    def partial_fanout(self, utxo: dict, timeout: float = 300.0) -> dict:
        return self._call(self._partial_fanout(utxo), timeout)

    def wait_for(self, tags: set, timeout: float = 120.0) -> dict:
        return self._call(self._wait_for_event(tags), timeout)

    # ------------------------------------------------------------------ async core

    async def _connect(self):
        self._event_cond = asyncio.Condition()
        self._ws = await websockets.connect(
            f"{self.ws_url}/?history=no", max_size=16 * 1024 * 1024
        )
        self._listener_task = asyncio.ensure_future(self._listen())
        async with self._event_cond:
            await asyncio.wait_for(
                self._event_cond.wait_for(
                    lambda: any(e.get("tag") == "Greetings" for e in self._events)
                ),
                timeout=10.0,
            )

    async def _disconnect(self):
        if self._ws:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=3.0)
            except Exception:
                pass
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except Exception:
                pass

    async def _listen(self):
        async for raw in self._ws:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self._record(event)

    async def _record(self, event: dict):
        tag = event.get("tag")
        if tag == "Greetings":
            self._head_status = event.get("headStatus", "unknown").lower()
        elif tag == "HeadIsInitializing":
            self._head_status = "initializing"
        elif tag == "HeadIsOpen":
            self._head_status = "open"
        elif tag == "HeadIsClosed":
            self._head_status = "closed"
        elif tag == "ReadyToFanout":
            self._head_status = "fanout_possible"
        elif tag == "HeadPartiallyFannedOut":
            self._head_status = "fanning_out"
        elif tag == "HeadIsFinalized":
            self._head_status = "final"
        elif tag == "TxValid":
            tx = event.get("transaction") or {}
            tx_id = tx.get("txId") or event.get("txId")
            if tx_id:
                self._confirmed_txs[tx_id] = tx
        elif tag == "SnapshotConfirmed":
            snapshot = event.get("snapshot") or {}
            for tx in snapshot.get("confirmed", []) or []:
                if isinstance(tx, dict) and tx.get("txId"):
                    self._confirmed_tx_ids.add(tx["txId"])
                    self._confirmed_txs.setdefault(tx["txId"], tx)
                elif isinstance(tx, str):
                    self._confirmed_tx_ids.add(tx)
        async with self._event_cond:
            self._events.append(event)
            self._event_cond.notify_all()

    async def _send(self, payload: dict):
        await self._ws.send(json.dumps(payload))

    async def _wait_for_event(self, tags: set, since: int = 0) -> dict:
        async with self._event_cond:
            await self._event_cond.wait_for(
                lambda: any(e.get("tag") in tags for e in self._events[since:])
            )
            return next(e for e in self._events[since:] if e.get("tag") in tags)

    @staticmethod
    def _is_rejection(event: dict) -> bool:
        # CommandFailed carries a tag; an input the node cannot even parse
        # (e.g. a command this node version does not know) comes back as a
        # bare {"input": ..., "reason": ...} with NO tag field.
        return event.get("tag") in ("CommandFailed", "InvalidInput") or (
            event.get("tag") is None and "reason" in event
        )

    async def _command_and_wait(self, command: dict, ok_tags: set) -> dict:
        marker = len(self._events)
        await self._send(command)

        def _resolved(events):
            return next(
                (e for e in events if e.get("tag") in ok_tags or self._is_rejection(e)),
                None,
            )

        async with self._event_cond:
            await self._event_cond.wait_for(lambda: _resolved(self._events[marker:]) is not None)
            event = _resolved(self._events[marker:])
        if self._is_rejection(event):
            reason = event.get("reason") or json.dumps(event)[:400]
            raise HydraClientError(f"command rejected: {str(reason)[:400]}")
        return event

    async def _submit_tx(self, tx_envelope: dict, tx_id: str) -> dict:
        marker = len(self._events)
        await self._send({"tag": "NewTx", "transaction": tx_envelope})

        def _resolved(events):
            for e in events:
                tag = e.get("tag")
                if tag == "TxInvalid" and (e.get("transaction") or {}).get("txId") == tx_id:
                    return e
                if tag == "SnapshotConfirmed" and tx_id in self._confirmed_tx_ids:
                    return e
            return None

        async with self._event_cond:
            await self._event_cond.wait_for(lambda: _resolved(self._events[marker:]) is not None)
            event = _resolved(self._events[marker:])
        if event.get("tag") == "TxInvalid":
            reason = json.dumps(event.get("validationError") or event)[:500]
            raise HydraClientError(f"transaction invalid: {reason}")
        return {"tx_id": tx_id, "confirmed": True}

    async def _await_fanout_ready(self):
        # "fanning_out" counts: after a first PartialFanout the node awaits the
        # next selection and never re-emits ReadyToFanout.
        if self._head_status not in ("fanout_possible", "fanoutpossible", "fanning_out", "fanningout"):
            await self._wait_for_event({"ReadyToFanout"})

    async def _fanout(self) -> dict:
        await self._await_fanout_ready()
        return await self._command_and_wait({"tag": "Fanout"}, {"HeadIsFinalized"})

    async def _partial_fanout(self, utxo: dict) -> dict:
        await self._await_fanout_ready()
        # A partial fanout distributes the subset (HeadPartiallyFannedOut, which
        # reports distributed + remaining UTxO) and waits for further selections;
        # when the head is drained the node produces the final fanout itself
        # (HeadIsFinalized). Either observation means this call worked.
        return await self._command_and_wait(
            {"tag": "PartialFanout", "utxoToFanout": utxo},
            {"HeadPartiallyFannedOut", "HeadIsFinalized"},
        )


_clients = {}


def get_client(node: int = 1) -> HydraClient:
    if node not in _clients:
        client = HydraClient(node)
        client.start()
        _clients[node] = client
    return _clients[node]
