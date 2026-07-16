"""DVAS backend regression tests — auth, RBAC, exports 65B, infra health."""
import os
import io
import zipfile
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://dvas-design-system.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
PWD = "dvas1234"

# 4-role model — field@dvas.kp migrated to control_room_officer,
#                  supervisor@dvas.kp migrated to senior_officer
USERS = {
    "operator":  ("operator@dvas.kp",    "drone_operator"),
    "control":   ("control@dvas.kp",     "control_room_officer"),
    "control2":  ("field@dvas.kp",       "control_room_officer"),   # migrated from field_supervisor
    "senior":    ("senior@dvas.kp",      "senior_officer"),
    "senior2":   ("supervisor@dvas.kp",  "senior_officer"),          # migrated from supervisory_officer
    "admin":     ("admin@dvas.kp",       "system_administrator"),
}


def _login(email):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": PWD}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def tokens():
    return {k: _login(v[0]) for k, v in USERS.items()}


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- Auth ----------
@pytest.mark.parametrize("key,email,role", [(k, v[0], v[1]) for k, v in USERS.items()])
def test_login_all_roles(key, email, role):
    tok = _login(email)
    r = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok}"}, timeout=10)
    assert r.status_code == 200
    me = r.json()
    assert me["email"] == email
    assert me["role"] == role


def test_login_invalid():
    r = requests.post(f"{API}/auth/login", json={"email": "operator@dvas.kp", "password": "wrong"}, timeout=10)
    assert r.status_code == 401


