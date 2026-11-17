"""/ws/status accepts the dashboard backend's service account (A5) and nothing else role-less.

Run from dvas_ingest/ingest/:  python -m pytest tests/test_status_auth.py -q
"""
import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import auth  # noqa: E402

SERVICE = {"azp": "dvas-admin-service", "preferred_username": "service-account-dvas-admin-service",
           "realm_access": {"roles": ["offline_access"]}}


def _check(monkeypatch, payload, clients):
    async def jwks():
        return {}
    monkeypatch.setattr(auth, "get_jwks", jwks)
    monkeypatch.setattr(auth.jwt, "decode", lambda *a, **k: payload)
    return asyncio.run(auth.validate_token_string("t", clients))


def test_backend_service_account_reads_the_status_feed(monkeypatch):
    assert _check(monkeypatch, SERVICE, auth.STATUS_SERVICE_CLIENTS)["azp"] == "dvas-admin-service"


def test_service_account_still_needs_a_role_elsewhere(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _check(monkeypatch, SERVICE, frozenset())
    assert e.value.status_code == 403


def test_a_user_logged_in_through_that_client_needs_a_role(monkeypatch):
    user = {**SERVICE, "preferred_username": "someone"}
    with pytest.raises(HTTPException):
        _check(monkeypatch, user, auth.STATUS_SERVICE_CLIENTS)


def test_other_clients_need_a_role(monkeypatch):
    other = {**SERVICE, "azp": "dvas-dashboard", "preferred_username": "service-account-dvas-dashboard"}
    with pytest.raises(HTTPException):
        _check(monkeypatch, other, auth.STATUS_SERVICE_CLIENTS)
