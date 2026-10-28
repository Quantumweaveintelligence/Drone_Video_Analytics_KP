"""
edge_sender.py
==============
Operator Laptop Client — Kerala Police Drone Ingest System (Phase 1)

Authenticates with Keycloak, reads frames from camera or disk, and
sends them securely to the ingest server over HTTPS.

Usage:
    # Install: pip install httpx
    # Edit INGEST_URL, TOKEN_URL, CLIENT_ID, CLIENT_SEC, USERNAME, PASSWORD

    python edge_sender.py                          # sends a single test frame
    python edge_sender.py --loop --fps 5           # continuous loop, 5 FPS
    python edge_sender.py --frame path/to/img.jpg  # send specific file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

# ── Configuration ─────────────────────────────────────────────────────────────
# LAN demo: direct HTTP to ingest server (no VPN / no TLS)
#   Ingest server LAN IP : 10.51.27.250
#   Edge laptop LAN IP   : 10.51.27.155
#
# To override at runtime without editing this file, set environment variables:
#   set INGEST_URL=http://10.51.27.250/ingest/frame
#   set TOKEN_URL=http://10.51.27.250:8080/realms/kerala-police-drone/protocol/openid-connect/token

import os

INGEST_URL  = os.getenv("INGEST__URL", os.getenv("INGEST_URL",  "http://10.123.225.251/ingest/frame"))
TOKEN_URL   = os.getenv("INGEST__TOKEN_URL", os.getenv("TOKEN_URL",   "http://10.123.225.251:8080/realms/kerala-police-drone/protocol/openid-connect/token"))
CLIENT_ID   = os.getenv("KC_CLIENT_ID",  "drone-ingest-client")
CLIENT_SEC  = os.getenv("INGEST__CLIENT_SECRET", os.getenv("KC_CLIENT_SEC", "yNa0AfA0mToUmhQHSiM2vjAyYud6NSMn"))

USERNAME    = os.getenv("EDGE_USERNAME", "operator_001")
PASSWORD    = os.getenv("EDGE_PASSWORD", "Operator@001")  # rotate after pilot
DRONE_ID    = os.getenv("DRONE_ID",      "DRONE_KL_001")
MISSION_ID  = os.getenv("MISSION_ID",    "MISSION_DEFAULT")

# TLS verification:
#   TLS_VERIFY=true  + TLS_CA_CERT=<path>  → verify against that CA cert file (recommended)
#   TLS_VERIFY=true  + TLS_CA_CERT=""      → verify against system CAs
#   TLS_VERIFY=false                       → skip verification (dev only)
# Python's httpx does NOT read the Windows certificate store automatically,
# so always supply TLS_CA_CERT when the server uses a private / self-signed CA.
TLS_VERIFY  = os.getenv("TLS_VERIFY",  "false").lower() == "true"
TLS_CA_CERT = os.getenv("TLS_CA_CERT", "")   # path to root_ca.crt


def _tls_param():
    """
    Return the correct value for httpx.AsyncClient(verify=...).

    - If TLS_VERIFY is False  → False  (skip all TLS checks)
    - If TLS_VERIFY is True and TLS_CA_CERT points to a file → that file path
    - If TLS_VERIFY is True and TLS_CA_CERT is empty          → True (system CAs)
    """
    if not TLS_VERIFY:
        return False
    if TLS_CA_CERT and Path(TLS_CA_CERT).exists():
        return TLS_CA_CERT
    return True

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("edge_sender")


class IngestClient:
    """
    Authenticated HTTP client for the drone ingest server.

    Handles:
    - Keycloak token acquisition and auto-refresh (30 s before expiry)
    - Multipart frame upload with metadata JSON
    - Retry on 5xx errors (up to 3 attempts)
    """

    def __init__(
        self,
        drone_id:   str,
        mission_id: str,
        username:   str,
        password:   str,
    ) -> None:
        if drone_id:
            import re
            cleaned_drone = re.sub(r"[^A-Z0-9_\-]", "-", str(drone_id).upper())
            if len(cleaned_drone) > 30:
                cleaned_drone = cleaned_drone[:30].rstrip("-_")
            self.drone_id = cleaned_drone
        else:
            self.drone_id = "DRONE_KL_001"

        self.mission_id    = mission_id
        self.username      = username
        self.password      = password
        self.token:        Optional[str] = None
        self.token_expiry: float         = 0.0
        self.frame_seq:    int           = 0
        # Persistent HTTP client — avoids TCP+TLS handshake per frame
        self._http_client: Optional[httpx.AsyncClient] = None

    # ── Token management ──────────────────────────────────────────────────────

    async def _acquire_token(self) -> str:
        """
        Fetch a new token from Keycloak using the Resource Owner Password
        Credentials Grant (direct access).
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "client_id":     CLIENT_ID,
                    "client_secret": CLIENT_SEC,
                    "username":      self.username,
                    "password":      self.password,
                    "grant_type":    "password",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self.token        = data["access_token"]
        self.token_expiry = time.time() + data.get("expires_in", 300)
        log.info(
            "token_acquired",
            extra={"expires_in_s": data.get("expires_in"), "user": self.username},
        )
        return self.token

    async def get_token(self) -> str:
        """Return a valid token, refreshing 30 seconds before expiry."""
        if self.token and time.time() < self.token_expiry - 30:
            return self.token
        return await self._acquire_token()

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the persistent HTTP client, creating it on first use."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                verify=_tls_param(),
                timeout=15.0,
            )
        return self._http_client

    async def close(self) -> None:
        """Close the persistent HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.close()
            self._http_client = None

    # ── Frame upload ──────────────────────────────────────────────────────────

    async def send_frame(
        self,
        frame_path:   str,
        change_score: float,
        lat:          Optional[float] = None,
        lon:          Optional[float] = None,
        alt:          Optional[float] = None,
        retries:      int = 3,
    ) -> dict:
        """
        Send a JPEG/PNG frame to the ingest server.

        Args:
            frame_path:   Path to the image file on disk.
            change_score: Edge change-detection score [0.0–1.0].
            lat/lon/alt:  Optional GPS telemetry.
            retries:      Number of retry attempts on 5xx.

        Returns:
            Response JSON dict on success.

        Raises:
            httpx.HTTPStatusError on non-retryable errors.
        """
        token = await self.get_token()
        self.frame_seq += 1

        meta = {
            "drone_id":       self.drone_id,
            "mission_id":     self.mission_id,
            "timestamp":      time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "frame_sequence": self.frame_seq,
            "change_score":   change_score,
            "latitude":       lat,
            "longitude":      lon,
            "altitude_m":     alt,
        }

        frame_bytes = Path(frame_path).read_bytes()
        last_exc: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                client = await self._get_client()
                resp = await client.post(
                    INGEST_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    files={"frame": ("frame.jpg", frame_bytes, "image/jpeg")},
                    data={"metadata_json": json.dumps(meta)},
                )

                if resp.status_code < 500:
                    # Client errors (4xx) — do not retry
                    if resp.is_error:
                        log.error(f"ingest_frame_client_error status={resp.status_code} body={resp.text}")
                    resp.raise_for_status()
                    data = resp.json()
                    log.info(
                        "frame_sent",
                        extra={
                            "drone":    self.drone_id,
                            "seq":      self.frame_seq,
                            "score":    change_score,
                            "status":   resp.status_code,
                            "msg_id":   data.get("message_id", ""),
                        },
                    )
                    return data
                else:
                    log.warning(
                        "server_error_retrying",
                        extra={"attempt": attempt, "status": resp.status_code},
                    )
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                log.warning(
                    f"send_failed_retrying attempt={attempt} error={exc}",
                    extra={"attempt": attempt, "error": str(exc)},
                )
                await asyncio.sleep(0.1)   # fast retry instead of exponential backoff

        raise RuntimeError(f"Failed after {retries} attempts: {last_exc}")

    async def send_frame_bytes(
        self,
        frame_bytes:  bytes,
        change_score: float,
        lat:          Optional[float] = None,
        lon:          Optional[float] = None,
        alt:          Optional[float] = None,
        speed_mps:    Optional[float] = None,
        battery_pct:  Optional[float] = None,
        frames_total: Optional[int]   = None,
        abs_alt:      Optional[float] = None,
        gb_yaw:       Optional[float] = None,
        gb_pitch:     Optional[float] = None,
        gb_roll:      Optional[float] = None,
        focal_len:    Optional[float] = None,
        dzoom_ratio:  Optional[float] = None,
        retries:      int = 3,
    ) -> dict:
        """
        Send raw JPEG bytes to the ingest server (no file path needed).

        This is the primary method used by EdgeSenderThread in main.py.
        The frame bytes come from SelectedFrame.encode_jpeg(), so no disk
        I/O is needed.

        Args:
            frame_bytes:  Raw JPEG bytes to send.
            change_score: Edge change-detection score [0.0–1.0].
            lat/lon/alt:  Optional GPS telemetry. alt = rel_alt (altitude_m).
            speed_mps:    Optional drone speed in meters per second.
            battery_pct:  Optional drone battery percentage.
            frames_total: Optional total frames captured by the edge so far.
            abs_alt:      Optional absolute altitude from SRT metadata.
            gb_yaw:       Optional gimbal yaw from SRT metadata.
            gb_pitch:     Optional gimbal pitch from SRT metadata.
            gb_roll:      Optional gimbal roll from SRT metadata.
            focal_len:    Optional focal length from SRT metadata.
            dzoom_ratio:  Optional digital zoom ratio from SRT metadata.
            retries:      Number of retry attempts on 5xx.

        Returns:
            Response JSON dict on success.
        """
        token = await self.get_token()
        self.frame_seq += 1

        meta = {
            "drone_id":       self.drone_id,
            "mission_id":     self.mission_id,
            "timestamp":      time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "frame_sequence": self.frame_seq,
            "change_score":   change_score,
            "latitude":       lat,
            "longitude":      lon,
            "altitude_m":     alt,
            "speed_mps":      speed_mps,
            "battery_pct":    battery_pct,
            "frames_total":   frames_total,
            "abs_alt":        abs_alt,
            "gb_yaw":         gb_yaw,
            "gb_pitch":       gb_pitch,
            "gb_roll":        gb_roll,
            "focal_len":      focal_len,
            "dzoom_ratio":    dzoom_ratio,
            "frame_seq":     self.frame_seq,
            "capture_ts":    time.time(),
        }

        last_exc: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                client = await self._get_client()
                resp = await client.post(
                    INGEST_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    files={"frame": ("frame.jpg", frame_bytes, "image/jpeg")},
                    data={"metadata_json": json.dumps(meta)},
                )

                if resp.status_code < 500:
                    if resp.is_error:
                        log.error(f"ingest_frame_client_error status={resp.status_code} body={resp.text}")
                    resp.raise_for_status()
                    data = resp.json()
                    log.info(
                        "frame_sent",
                        extra={
                            "drone":    self.drone_id,
                            "seq":      self.frame_seq,
                            "score":    change_score,
                            "status":   resp.status_code,
                            "msg_id":   data.get("message_id", ""),
                        },
                    )
                    return data
                else:
                    log.warning(
                        "server_error_retrying",
                        extra={"attempt": attempt, "status": resp.status_code},
                    )
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                log.warning(
                    f"send_failed_retrying attempt={attempt} error={exc}",
                    extra={"attempt": attempt, "error": str(exc)},
                )
                await asyncio.sleep(0.1)

        raise RuntimeError(f"Failed after {retries} attempts: {last_exc}")


    # ── Mission lifecycle ─────────────────────────────────────────────────────

    async def start_mission(
        self,
        mission_name: Optional[str] = None,
        location:     Optional[str] = None,
        operator:     Optional[str] = None,
        geofence:     Optional[dict] = None,
    ) -> None:
        """Register mission start with the ingest server, optionally including geofence."""
        mission_start_url = os.getenv(
            "INGEST__MISSION_START_URL",
            os.getenv("MISSION_START_URL", "http://10.123.225.251/ingest/mission/start")
        )
        token = await self.get_token()
        payload = {
            "drone_id":     self.drone_id,
            "mission_id":   self.mission_id,
            "operator":     operator or self.username,
        }
        if mission_name:
            payload["mission_name"] = mission_name
        if location:
            payload["location"] = location
        if geofence:
            payload["geofence"] = geofence
        client = await self._get_client()
        resp = await client.post(
            mission_start_url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        resp.raise_for_status()
        log.info("mission_started", extra={"mission": self.mission_id, "has_geofence": bool(geofence)})

    async def send_geofence_update(self, geofence: dict) -> None:
        """Send mid-flight updated geofence to the ingest server."""
        base_url = os.getenv(
            "INGEST__MISSION_START_URL",
            os.getenv("MISSION_START_URL", "http://10.123.225.251/ingest/mission/start")
        )
        geofence_url = base_url.replace("/start", "/geofence")
        token = await self.get_token()
        payload = {
            "drone_id":   self.drone_id,
            "mission_id": self.mission_id,
            "geofence":   geofence,
        }
        client = await self._get_client()
        resp = await client.post(
            geofence_url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        resp.raise_for_status()
        log.info("geofence_updated_sent", extra={"mission": self.mission_id})

    async def end_mission(self) -> None:
        """Register mission end with the ingest server."""
        mission_end_url = os.getenv(
            "INGEST__MISSION_END_URL",
            os.getenv("MISSION_END_URL", "http://10.123.225.251/ingest/mission/end")
        )
        token = await self.get_token()
        client = await self._get_client()
        resp = await client.post(
            mission_end_url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "drone_id":    self.drone_id,
                "mission_id":  self.mission_id,
                "frames_sent": self.frame_seq,
            },
        )
        resp.raise_for_status()
        log.info(
            "mission_ended",
            extra={"mission": self.mission_id, "frames": self.frame_seq},
        )


# ── CLI entry points ──────────────────────────────────────────────────────────

async def run_single(args: argparse.Namespace) -> None:
    """Send a single frame and exit."""
    client = IngestClient(
        drone_id   = args.drone_id,
        mission_id = args.mission_id,
        username   = args.username,
        password   = args.password,
    )
    await client.start_mission()
    result = await client.send_frame(
        frame_path   = args.frame,
        change_score = args.score,
        lat          = args.lat,
        lon          = args.lon,
        alt          = args.alt,
    )
    print(json.dumps(result, indent=2))
    await client.end_mission()
    await client.close()


async def run_loop(args: argparse.Namespace) -> None:
    """
    Continuous loop — sends the same test frame at --fps rate.
    Useful for load testing or simulating a live drone feed.
    """
    client = IngestClient(
        drone_id   = args.drone_id,
        mission_id = args.mission_id,
        username   = args.username,
        password   = args.password,
    )
    await client.start_mission()

    interval = 1.0 / max(args.fps, 0.1)
    log.info(f"Starting loop at {args.fps} FPS (interval {interval:.3f}s). Ctrl-C to stop.")

    try:
        while True:
            t0 = time.monotonic()
            try:
                await client.send_frame(
                    frame_path   = args.frame,
                    change_score = args.score,
                    lat          = args.lat,
                    lon          = args.lon,
                    alt          = args.alt,
                )
            except Exception as exc:
                log.error("frame_error", extra={"error": str(exc)})

            elapsed = time.monotonic() - t0
            sleep   = max(0.0, interval - elapsed)
            await asyncio.sleep(sleep)

    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("loop_stopped")
    finally:
        await client.end_mission()
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kerala Police Drone Ingest — Edge Sender Client"
    )
    parser.add_argument("--frame",      default="test_frame.jpg",
                        help="Path to JPEG image to send (default: test_frame.jpg)")
    parser.add_argument("--drone-id",   default=DRONE_ID)
    parser.add_argument("--mission-id", default=MISSION_ID)
    parser.add_argument("--username",   default=USERNAME)
    parser.add_argument("--password",   default=PASSWORD)
    parser.add_argument("--score",      type=float, default=0.75,
                        help="Change score [0.0–1.0]")
    parser.add_argument("--lat",        type=float, default=None)
    parser.add_argument("--lon",        type=float, default=None)
    parser.add_argument("--alt",        type=float, default=None)
    parser.add_argument("--loop",       action="store_true",
                        help="Send frames continuously")
    parser.add_argument("--fps",        type=float, default=5.0,
                        help="Target FPS in loop mode (default: 5)")

    args = parser.parse_args()

    # Suppress SSL warnings (self-signed cert)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if args.loop:
        asyncio.run(run_loop(args))
    else:
        asyncio.run(run_single(args))


if __name__ == "__main__":
    main()
