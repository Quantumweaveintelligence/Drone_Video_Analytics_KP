"""
DVAS Analytics — Valkey/Redis Stream Client
Reads frames from drone:frames:{drone_id} streams and publishes confirmed events.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis

from shared.config import cfg

log = logging.getLogger(__name__)

_client: Optional[aioredis.Redis] = None


def get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)
    return _client


async def close_client():
    global _client
    if _client:
        await _client.aclose()
        _client = None


async def xread_frames(
    drone_id: str,
    last_id: str = "0",
    count: int = 16,
    block_ms: int = 5000,
) -> Tuple[str, List[Dict]]:
    """
    Read up to `count` frames from drone:frames:{drone_id}.
    Returns (new_last_id, list_of_frame_dicts).
    Each frame dict has: frame_id, jpeg_b64, timestamp, drone_id
    """
    client = get_client()
    stream_key = f"drone:frames:{drone_id}"
    results = await client.xread(
        {stream_key: last_id},
        count=count,
        block=block_ms,
    )
    if not results:
        return last_id, []

    frames = []
    new_last_id = last_id
    for _stream_name, entries in results:
        for entry_id, fields in entries:
            new_last_id = entry_id
            frames.append({
                "entry_id": entry_id,
                "drone_id": drone_id,
                **fields,
            })

    return new_last_id, frames


async def publish_confirmed_event(event: Dict[str, Any]) -> None:
    """Publish a confirmed event to drone:events:confirmed stream."""
    client = get_client()
    await client.xadd(
        "drone:events:confirmed",
        {"data": json.dumps(event, default=str)},
        maxlen=10000,
        approximate=True,
    )
    log.debug("Published confirmed event: %s", event.get("event_type"))


async def publish_analytics_result(service: str, result: Dict[str, Any]) -> None:
    """Publish analytics results to per-service stream for dashboard consumption."""
    client = get_client()
    stream_key = f"analytics:{service}:results"
    await client.xadd(
        stream_key,
        {"data": json.dumps(result, default=str)},
        maxlen=5000,
        approximate=True,
    )


async def get_stream_info(drone_id: str) -> Dict:
    """Get info about a drone's frame stream."""
    client = get_client()
    stream_key = f"drone:frames:{drone_id}"
    try:
        info = await client.xinfo_stream(stream_key)
        return {
            "stream_key": stream_key,
            "length": info.get("length", 0),
            "first_entry_id": info.get("first-entry", [None])[0],
            "last_entry_id": info.get("last-entry", [None])[0],
        }
    except Exception:
        return {"stream_key": stream_key, "length": 0, "status": "not_found"}
