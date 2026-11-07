"""
DVAS Analytics — Valkey/Redis Stream Client
Reads drone frames (per-mission streams, see shared/mission_streams.py) and publishes
confirmed events.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis

from shared import mission_streams
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


async def _frames_key(client, drone_id: str) -> str:
    """Stream with the drone's newest frames (active mission, else legacy key)."""
    return (await mission_streams.latest_stream(client, drone_id)
            or mission_streams.legacy_key(drone_id))


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
    Read up to `count` frames from the drone's current stream.
    Returns (new_last_id, list_of_frame_dicts).
    Each frame dict has: entry_id, frame (raw JPEG bytes), timestamp, drone_id

    Uses a raw (non-decoding) Redis client because the ingest server
    stores raw JPEG bytes in the 'frame' field.  Text fields are
    decoded; 'frame' stays bytes.
    """
    raw_client = _get_raw_client()
    stream_key = await _frames_key(raw_client, drone_id)
    block_val = block_ms if block_ms and block_ms > 0 else None
    results = await raw_client.xread(
        {stream_key.encode(): last_id.encode()},
        count=count,
        block=block_val,
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
                    # Raw JPEG bytes (the only payload since RunPod plan 9.1);
                    # decode with shared.frame_sampler.decode_frame
                    decoded["frame"] = v if isinstance(v, bytes) else str(v).encode("latin-1")
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


async def get_latest_stream_frame(drone_id: str) -> Optional[Any]:
    """Retrieve and decode the drone's most recent frame (its active mission's stream).
    
    Checks both the binary 'frame' field and the base64 'jpeg_b64' field.
    Returns a BGR numpy image (as decoded by cv2.imdecode), or None if no frames exist.
    """
    import base64
    import cv2
    import numpy as np

    try:
        raw_client = _get_raw_client()
        stream_key = await _frames_key(raw_client, drone_id)
        entries = await raw_client.xrevrange(stream_key, max="+", min="-", count=1)
        if not entries:
            return None
        _entry_id, fields = entries[0]
        raw_bytes = fields.get(b"frame") or fields.get("frame")
        if raw_bytes:
            if isinstance(raw_bytes, str):
                raw_bytes = raw_bytes.encode("latin-1")
            nparr = np.frombuffer(raw_bytes, np.uint8)
            decoded = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if decoded is not None:
                return decoded

        b64_val = fields.get(b"jpeg_b64") or fields.get("jpeg_b64")
        if b64_val:
            if isinstance(b64_val, bytes):
                b64_val = b64_val.decode("utf-8")
            raw_bytes = base64.b64decode(b64_val)
            nparr = np.frombuffer(raw_bytes, np.uint8)
            decoded = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if decoded is not None:
                return decoded
    except Exception as exc:
        log.warning("get_latest_stream_frame failed for %s: %s", drone_id, exc)
    return None


async def xreadgroup_frames(group: str, consumer: str, drone_id: str,
                            last_id: str = ">", count: int = 1, block_ms: int = 100):
    """Read frames using consumer group. Returns list of (entry_id, fields) tuples."""
    client = _get_raw_client()
    stream_key = await _frames_key(client, drone_id)
    block_val = block_ms if block_ms and block_ms > 0 else None
    try:
        result = await client.xreadgroup(
            groupname=group, consumername=consumer,
            streams={stream_key: last_id},
            count=count, block=block_val
        )
        if not result:
            return []
        
        entries = result[0][1] if result else []
        
        # Decode entry_ids from raw client, and also decode field keys
        decoded_entries = []
        for entry_id, fields in entries:
            eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
            decoded = {"entry_id": eid, "drone_id": drone_id}
            for k, v in fields.items():
                key = k.decode() if isinstance(k, bytes) else k
                if key == "frame":
                    decoded["frame"] = v if isinstance(v, bytes) else str(v).encode("latin-1")
                    continue
                if isinstance(v, bytes):
                    try:
                        decoded[key] = v.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                else:
                    decoded[key] = v
            decoded_entries.append((eid, decoded))
            
        return decoded_entries
    except Exception as e:
        if "NOGROUP" in str(e):
            try:
                # Do NOT recreate empty streams with mkstream=True if the stream was deleted/purged
                await client.xgroup_create(stream_key, group, id="0", mkstream=False)
            except Exception:
                pass
            return []
        raise

async def ack_frames(drone_id: str, group: str, *message_ids):
    """Acknowledge processed frames in the consumer group."""
    if not message_ids:
        return
    client = get_client()
    stream_key = await _frames_key(client, drone_id)
    await client.xack(stream_key, group, *message_ids)


async def publish_confirmed_event(event: Dict[str, Any]) -> None:
    """Publish a confirmed event to drone:events:confirmed stream."""
    # Ensure default context windows and identifiers for edge buffer extraction
    event.setdefault("pre_event_s", 15)
    event.setdefault("post_event_s", 15)
    if "mission_id" not in event or not event["mission_id"]:
        event["mission_id"] = "M-DEFAULT"

    client = get_client()
    await client.xadd(
        "drone:events:confirmed",
        {"data": json.dumps(event, default=str)},
        maxlen=10000,
        approximate=True,
    )
    log.debug("Published confirmed event: %s for drone %s", event.get("event_type"), event.get("drone_id"))


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
    stream_key = await _frames_key(client, drone_id)
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
    mission_id: Optional[str] = None,
) -> None:
    """
    Store vehicle track info (type, colour, plate) in Redis hash.
    Scopes by mission_id when available to prevent cross-mission state bleed.
    TTL: 5 minutes by default.
    """
    client = get_client()
    m_id = mission_id or data.get("mission_id") or ""
    serialized = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                  for k, v in data.items()}
    # Write legacy key for compatibility
    legacy_key = f"{cfg.VEHICLE_TRACK_KEY_PREFIX}:{drone_id}:{tracker_id}"
    await client.hset(legacy_key, mapping=serialized)
    await client.expire(legacy_key, ttl)

    if m_id:
        mission_key = f"{cfg.VEHICLE_TRACK_KEY_PREFIX}:{drone_id}:{m_id}:{tracker_id}"
        await client.hset(mission_key, mapping=serialized)
        await client.expire(mission_key, ttl)


async def hget_vehicle_track(
    tracker_id: int,
    drone_id: str,
    mission_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retrieve vehicle track info from Redis.
    Checks mission-scoped key first if mission_id is provided.
    Returns {} if not found.
    """
    client = get_client()
    raw = None
    if mission_id:
        mission_key = f"{cfg.VEHICLE_TRACK_KEY_PREFIX}:{drone_id}:{mission_id}:{tracker_id}"
        raw = await client.hgetall(mission_key)

    if not raw:
        key = f"{cfg.VEHICLE_TRACK_KEY_PREFIX}:{drone_id}:{tracker_id}"
        raw = await client.hgetall(key)
        # If legacy entry belongs to a different mission, do not use it!
        if raw and mission_id:
            raw_mid = raw.get("mission_id") or raw.get(b"mission_id")
            if raw_mid:
                raw_mid_str = raw_mid.decode() if isinstance(raw_mid, bytes) else str(raw_mid)
                if raw_mid_str and raw_mid_str != mission_id:
                    return {}

    if not raw:
        return {}
    result = {}
    for k, v in raw.items():
        k_str = k.decode() if isinstance(k, bytes) else k
        try:
            result[k_str] = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            result[k_str] = v.decode() if isinstance(v, bytes) else v
    return result



async def scan_active_stream_drones() -> List[str]:
    """
    Drone ids with frames in Valkey: drones with a mission stream (drone:missions:*) and
    drones with a non-empty legacy drone:frames:* stream. Lets services pick up any drone
    id without configuration.
    """
    try:
        return await mission_streams.discover_drones(get_client())
    except Exception as exc:
        log.warning("Failed to scan active stream drones from Valkey: %s", exc)
        return []
