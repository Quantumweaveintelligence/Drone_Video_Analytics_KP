"""
endpoints.py
============
All ingest-server URLs derived from one base URL (the RunPod / ingest host).

The operator enters a single URL (installer page or edge Settings), for example
    https://<pod>-80.proxy.runpod.net
    https://<pod>-80.proxy.runpod.net/ingest
    https://<pod>-80.proxy.runpod.net/ingest/frame
and every endpoint the edge needs is built from its scheme and host.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Optional
from urllib.parse import urlsplit

KEYCLOAK_REALM = "kerala-police-drone"
_SUFFIXES = ("/ingest/frame", "/ingest")


class InvalidIngestURL(ValueError):
    """The entered URL cannot be used as an ingest base URL."""


def normalize_base(url: str, allow_http: bool = False) -> str:
    """Return scheme://host[:port] for an entered ingest URL, or raise InvalidIngestURL.

    allow_http: True only for the install configuration (edge.env), never for edited settings."""
    raw = (url or "").strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise InvalidIngestURL("URL must start with http:// or https://")
    if parts.scheme == "http" and not allow_http and os.getenv("DVAS_ALLOW_HTTP_INGEST", "false").lower() != "true":
        raise InvalidIngestURL("https:// is required (plain http only on LAN installs with DVAS_ALLOW_HTTP_INGEST=true)")
    if not parts.hostname:
        raise InvalidIngestURL("URL has no host")
    if parts.username or parts.password:
        raise InvalidIngestURL("URL must not contain a user name or password")
    if parts.query or parts.fragment:
        raise InvalidIngestURL("URL must not contain ? or #")
    path = parts.path.rstrip("/")
    for suffix in _SUFFIXES:
        if path.lower().endswith(suffix):
            path = path[: -len(suffix)]
            break
    if path:
        raise InvalidIngestURL(f"unexpected path {path!r}; enter the server address, e.g. https://<pod>-80.proxy.runpod.net")
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class IngestEndpoints:
    base: str
    frame: str
    mission_start: str
    mission_end: str
    mission_geofence: str
    token: str
    video_ws: str
    cfs_events: str

    @property
    def tls(self) -> bool:
        return self.base.startswith("https://")

    @classmethod
    def from_base(cls, url: str, allow_http: bool = False) -> "IngestEndpoints":
        base = normalize_base(url, allow_http)
        ws_base = "wss://" + base[len("https://"):] if base.startswith("https://") else "ws://" + base[len("http://"):]
        return cls(
            base=base,
            frame=f"{base}/ingest/frame",
            mission_start=f"{base}/ingest/mission/start",
            mission_end=f"{base}/ingest/mission/end",
            mission_geofence=f"{base}/ingest/mission/geofence",
            token=f"{base}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
            video_ws=f"{ws_base}/ingest/video/ws",
            cfs_events=f"{base}/ingest/cfs_events",
        )

    @classmethod
    def from_env(cls) -> Optional["IngestEndpoints"]:
        """INGEST__BASE_URL, else the v1 key INGEST__URL (…/ingest/frame). None when neither is set."""
        url = os.getenv("INGEST__BASE_URL") or os.getenv("INGEST__URL") or os.getenv("INGEST_URL")
        if not url:
            return None
        endpoints = cls.from_base(url, allow_http=True)
        token = os.getenv("INGEST__TOKEN_URL")   # LAN installs reach Keycloak directly on :8080
        return replace(endpoints, token=token) if token else endpoints
