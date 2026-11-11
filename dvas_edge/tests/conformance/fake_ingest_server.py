"""
Fake ingest server for the uplink contract v1 (§3). Used by the edge tests; the same
fixtures are used by the server-side tests (feature/ingest-v2) so both sides agree.

Behaviour follows the real ingest server's rules:
- Bearer token required (wrong token -> `error` + close 4401);
- only the mission's operator may stream (configurable -> close 4403);
- `ready.last_seq` is the highest seq already received for the mission (resume);
- frames with seq <= last_seq are decoded but not counted again (resume de-duplication).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from websockets.asyncio.server import serve
from websockets.http11 import Response
from websockets.datastructures import Headers

FIXTURES = Path(__file__).with_name("fixtures")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


REQUIRED_KEYS = {name: set(fixture(name)) for name in (
    "hello", "ready", "meta", "stats_edge", "bye", "auth", "stats")}


@dataclass
class Recorded:
    hellos: List[dict] = field(default_factory=list)
    metas: List[dict] = field(default_factory=list)
    ts_chunks: List[bytes] = field(default_factory=list)
    other: List[dict] = field(default_factory=list)
    order_errors: int = 0            # binary chunk that did not follow a meta
    connections: int = 0
    unique_seqs: set = field(default_factory=set)


class FakeIngestServer:
    def __init__(
        self,
        token: str = "tok",
        reject_404: bool = False,
        close_after_frames: Optional[int] = None,
        close_code: int = 1011,
        refuse_owner: bool = False,
        close_conns: int = 1,
    ) -> None:
        self.token = token
        self.reject_404 = reject_404
        self.close_after_frames = close_after_frames
        self.close_code = close_code
        self.close_conns = close_conns          # close mid-stream on the first N connections
        self.refuse_owner = refuse_owner
        self.rec = Recorded()
        self.last_seq = 0
        self.port = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Future] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ingest/video/ws"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> "FakeIngestServer":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(10), "fake server did not start"
        return self

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set_result, None)
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        self._stop = self._loop.create_future()
        async with serve(self._handler, "127.0.0.1", 0, process_request=self._process_request) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop

    def _process_request(self, connection, request):
        if self.reject_404:
            return Response(404, "Not Found", Headers(), b"not found")
        return None

    # ── protocol ──────────────────────────────────────────────────────────────

    async def _handler(self, ws) -> None:
        self.rec.connections += 1
        auth = ws.request.headers.get("Authorization", "")
        hello = json.loads(await ws.recv())
        self.rec.hellos.append(hello)
        if auth != f"Bearer {self.token}":
            await ws.send(json.dumps({"type": "error", "code": "unauthorized", "detail": "bad token"}))
            await ws.close(4401, "unauthorized")
            return
        if self.refuse_owner:
            await ws.send(json.dumps(fixture("error")))
            await ws.close(4403, "not mission operator")
            return
        await ws.send(json.dumps({**fixture("ready"), "last_seq": self.last_seq, "t_server": time.time()}))
        frames_this_conn = 0
        expect_binary = False
        async for msg in ws:
            if isinstance(msg, bytes):
                if not expect_binary:
                    self.rec.order_errors += 1
                self.rec.ts_chunks.append(msg)
                expect_binary = False
                continue
            body = json.loads(msg)
            kind = body.get("type")
            if kind == "meta":
                self.rec.metas.append(body)
                expect_binary = True
                if body["seq"] > self.last_seq:
                    self.rec.unique_seqs.add(body["seq"])
                    self.last_seq = body["seq"]
                frames_this_conn += 1
                if self.close_after_frames and frames_this_conn >= self.close_after_frames and self.rec.connections <= self.close_conns:
                    await ws.close(self.close_code, "test close")
                    return
            elif kind == "auth":
                self.rec.other.append(body)
                await ws.send(json.dumps({**fixture("auth_ok"), "exp": int(time.time()) + 3600}))
            else:
                self.rec.other.append(body)
