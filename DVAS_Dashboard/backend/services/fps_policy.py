"""
Analytics FPS policy and live GPU throughput (RunPod plan Phase 10).

The policy (target min / max / floor) is stored with the other run-time settings
(services/settings_service.py) and pushed to the analytics gateway, whose FPS
governor applies it. The live status comes from the gateway's governor document.
"""
import asyncio
import logging
from typing import Optional

from core.analytics_client import _analytics_get, _analytics_put
from core.config import ANALYTICS_BASE
from services import settings_service

logger = logging.getLogger("dvas.fps_policy")


async def current() -> dict:
    v = await settings_service.get_all()
    return {"target_min": v["analytics_fps_min"], "target_max": v["analytics_fps_max"],
            "floor": v["analytics_fps_floor"]}


async def push(policy: Optional[dict] = None) -> bool:
    """Send the stored policy to the gateway. False when the gateway is unreachable."""
    policy = policy or await current()
    res = await _analytics_put(f"{ANALYTICS_BASE}/governor/policy", policy, timeout=5.0, soft=True)
    if "error" in res:
        logger.warning("FPS policy push failed: %s", res.get("detail"))
        return False
    return True


async def status() -> dict:
    """Governor document from the gateway, or {"error": ...} when unavailable."""
    return await _analytics_get(f"{ANALYTICS_BASE}/governor/status", timeout=5.0, soft=True)


def in_sync(stored: dict, gw_status: dict) -> bool:
    gw = (gw_status or {}).get("policy") or {}
    try:
        return all(float(gw.get(k, -1)) == float(stored[k]) for k in stored)
    except (TypeError, ValueError):
        return False


async def push_on_startup(delay_s: float = 8.0, attempts: int = 10) -> None:
    """The gateway may start after the backend; retry until the policy lands."""
    await asyncio.sleep(delay_s)
    for i in range(attempts):
        try:
            if await push():
                return
        except Exception as exc:
            logger.debug("FPS policy push attempt %d failed: %s", i + 1, exc)
        await asyncio.sleep(min(60, 5 * (i + 1)))
