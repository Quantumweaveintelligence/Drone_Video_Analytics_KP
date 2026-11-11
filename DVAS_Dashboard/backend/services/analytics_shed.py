"""
Audit trail of analytics shedding (RunPod v2 plan R6.3).

When an analytics service falls more than DVAS_SHED_AFTER_S behind live it sheds frames
instead of queueing them (drop-oldest, lower cadence). The sampler records every shed range
in the Valkey stream `dvas:analytics_shed`; this task writes each one into the tamper-evident
audit log as `analytics_shed` (service, drone, mission stream, seq range, frames, how far
behind), so a coverage gap in the analysis is always on record. The recording on disk still
holds every frame.

A consumer group keeps the position across restarts; an entry is acknowledged only after its
audit row is written, so none is lost (at worst written twice after a crash).
"""
import asyncio
import logging
import os
from typing import Awaitable, Callable

logger = logging.getLogger("dvas")

STREAM = "dvas:analytics_shed"
GROUP = "dashboard-audit"
CONSUMER = "backend"


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _details(rec: dict) -> str:
    return (f"{rec.get('service', '?')} analytics shed {rec.get('frames', '?')} frames "
            f"(seq {rec.get('from_seq', '?')}–{rec.get('to_seq', '?')}) for drone "
            f"{rec.get('drone_id', '?')}: {rec.get('behind_s', '?')} s behind live, "
            f"{'dropped the oldest' if rec.get('reason') == 'drop_oldest' else 'reduced cadence'}. "
            f"The frames are in the mission's recording.")


async def audit_record(rec: dict, write: Callable[..., Awaitable]) -> None:
    await write(
        actor={"id": "system:analytics", "name": f"analytics:{rec.get('service', '?')}", "role": "system"},
        action="analytics_shed",
        action_type="system",
        category="analytics",
        severity="warning",
        target_type="mission_stream",
        target_id=rec.get("stream") or None,
        target_name=rec.get("drone_id") or None,
        details=_details(rec),
        after_values=rec,
        source_module="analytics",
    )


async def process_batch(r, write: Callable[..., Awaitable], block_ms: int = 5000) -> int:
    """Audit this consumer's pending entries first, then new ones. Returns entries handled."""
    done = 0
    for start, block in (("0", None), (">", block_ms)):
        res = await r.xreadgroup(GROUP, CONSUMER, {STREAM: start}, count=50, block=block)
        for _stream, entries in res or []:
            for eid, fields in entries:
                if fields:                                # a pending id whose entry was trimmed: just ack
                    await audit_record({_s(k): _s(v) for k, v in fields.items()}, write)
                await r.xack(STREAM, GROUP, eid)
                done += 1
        if done:
            break
    return done


async def _loop() -> None:
    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("redis client not installed: analytics shed ranges are not written to the audit log")
        return
    from audit_service import write_audit_v2
    url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    while True:
        try:
            r = aioredis.from_url(url)
            try:
                await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
            while True:
                await process_batch(r, write_audit_v2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("analytics shed audit: %s; retrying in 10 s", exc)
            await asyncio.sleep(10)


def start_analytics_shed_audit():
    asyncio.create_task(_loop())