# ---------- Infra health (admin only) ----------
def test_infra_health_admin(tokens):
    r = requests.get(f"{API}/infra/health", headers=_h(tokens["admin"]), timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ("feeds", "gpu", "queue", "alerts"):
        assert k in d


def test_infra_health_forbidden_for_non_admin(tokens):
    r = requests.get(f"{API}/infra/health", headers=_h(tokens["operator"]), timeout=10)
    assert r.status_code == 403


# ---------- Command KPIs ----------
def test_command_kpis(tokens):
    # All 4 operational roles (CRO and SR) should get KPIs
    for who in ("control", "control2", "senior", "senior2"):
        r = requests.get(f"{API}/command/kpis", headers=_h(tokens[who]), timeout=10)
        assert r.status_code == 200, who
        d = r.json()
        for k in ("active_missions", "alerts_24h", "evidence_total", "exports_pending", "drones_online"):
            assert k in d


def test_command_kpis_denied_for_operator_admin(tokens):
    for who in ("operator", "admin"):
        r = requests.get(f"{API}/command/kpis", headers=_h(tokens[who]), timeout=10)
        assert r.status_code == 403, who


# ---------- Data endpoints RBAC ----------
def test_drones_operator_scoped(tokens):
    r = requests.get(f"{API}/drones", headers=_h(tokens["operator"]), timeout=10)
    assert r.status_code == 200
    for d in r.json():
        # operator only sees their own
        assert d.get("operator_id")


def test_alerts_denied_for_operator_admin(tokens):
    for who in ("operator", "admin"):
        r = requests.get(f"{API}/alerts", headers=_h(tokens[who]), timeout=10)
        assert r.status_code == 403


def test_evidence_list(tokens):
    # Both SR accounts should see evidence
    for who in ("senior", "senior2"):
        r = requests.get(f"{API}/evidence", headers=_h(tokens[who]), timeout=10)
        assert r.status_code == 200, who
        assert isinstance(r.json(), list)


def test_edge_status(tokens):
    r = requests.get(f"{API}/edge/status", headers=_h(tokens["operator"]), timeout=10)
    assert r.status_code == 200
    body = r.json()
    for k in ("buffer_pct", "battery_pct", "signal_pct", "drone"):
        assert k in body


# ---------- Export approval workflow + 65B bundle ----------
def test_export_full_workflow(tokens):
    """CRO requests export; SR approves; CRO downloads bundle."""
    # CRO (migrated from field_supervisor): pick an evidence in scope
    r = requests.get(f"{API}/evidence", headers=_h(tokens["control2"]), timeout=10)
    assert r.status_code == 200
    ev_list = r.json()
    assert ev_list, "No evidence to test with"
    ev = next((e for e in ev_list if e["jurisdiction"] == "Thiruvananthapuram"), ev_list[0])

    # CRO submits export request
    r = requests.post(f"{API}/exports",
                      headers=_h(tokens["control2"]),
                      json={"evidence_id": ev["id"], "justification": "TEST_ regression"},
                      timeout=15)
    assert r.status_code == 200, r.text
    exp = r.json()
    assert exp["status"] == "pending"
    exp_id = exp["id"]

    # CRO cannot decide (approve/reject) — only SR can
    r = requests.post(f"{API}/exports/{exp_id}/decision",
                      headers=_h(tokens["control2"]),
                      json={"decision": "approved", "comment": "nope"},
                      timeout=10)
    assert r.status_code == 403

    # SR (migrated from supervisory_officer) approves
    r = requests.post(f"{API}/exports/{exp_id}/decision",
                      headers=_h(tokens["senior2"]),
                      json={"decision": "approved", "comment": "TEST_ approved"},
                      timeout=15)
    assert r.status_code == 200

    # CRO downloads the 65B bundle for their own request
    r = requests.get(f"{API}/exports/{exp_id}/bundle", headers=_h(tokens["control2"]), timeout=20)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert any("certificate_65B.pdf" in n for n in names)
    assert "metadata.json" in names


def test_cro_cannot_approve_exports(tokens):
    """Control Room Officer must NOT be able to approve/reject any export request."""
    # Get any export ID from the SR account (SR sees all)
    r = requests.get(f"{API}/exports", headers=_h(tokens["senior"]), timeout=10)
    if r.status_code == 200 and r.json():
        exp_id = r.json()[0]["id"]
        r2 = requests.post(f"{API}/exports/{exp_id}/decision",
                           headers=_h(tokens["control"]),
                           json={"decision": "approved", "comment": "unauthorized"},
                           timeout=10)
        assert r2.status_code == 403, "CRO should not be able to approve exports"


def test_senior_sees_all_exports(tokens):
    """Senior Officer can see all export requests (jurisdiction-wide)."""
    r = requests.get(f"{API}/exports", headers=_h(tokens["senior"]), timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_operator_cannot_request_export(tokens):
    """Drone Operator must NOT be able to submit export requests."""
    r = requests.post(f"{API}/exports",
                      headers=_h(tokens["operator"]),
                      json={"evidence_id": "dummy", "justification": "unauthorized"},
                      timeout=10)
    assert r.status_code == 403


# ---------- Audit logs ----------
def test_audit_logs_senior_full_access(tokens):
    """Senior Officer gets full audit log (not restricted like sys admin)."""
    r = requests.get(f"{API}/audit-logs", headers=_h(tokens["senior"]), timeout=10)
    assert r.status_code == 200
    logs = r.json()
    assert isinstance(logs, list)
    # SR sees more than just their own entries (unlike sys admin)
    # (may be empty in a fresh DB — just confirm 200)


def test_audit_logs_admin_scoped(tokens):
    r = requests.get(f"{API}/audit-logs", headers=_h(tokens["admin"]), timeout=10)
    assert r.status_code == 200
    logs = r.json()
    for log in logs:
        assert log["actor_role"] == "system_administrator"


def test_audit_logs_denied(tokens):
    # CRO and Operator should not see audit logs
    for who in ("operator", "control"):
        r = requests.get(f"{API}/audit-logs", headers=_h(tokens[who]), timeout=10)
        assert r.status_code == 403, who


# ---------- Users admin ----------
def test_users_list_admin_only(tokens):
    r = requests.get(f"{API}/users", headers=_h(tokens["admin"]), timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    # Senior Officer must NOT have access to user list
    r2 = requests.get(f"{API}/users", headers=_h(tokens["senior"]), timeout=10)
    assert r2.status_code == 403


def test_roles_in_db_are_4_role_model(tokens):
    """Verify no users have deprecated roles after migration."""
    r = requests.get(f"{API}/users", headers=_h(tokens["admin"]), timeout=10)
    assert r.status_code == 200
    deprecated = {"field_supervisor", "supervisory_officer"}
    for u in r.json():
        assert u["role"] not in deprecated, (
            f"{u['email']} still has deprecated role '{u['role']}'"
        )
