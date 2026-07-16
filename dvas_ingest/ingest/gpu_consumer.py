"""
gpu_consumer.py  (Redis Streams edition)
=========================================
GPU Batch Consumer — Kerala Police Drone Ingest System (Phase 1)

Reads frames from Redis Streams (one per drone), batches them for GPU
inference, and publishes confirmed events back to the events stream.

Environment variables:
  REDIS_URL    — Redis connection URL  (default: redis://redis:6379/0)
  DRONE_IDS    — Comma-separated drone IDs to consume from
  BATCH_SIZE   — GPU inference batch size (default: 16)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import redis.asyncio as aioredis

# ── Configuration ─────────────────────────────────────────────────────────────
# Valkey is a BSD-3-Clause drop-in Redis replacement — wire-protocol identical.
# The redis:// scheme is correct even when connecting to Valkey.
REDIS_URL = os.getenv("REDIS_URL", "redis://valkey:6379/0")

_drone_ids_env = os.getenv(
    "DRONE_IDS",
    "DRONE_KL_001,DRONE_KL_002,DRONE_KL_003,DRONE_KL_004,DRONE_KL_005"
)
DRONE_IDS = [d.strip() for d in _drone_ids_env.split(",") if d.strip()]

BATCH_SIZE      = int(os.getenv("BATCH_SIZE",      "16"))
BATCH_TIMEOUT_S = float(os.getenv("BATCH_TIMEOUT_S", "2.0"))
EVENTS_STREAM   = "drone:events:confirmed"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("gpu_consumer")


# ── Frame payload ─────────────────────────────────────────────────────────────
@dataclass
class FramePayload:
    drone_id:   str
    entry_id:   str          # Redis stream entry ID
    jpeg_bytes: bytes
    metadata:   dict
    received_at: float = field(default_factory=time.time)


# ── Batch accumulator ─────────────────────────────────────────────────────────
class BatchAccumulator:
    def __init__(self, size: int, timeout_s: float):
        self._size      = size
        self._timeout_s = timeout_s
        self._batch:    List[FramePayload] = []
        self._deadline: float = time.monotonic() + timeout_s

    def add(self, frame: FramePayload) -> None:
        self._batch.append(frame)

    def is_ready(self) -> bool:
        return (
            len(self._batch) >= self._size
            or time.monotonic() >= self._deadline
        )

    def flush(self) -> List[FramePayload]:
        batch, self._batch = self._batch, []
        self._deadline = time.monotonic() + self._timeout_s
        return batch

    def __len__(self) -> int:
        return len(self._batch)


# ── Stub GPU analytics ────────────────────────────────────────────────────────
async def run_gpu_inference(batch: List[FramePayload]) -> List[dict]:
    """
    Placeholder GPU analytics.
    Replace with your actual model inference (YOLOv8, DeepSORT, etc.)
    """
    await asyncio.sleep(0.01)   # simulate GPU time
    results = []
    for fp in batch:
        results.append({
            "drone_id":       fp.drone_id,
            "entry_id":       fp.entry_id,
            "timestamp":      fp.metadata.get("timestamp", ""),
            "frame_sequence": fp.metadata.get("frame_sequence", 0),
            "detections":     [],   # TODO: real model output
            "confirmed":      True,
            "processed_at":   time.time(),
        })
    return results


# ── Consumer ──────────────────────────────────────────────────────────────────
class DroneFrameConsumer:
    def __init__(self, redis: aioredis.Redis):
        self._redis     = redis
        self._batch     = BatchAccumulator(BATCH_SIZE, BATCH_TIMEOUT_S)
        self._processed = 0
        self._errors    = 0
        # Track last-seen entry IDs per stream (for XREAD)
        self._last_ids: Dict[str, str] = {
            f"drone:frames:{did}": "0-0" for did in DRONE_IDS
        }

    async def run(self) -> None:
        log.info("GPU consumer starting | drones=%s batch_size=%d", DRONE_IDS, BATCH_SIZE)
        stream_keys = list(self._last_ids.keys())

        while True:
            try:
                # Read new entries from all drone streams (non-blocking with 500ms block)
                results = await self._redis.xread(
                    streams={k: self._last_ids[k] for k in stream_keys},
                    count=BATCH_SIZE,
                    block=500,
                )

                if results:
                    for stream_key, entries in results:
                        stream_name = stream_key.decode() if isinstance(stream_key, bytes) else stream_key
                        for entry_id_raw, fields in entries:
                            entry_id = entry_id_raw.decode() if isinstance(entry_id_raw, bytes) else entry_id_raw
                            self._last_ids[stream_name] = entry_id

                            # Skip sentinel init entries
                            if b"_init" in fields:
                                continue

                            try:
                                drone_id  = stream_name.split(":")[-1]
                                meta_raw  = fields.get(b"metadata", b"{}")
                                metadata  = json.loads(meta_raw)
                                jpeg_bytes= fields.get(b"frame", b"")

                                fp = FramePayload(
                                    drone_id   = drone_id,
                                    entry_id   = entry_id,
                                    jpeg_bytes = jpeg_bytes,
                                    metadata   = metadata,
                                )
                                self._batch.add(fp)
                            except Exception as exc:
                                log.warning("frame_parse_error entry_id=%s error=%s", entry_id, exc)
                                self._errors += 1

                # Process batch when ready
                if self._batch.is_ready() and len(self._batch) > 0:
                    await self._process_batch()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("consumer_loop_error error=%s", exc)
                await asyncio.sleep(2)

        log.info("GPU consumer stopped | processed=%d errors=%d", self._processed, self._errors)

    async def _process_batch(self) -> None:
        batch = self._batch.flush()
        if not batch:
            return

        t0 = time.monotonic()
        try:
            results = await run_gpu_inference(batch)
            elapsed = (time.monotonic() - t0) * 1000
            self._processed += len(batch)

            log.info(
                "batch_processed frames=%d latency_ms=%.1f total=%d",
                len(batch), elapsed, self._processed
            )

            # Publish confirmed events to events stream
            for r in results:
                if r.get("confirmed"):
                    await self._redis.xadd(
                        EVENTS_STREAM,
                        {b"data": json.dumps(r).encode()},
                        maxlen=500,
                        approximate=True,
                    )

        except Exception as exc:
            log.error("batch_inference_error error=%s", exc)
            self._errors += len(batch)


# ── Entry point ───────────────────────────────────────────────────────────────
async def main() -> None:
    log.info("Connecting to Redis at %s", REDIS_URL.split("@")[-1])

    redis = aioredis.from_url(
        REDIS_URL,
        encoding="utf-8",
        decode_responses=False,
        socket_connect_timeout=10,
        retry_on_timeout=True,
    )

    # Wait for Redis to be ready
    for attempt in range(30):
        try:
            await redis.ping()
            log.info("Redis connected!")
            break
        except Exception:
            log.info("Redis not ready, attempt %d/30...", attempt + 1)
            await asyncio.sleep(2)
    else:
        log.error("Could not connect to Redis after 30 attempts — exiting")
        return

    consumer = DroneFrameConsumer(redis)
    try:
        await consumer.run()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
