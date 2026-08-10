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
_raw_client: Optional[aioredis.Redis] = None


def get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)
    return _client


def _get_raw_client() -> aioredis.Redis:
    """Return a Redis client without decode_responses for binary-safe reads.

    The ingest server stores raw JPEG bytes in the 'frame' field alongside
    the base64-encoded 'jpeg_b64' field.  A client with decode_responses=True
    would attempt to UTF-8-decode the binary 'frame' field, raising
    ``UnicodeDecodeError``.  This raw client avoids that.
    """
    global _raw_client
    if _raw_client is None:
        _raw_client = aioredis.from_url(cfg.REDIS_URL, decode_responses=False)
    return _raw_client


async def close_client():
    global _client, _raw_client
    if _client:
        await _client.aclose()
        _client = None
    if _raw_client:
        await _raw_client.aclose()
        _raw_client = None


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

    Uses a raw (non-decoding) Redis client because the ingest server
    stores raw JPEG bytes in the 'frame' field.  Text fields are
    manually decoded; binary fields are skipped.
    """
    raw_client = _get_raw_client()
    stream_key = f"drone:frames:{drone_id}"
    results = await raw_client.xread(
        {stream_key.encode(): last_id.encode()},
        count=count,
        block=block_ms,
    )
    if not results:
        return last_id, []

    frames = []
    new_last_id = last_id
    for _stream_name, entries in results:
        for entry_id, fields in entries:
            # entry_id comes back as bytes from raw client
            eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
            new_last_id = eid
            # Decode text fields, skip binary fields (e.g. 'frame')
            decoded = {"entry_id": eid, "drone_id": drone_id}
            for k, v in fields.items():
                key = k.decode() if isinstance(k, bytes) else k
                if key == "frame":
                    # Skip raw binary JPEG — analytics uses jpeg_b64 instead
                    continue
                if isinstance(v, bytes):
                    try:
                        decoded[key] = v.decode("utf-8")
                    except UnicodeDecodeError:
                        # Binary field that isn't UTF-8 — skip it
                        continue
                else:
                    decoded[key] = v
            frames.append(decoded)

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

async def hset_vehicle_track(
    tracker_id: int,
    drone_id: str,
    data: Dict[str, Any],
    ttl: int = 300,
) -> None:
    """
    Store vehicle track info (type, colour, plate) in Redis hash.
    Key: vehicle:track:{drone_id}:{tracker_id}
    TTL: 5 minutes by default.
    """
    client = get_client()
    key = f"{cfg.VEHICLE_TRACK_KEY_PREFIX}:{drone_id}:{tracker_id}"
    serialized = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                  for k, v in data.items()}
    await client.hset(key, mapping=serialized)
    await client.expire(key, ttl)


async def hget_vehicle_track(
    tracker_id: int,
    drone_id: str,
) -> Dict[str, Any]:
    """
    Retrieve vehicle track info from Redis.
    Returns {} if not found.
    """
    client = get_client()
    key = f"{cfg.VEHICLE_TRACK_KEY_PREFIX}:{drone_id}:{tracker_id}"
    raw = await client.hgetall(key)
    if not raw:
        return {}
    result = {}
    for k, v in raw.items():
        try:
            result[k] = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            result[k] = v
    return result
