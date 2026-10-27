"""Tests for the users-file handling of dvas_ingest/ingest/scripts/setup_keycloak.py."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "setup_keycloak", ROOT / "dvas_ingest" / "ingest" / "scripts" / "setup_keycloak.py")
setup_keycloak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_keycloak)


def test_missing_users_file_skips_users(tmp_path):
    assert setup_keycloak.load_users_file("") == []
    assert setup_keycloak.load_users_file(str(tmp_path / "nope.json")) == []


def test_valid_users_file(tmp_path):
    p = tmp_path / "users.json"
    p.write_text(json.dumps([{"username": "control_001", "password": "x" * 20, "role": "control-room"}]))
    users = setup_keycloak.load_users_file(str(p))
    assert users[0]["username"] == "control_001"


@pytest.mark.parametrize("bad", [
    {"username": "u", "role": "control-room"},                 # no password
    {"username": "u", "password": "p", "role": "superuser"},    # unknown role
])
def test_invalid_users_rejected(tmp_path, bad):
    p = tmp_path / "users.json"
    p.write_text(json.dumps([bad]))
    with pytest.raises(ValueError):
        setup_keycloak.load_users_file(str(p))


def test_no_credentials_in_source():
    src = (ROOT / "dvas_ingest" / "ingest" / "scripts" / "setup_keycloak.py").read_text()
    # needles are split so this test file itself never matches the CI secret scan
    for needle in ("Operator" + "@00", "admin" + "123", "yNa0" + "AfA0", "Admin@" + "KP2025"):
        assert needle not in src


def test_gen_secrets_users_have_valid_roles(tmp_path):
    import subprocess
    script = ROOT / "deploy" / "runpod" / "bin" / "gen-secrets.sh"
    subprocess.run(["bash", str(script), str(tmp_path)], check=True, capture_output=True)
    users = setup_keycloak.load_users_file(str(tmp_path / "users.json"))
    assert {u["role"] for u in users} == set(setup_keycloak.ROLES)
    env = dict(l.split("=", 1) for l in (tmp_path / "dvas.env").read_text().splitlines()
               if l and not l.startswith("#"))
    assert len(env["FACE_VECTOR_ENCRYPTION_KEY"]) == 64
    assert all(env[k] for k in ("KC_CLIENT_SECRET", "ADMIN_CLIENT_SECRET", "DVAS_DB_PASSWORD"))
    # idempotent: a second run must not overwrite
    before = (tmp_path / "dvas.env").read_text()
    subprocess.run(["bash", str(script), str(tmp_path)], check=True, capture_output=True)
    assert (tmp_path / "dvas.env").read_text() == before
