"""Tests for deploy/runpod/tools/sanitize_realm.py.  Run: python -m pytest deploy/runpod/tests -q"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sanitize_realm", HERE.parent / "tools" / "sanitize_realm.py")
sanitize_realm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sanitize_realm)

RAW = {
    "realm": "kerala-police-drone",
    "roles": {"realm": [{"name": "sys-admin"}]},
    "users": [{"username": "admin_dvas", "credentials": [{"hashedSaltedValue": "abc"}]}],
    "clients": [
        {"clientId": "drone-ingest-client", "secret": "real-secret", "protocolMappers": [{"name": "m"}]},
        {"clientId": "dvas-admin-service", "secret": "other", "attributes": {"client.secret.creation.time": "1"}},
        {"clientId": "account", "publicClient": True},
    ],
    "components": {
        "org.keycloak.keys.KeyProvider": [{"config": {"privateKey": ["-----BEGIN"]}}],
        "org.keycloak.userprofile.UserProfileProvider": [{"name": "up"}],
    },
    "smtpServer": {"host": "smtp", "password": "pw"},
}


def test_sanitize_removes_secrets_and_users():
    clean = sanitize_realm.sanitize(RAW)
    assert "users" not in clean
    secrets = {c["clientId"]: c.get("secret") for c in clean["clients"]}
    assert secrets["drone-ingest-client"] == sanitize_realm.MASK
    assert secrets["account"] is None
    assert "org.keycloak.keys.KeyProvider" not in clean["components"]
    assert "org.keycloak.userprofile.UserProfileProvider" in clean["components"]
    assert clean["smtpServer"]["password"] == sanitize_realm.MASK
    assert clean["roles"] == RAW["roles"]
    assert clean["clients"][0]["protocolMappers"] == [{"name": "m"}]
    assert sanitize_realm.find_leaks(clean) == []


def test_find_leaks_detects_remaining_secret():
    assert sanitize_realm.find_leaks({"identityProviders": [{"config": {"clientSecret": "x"}}]})


def test_cli_round_trip(tmp_path):
    raw = tmp_path / "raw.json"
    out = tmp_path / "tpl.json"
    raw.write_text(json.dumps(RAW))
    assert sanitize_realm.main(["x", str(raw), str(out)]) == 0
    assert "real-secret" not in out.read_text()
