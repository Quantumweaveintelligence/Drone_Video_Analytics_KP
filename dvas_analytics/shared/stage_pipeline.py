# -*- coding: utf-8 -*-
"""
DVAS Analytics — GPU/CPU stage pipelining for the ordered services (v2 plan R4.2).

A round used to run as one block: model call (GPU), tracker step, post-processing (CPU).
With DVAS_STAGE_PIPELINE=1 the model call of round N+1 runs while round N's tracker step
and post-processing run, so a round costs about max(GPU, CPU) instead of the sum.

Results are identical by construction:
  * the model call is a pure function of the frame: the same frames, the same arguments;
  * the tracker and the post-processing still take rounds strictly in order, one at a time,
    and the loop's per-round work (mission changes, resets) still happens before them;
  * a round is acknowledged only after its tracker step and post-processing. The round read
    ahead is held: a process restart re-reads it from the pending list, and a stream stop
    keeps it here for the next start (like the sampler's own prefetch), so no frame is lost.

Loop use:
    decoded = await pipe.next(drones)                  # {drone: (fields, frame)}
    ...  per-round work on `decoded` (mission changes, ...)
    results = await pipe.finish(frames, finish_fn, drones)
and `await pipe.stop()` where the loop ends (before the sampler's done()).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

ENABLED = os.getenv("DVAS_STAGE_PIPELINE", "1") != "0"


def _frames(decoded: dict) -> dict:
    return {d: fr for d, (_f, fr) in decoded.items()}


class StagePipeline:
    """infer(frames) -> handle runs in a worker thread (the model call); finish_fn(frames,
    handle) -> {drone: result} runs the tracker step and post-processing."""

    def __init__(self, sampler, infer: Callable[[dict], object]):
        self.sampler = sampler
        self.infer = infer
        self._ahead = None                    # (decoded, token, task): read ahead, not finished
        self._cur = None                      # (decoded, token, task): handed out by next()

    def _start(self, decoded: dict) -> asyncio.Task:
        task = asyncio.create_task(asyncio.to_thread(self.infer, _frames(decoded)))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())   # retrieved in finish()
        return task

    async def next(self, drones: List[str]) -> dict:
        if not ENABLED:
            return await self.sampler.next_decoded(drones)
        if self._cur is not None:             # the loop skipped finish(): as before, done with it
            await self._drop_cur()
        if self._ahead is not None:
            self._cur, self._ahead = self._ahead, None
            return self._cur[0]
        decoded, token = await self.sampler.next_decoded_held(drones)
        if not decoded:
            await self.sampler.ack_held(token)
            return decoded
        self._cur = (decoded, token, self._start(decoded))
        return decoded

    async def finish(self, frames: dict, finish_fn: Callable[[dict, object], Dict[str, dict]],
                     drones: List[str]) -> Dict[str, dict]:
        """Round handed out by next(): wait for its model call, start the next round's, then
        run its tracker step and post-processing. `frames` is what infer() was given."""
        if not ENABLED:
            handle = await asyncio.to_thread(self.infer, frames)
            return await asyncio.to_thread(finish_fn, frames, handle)
        decoded, token, task = self._cur
        self._cur = None
        try:
            handle = await task
            if set(frames) != set(decoded):   # the loop left a drone out: its own model call
                handle = await asyncio.to_thread(self.infer, frames)
            if self._ahead is None:
                nxt, ntoken = await self.sampler.next_decoded_held(drones)
                if nxt:
                    self._ahead = (nxt, ntoken, self._start(nxt))
                else:
                    await self.sampler.ack_held(ntoken)
            return await asyncio.to_thread(finish_fn, frames, handle)
        finally:
            await self.sampler.ack_held(token)

    async def _drop_cur(self) -> None:
        _decoded, token, _task = self._cur
        self._cur = None
        await self.sampler.ack_held(token)

    async def stop(self) -> None:
        """Loop ends: a round handed out but not finished is acknowledged (as before); the
        round read ahead is kept, unacknowledged, for the next start."""
        if self._cur is not None:
            await self._drop_cur()

    def reset_device(self) -> None:
        """Models moved between devices (CUDA fallback): the read-ahead model call is redone."""
        if self._ahead is not None:
            decoded, token, _task = self._ahead
            self._ahead = (decoded, token, self._start(decoded))
