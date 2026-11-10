# -*- coding: utf-8 -*-
"""
DVAS Analytics — which Valkey stream holds a drone's frames (plan Phase 2a).

Ingest writes every mission to its own stream, so two flights that use the same drone id
can never mix frames, and a drone's next mission never continues the last one's tracks:

  mission:frames:{sid}            frames of one mission (sid = mission id, safe characters)
  mission:index:{sid}             HASH drone_id, mission_id, operator_sub, status, started_at, ended_at
  drone:missions:{drone_id}       ZSET sid → started_at (missions whose stream still exists)
  drone:active_mission:{drone_id} sid of the drone's newest mission
  mission:video:{sid}             HASH of a video mission (v2): decoder_done=1 once every
                                  recorded frame was fed to the stream

Analytics keep the drone id as their key (trackers, stats, results); this module maps a
drone to its streams. Legacy `drone:frames:{drone_id}` streams (written before the upgrade,
or with STREAM_KEY_MODE=drone) are still read, before any mission stream.

STREAM_KEY_MODE=drone is the rollback switch: only the legacy per-drone stream is read.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import List, Optional

log = logging.getLogger(__name__)

MODE = (os.getenv("STREAM_KEY_MODE", "mission") or "mission").strip().lower()
if MODE not in ("mission", "drone"):
    log.warning("STREAM_KEY_MODE=%r is not 'mission' or 'drone' — using 'mission'", MODE)
    MODE = "mission"

_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")
# An ended (draining) mission still takes the edge's in-flight retries for a few seconds;
# its stream is read that long before analytics move on to the drone's next mission.
try:
    SWITCH_AFTER_S = max(0.0, float(os.getenv("DVAS_MISSION_SWITCH_S", "5")))
except ValueError:
    SWITCH_AFTER_S = 5.0


def sid_of(mission_id: str) -> str:
    return _UNSAFE.sub("_", mission_id)


def legacy_key(drone_id: str) -> str:
    return f"drone:frames:{drone_id}"


def mission_key(sid: str) -> str:
    return f"mission:frames:{sid}"


def missions_key(drone_id: str) -> str:
    return f"drone:missions:{drone_id}"


def active_key(drone_id: str) -> str:
    return f"drone:active_mission:{drone_id}"


def index_key(sid: str) -> str:
    return f"mission:index:{sid}"


def video_key(sid: str) -> str:
    return f"mission:video:{sid}"


def _s(v) -> str:
    return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)


def sid_of_key(key: str) -> Optional[str]:
    return key.split("mission:frames:", 1)[1] if key.startswith("mission:frames:") else None


async def drone_streams(client, drone_id: str, mode: Optional[str] = None) -> List[str]:
    """The drone's frame streams that exist, oldest first (legacy, then missions by start)."""
    mode = mode or MODE
    out = []
    if await client.exists(legacy_key(drone_id)):
        out.append(legacy_key(drone_id))
    if mode == "mission":
        for sid in await client.zrange(missions_key(drone_id), 0, -1):
            out.append(mission_key(_s(sid)))
    return out


async def is_live(client, key: str, now: Optional[float] = None) -> bool:
    """A mission stream that may still receive frames: status live, a video mission whose
    decoder is not done, or draining for less than SWITCH_AFTER_S. Legacy streams count as live in drone mode (written forever) and
    as finished in mission mode (ingest no longer writes them)."""
    sid = sid_of_key(key)
    if sid is None:
        return MODE == "drone"
    status, ended_at = await client.hmget(index_key(sid), ["status", "ended_at"])
    status = _s(status) if status is not None else ""
    if status == "live":
        return True
    # A video mission (v2 R1) is fed from its recording at the analytics' pace, possibly
    # long after the mission ended: it takes frames until its decoder is done.
    done = await client.hget(video_key(sid), "decoder_done")
    if done is not None and _s(done) != "1" and status != "ended":
        return True
    if status != "draining":
        return False
    try:
        ended = float(_s(ended_at))
    except (TypeError, ValueError):
        return False
    return (time.time() if now is None else now) - ended < SWITCH_AFTER_S


async def latest_stream(client, drone_id: str, mode: Optional[str] = None) -> Optional[str]:
    """Stream holding the drone's newest frames: active mission, else newest mission, else legacy."""
    mode = mode or MODE
    if mode == "mission":
        sid = await client.get(active_key(drone_id))
        if sid and await client.exists(mission_key(_s(sid))):
            return mission_key(_s(sid))
        newest = await client.zrange(missions_key(drone_id), -1, -1)
        if newest and await client.exists(mission_key(_s(newest[0]))):
            return mission_key(_s(newest[0]))
    if await client.exists(legacy_key(drone_id)):
        return legacy_key(drone_id)
    return None


async def _scan(client, pattern: str) -> List[str]:
    keys, cursor = [], 0
    for _ in range(200):
        cursor, batch = await client.scan(cursor=cursor, match=pattern, count=200)
        keys.extend(_s(k) for k in batch)
        if not cursor or int(cursor) == 0:
            break
    return keys


async def discover_drones(client, mode: Optional[str] = None) -> List[str]:
    """Drone ids with frames waiting: mission registry entries and non-empty legacy streams."""
    mode = mode or MODE
    found: List[str] = []
    if mode == "mission":
        for key in await _scan(client, "drone:missions:*"):
            try:
                if await client.zcard(key):
                    found.append(key.split(":", 2)[2])
            except Exception:
                continue
    for key in await _scan(client, "drone:frames:*"):
        drone = key.split(":", 2)[2] if key.count(":") >= 2 else ""
        if not drone or drone in found:
            continue
        try:
            if await client.xlen(key) > 0:
                found.append(drone)
        except Exception:
            found.append(drone)
    return found
