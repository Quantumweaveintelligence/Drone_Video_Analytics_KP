"""
Report data layer (evidence plan R2). One `collect_<type>()` per report type, all
returning plain JSON-serialisable dicts, used by both the PDF and preview-data,
so the preview and the PDF always show the same numbers.

* Periods are IST calendar days ([00:00 IST, 24:00 IST) → UTC); the UI sends dates.
* The requester's scope is applied to every query (B8 policy): seniors/admins
  see everything; CROs their jurisdiction (+ statewide rows); analytics tables,
  which have no jurisdiction, are limited to the in-scope missions / drones.
* Mission hours are clipped to the period; open missions are counted apart and
  nothing is padded (B13).
* Analytics aggregates come from the analytics DB through the read-only role
  (services/analytics_ro.py). If it is unreachable the section says so; the
  report is still produced from the dashboard data.
"""
import logging
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, false, or_, select

from core.timeutil import IST, iso_utc, ist_day_bounds, ist_range, parse_ts, today_ist
from models import (Alert, AuditLog, Case, CaseEvidence, Evidence, Export, Incident, Mission)
from services import analytics_ro

logger = logging.getLogger("dvas.report_data")

STATEWIDE = ("Kerala", "State HQ")
SEVERITIES = ("critical", "high", "medium", "low")


class ReportInputError(ValueError):
    """Bad or out-of-scope report parameters (→ HTTP 400/404)."""


# ── Scope ─────────────────────────────────────────────────────────────────────
class Scope:
    def __init__(self, role: str, jurisdiction: Optional[str]):
        self.role = role
        self.jurisdiction = jurisdiction or None
        self.unrestricted = role in ("senior_officer", "system_administrator") or not self.jurisdiction

    @classmethod
    def from_params(cls, params: dict) -> "Scope":
        s = params.get("scope") or {}
        return cls(s.get("role") or "", s.get("jurisdiction"))

    def jur_clause(self, column):
        from core.security import jurisdiction_clause
        return jurisdiction_clause(column, {"role": self.role, "jurisdiction": self.jurisdiction})


def _where(stmt, *clauses):
    for c in clauses:
        if c is not None:
            stmt = stmt.where(c)
    return stmt


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


# ── Parameters ────────────────────────────────────────────────────────────────
def normalize_params(report_type: str, raw: dict, user: dict) -> dict:
    """Validate and freeze the parameters stored in reports.params (so a report can be regenerated)."""
    from services.report_types import REPORT_TYPES
    spec = REPORT_TYPES.get(report_type)
    if spec is None:
        raise ReportInputError(f"Unknown report type {report_type}")
    raw = dict(raw or {})
    today = today_ist().isoformat()
    period = spec["period"]
    if period == "day":
        start_day = end_day = raw.get("date") or raw.get("start_date") or today
    elif period == "week":
        start_day = raw.get("week_start") or raw.get("start_date") or \
            (today_ist() - timedelta(days=today_ist().weekday())).isoformat()
        end_day = (datetime.fromisoformat(start_day).date() + timedelta(days=6)).isoformat()
    elif period == "range":
        if raw.get("period_days") and not raw.get("start_date"):          # old UI parameter
            days = max(1, min(int(raw["period_days"]), 366))
            end_day = raw.get("end_date") or today
            start_day = (datetime.fromisoformat(end_day).date() - timedelta(days=days - 1)).isoformat()
        else:
            start_day = raw.get("start_date") or today
            end_day = raw.get("end_date") or start_day
    else:                     # mission / case: the period comes from the mission / case
        start_day = end_day = None
    if start_day:
        try:
            s, e = ist_range(start_day, end_day)
        except ValueError as exc:
            raise ReportInputError(str(exc))
        if (e - s).days > 366:
            raise ReportInputError("Period longer than one year")
    if period == "mission" and not raw.get("mission_id"):
        raise ReportInputError("mission_id is required")
    if period == "case" and not raw.get("case_id"):
        raise ReportInputError("case_id is required")
    drone_ids = raw.get("drone_ids") or []
    if isinstance(drone_ids, str):
        drone_ids = [d.strip() for d in drone_ids.split(",") if d.strip()]
    return {
        "report_type": report_type,
        "start_day": start_day, "end_day": end_day, "tz": "Asia/Kolkata",
        "start_utc": s.isoformat() if start_day else None,
        "end_utc": e.isoformat() if start_day else None,
        "drone_ids": drone_ids,
        "mission_id": raw.get("mission_id"),
        "case_id": raw.get("case_id"),
        "jurisdiction": raw.get("jurisdiction") or None,
        "scope": {"role": user.get("role"), "jurisdiction": user.get("jurisdiction") or None},
        "data_cutoff": datetime.now(timezone.utc).isoformat(),
    }


def _period(params: dict):
    return parse_ts(params["start_utc"]), min(parse_ts(params["end_utc"]), parse_ts(params["data_cutoff"]))


# ── Dashboard sections ────────────────────────────────────────────────────────
async def missions_in(session, scope: Scope, start, end, *, mission_id=None, drone_ids=None,
                      jurisdiction=None) -> List[dict]:
    """Missions overlapping [start, end), with hours inside the period (no padding)."""
    stmt = _where(select(Mission), scope.jur_clause(Mission.jurisdiction),
                  Mission.mission_id == mission_id if mission_id else None,
                  Mission.drone_id.in_(drone_ids) if drone_ids else None,
                  Mission.jurisdiction == jurisdiction if jurisdiction else None)
    if not mission_id:
        stmt = stmt.where(Mission.started_at < end.isoformat(),
                          or_(Mission.ended_at.is_(None), Mission.ended_at >= start.isoformat()))
    rows = (await session.execute(stmt.order_by(Mission.started_at))).scalars().all()
    now = datetime.now(timezone.utc)
    out = []
    for m in rows:
        d = _row(m)
        st, en = parse_ts(m.started_at), parse_ts(m.ended_at)
        d["open"] = en is None
        if st:
            lo, hi = max(st, start), min(en or now, end)
            d["hours_in_period"] = round(max(0.0, (hi - lo).total_seconds()) / 3600, 2)
        else:
            d["hours_in_period"] = 0.0
        out.append(d)
    return out


async def alerts_in(session, scope: Scope, start, end, *, mission_id=None, drone_ids=None,
                    categories: Sequence[str] = ("ai", "manual"), jurisdiction=None) -> List[dict]:
    cat = Alert.category.in_(categories)
    if "manual" in categories:
        cat = or_(cat, Alert.category.is_(None))
    stmt = _where(select(Alert), cat, scope.jur_clause(Alert.jurisdiction),
                  Alert.ts >= start.isoformat(), Alert.ts < end.isoformat(),
                  Alert.mission_id == mission_id if mission_id else None,
                  Alert.drone_id.in_(drone_ids) if drone_ids else None,
                  Alert.jurisdiction == jurisdiction if jurisdiction else None)
    rows = (await session.execute(stmt.order_by(Alert.ts))).scalars().all()
    out = []
    for a in rows:
        d = _row(a)
        ts, ack = parse_ts(a.ts), parse_ts(a.acknowledged_at)
        d["minutes_to_ack"] = round((ack - ts).total_seconds() / 60, 1) if ts and ack and ack >= ts else None
        out.append(d)
    return out


async def incidents_in(session, start, end, *, mission_ids: Optional[List[str]] = None,
                       mission_id=None) -> List[dict]:
    stmt = _where(select(Incident), Incident.timestamp >= start.isoformat(), Incident.timestamp < end.isoformat(),
                  Incident.mission_id == mission_id if mission_id else None,
                  (Incident.mission_id.in_(mission_ids) if mission_ids else false())
                  if mission_ids is not None else None)
    return [_row(i) for i in (await session.execute(stmt.order_by(Incident.timestamp))).scalars().all()]


async def evidence_in(session, scope: Scope, start=None, end=None, *, mission_id=None, drone_ids=None,
                      event_types: Optional[Sequence[str]] = None, ids: Optional[List[str]] = None,
                      jurisdiction=None) -> List[dict]:
    stmt = _where(select(Evidence), Evidence.status != "disposed", scope.jur_clause(Evidence.jurisdiction),
                  Evidence.ts >= start.isoformat() if start else None,
                  Evidence.ts < end.isoformat() if end else None,
                  Evidence.mission_id == mission_id if mission_id else None,
                  Evidence.drone_id.in_(drone_ids) if drone_ids else None,
                  Evidence.event_type.in_(event_types) if event_types else None,
                  Evidence.id.in_(ids) if ids is not None else None,
                  Evidence.jurisdiction == jurisdiction if jurisdiction else None)
    return [_row(e) for e in (await session.execute(stmt.order_by(Evidence.ts))).scalars().all()]


def count_by(rows: List[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        k = r.get(key) or "unknown"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def ack_stats(alerts: List[dict]) -> dict:
    mins = sorted(a["minutes_to_ack"] for a in alerts if a.get("minutes_to_ack") is not None)
    acked = sum(1 for a in alerts if a.get("acknowledged_at"))
    return {
        "total": len(alerts), "acknowledged": acked,
        "ack_rate_pct": round(100 * acked / len(alerts), 1) if alerts else None,
        "median_minutes_to_ack": round(median(mins), 1) if mins else None,
        "p90_minutes_to_ack": round(mins[min(len(mins) - 1, int(0.9 * len(mins)))], 1) if mins else None,
    }


def map_points(*groups) -> List[dict]:
    """[(rows, kind, label_key)] → points with lat/lon for the report map."""
    pts = []
    for rows, kind, label_key in groups:
        for r in rows:
            lat, lon = r.get("lat", r.get("latitude")), r.get("lon", r.get("longitude"))
            if lat is None or lon is None or (lat == 0 and lon == 0):
                continue
            pts.append({"lat": float(lat), "lon": float(lon), "kind": kind, "label": str(r.get(label_key) or "")})
    return pts


# ── Analytics DB sections (read-only role) ────────────────────────────────────
class _Filter:
    """Builds `AND col = ANY($n)` clauses for the scoped mission / drone lists."""

    def __init__(self, start, end):
        self.args: List[Any] = [start, end]

    def any(self, column: str, values: Optional[List[str]]) -> str:
        if values is None:
            return ""
        self.args.append(list(values))
        return f" AND {column} = ANY(${len(self.args)}::text[])"


async def analytics_summary(start, end, *, mission_ids: Optional[List[str]] = None,
                            drone_ids: Optional[List[str]] = None,
                            sections: Sequence[str] = ("plates", "theft", "crowd", "intrusion", "vessel", "anomaly")
                            ) -> Dict[str, Any]:
    """Aggregates per module. mission_ids/drone_ids = None means unrestricted."""
    out: Dict[str, Any] = {}

    async def run(name, fn):
        try:
            out[name] = await fn()
        except analytics_ro.AnalyticsUnavailable as exc:
            out[name] = {"error": f"analytics data unavailable ({exc})"}
        except Exception as exc:
            logger.warning("Report section %s failed: %s", name, exc)
            out[name] = {"error": f"query failed ({type(exc).__name__})"}

    async def plates():
        f = _Filter(start, end)
        cond = "created_at >= $1 AND created_at < $2" + f.any("mission_id_str", mission_ids)
        tot = await analytics_ro.fetch(
            f"SELECT count(*) AS reads, count(DISTINCT plate_text) AS plates FROM plate_reads WHERE {cond}", *f.args)
        top = await analytics_ro.fetch(
            f"SELECT plate_text, count(*) AS reads, max(ocr_confidence) AS best_conf, min(created_at) AS first_seen,"
            f" max(created_at) AS last_seen FROM plate_reads WHERE {cond} AND plate_text <> ''"
            f" GROUP BY plate_text ORDER BY reads DESC LIMIT 50", *f.args)
        return {"reads": tot[0]["reads"], "distinct_plates": tot[0]["plates"], "top": top}

    async def theft():
        f = _Filter(start, end)
        cond = "detected_at >= $1 AND detected_at < $2" + f.any("drone_id", drone_ids)
        rows = await analytics_ro.fetch(
            f"SELECT plate_text, drone_id, detected_at, confidence, vehicle_type FROM theft_alerts"
            f" WHERE {cond} ORDER BY detected_at", *f.args)
        return {"hits": rows}

    async def crowd():
        f = _Filter(start, end)
        cond = "ts >= $1 AND ts < $2" + f.any("drone_id", drone_ids)
        per_drone = await analytics_ro.fetch(
            f"SELECT drone_id, max(estimated_count) AS peak, round(avg(estimated_count)::numeric, 1) AS avg,"
            f" count(*) AS readings FROM crowd_density_readings WHERE {cond} GROUP BY drone_id ORDER BY peak DESC",
            *f.args)
        hourly = await analytics_ro.fetch(
            f"SELECT date_trunc('hour', ts AT TIME ZONE 'Asia/Kolkata') AS hour_ist, max(estimated_count) AS peak"
            f" FROM crowd_density_readings WHERE {cond} GROUP BY 1 ORDER BY 1", *f.args)
        return {"per_drone": per_drone, "hourly": hourly}

    async def intrusion():
        f = _Filter(start, end)
        cond = "ts >= $1 AND ts < $2" + f.any("mission_id", mission_ids)
        zones = await analytics_ro.fetch(
            f"SELECT zone_id, count(*) FILTER (WHERE state_to = 'ENTERING') AS entries,"
            f" count(*) FILTER (WHERE state_to IN ('ENTERING','DWELLING')) AS breaches,"
            f" round(max(dwell_duration_s)::numeric, 1) AS max_dwell_s,"
            f" round((avg(dwell_duration_s) FILTER (WHERE dwell_duration_s > 0))::numeric, 1) AS avg_dwell_s"
            f" FROM intrusion_events WHERE {cond} GROUP BY zone_id ORDER BY breaches DESC", *f.args)
        classes = await analytics_ro.fetch(
            f"SELECT track_class, count(*) AS n FROM intrusion_events WHERE {cond}"
            f" AND state_to = 'ENTERING' GROUP BY 1 ORDER BY 2 DESC", *f.args)
        points = await analytics_ro.fetch(
            f"SELECT latitude, longitude, track_class, zone_id, ts FROM intrusion_events WHERE {cond}"
            f" AND state_to IN ('ENTERING','DWELLING') ORDER BY ts LIMIT 1000", *f.args)
        return {"zones": zones, "classes": classes, "points": points}

    async def vessel():
        f = _Filter(start, end)
        cond = "ts >= $1 AND ts < $2" + f.any("mission_id", mission_ids)
        return {"by_type": await analytics_ro.fetch(
            f"SELECT event_type, class_name, count(*) AS n FROM vessel_events WHERE {cond}"
            f" GROUP BY 1, 2 ORDER BY 3 DESC", *f.args)}

    async def anomaly():
        f = _Filter(start, end)
        cond = "ts >= $1 AND ts < $2" + f.any("mission_id", mission_ids)
        return {"by_type": await analytics_ro.fetch(
            f"SELECT event_type, lower(severity) AS severity, count(*) AS n FROM anomaly_events WHERE {cond}"
            f" GROUP BY 1, 2 ORDER BY 3 DESC", *f.args)}

    fns = {"plates": plates, "theft": theft, "crowd": crowd, "intrusion": intrusion,
           "vessel": vessel, "anomaly": anomaly}
    for name in sections:
        await run(name, fns[name])
    return _jsonable(out)


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return iso_utc(value) if value.tzinfo else value.isoformat()
    if hasattr(value, "is_finite"):           # Decimal
        return float(value)
    return value


async def _analytics_scope(session, scope: Scope, start, end, mission_id=None, drone_ids=None):
    """(mission_ids, drone_ids) the analytics queries are limited to; None = unrestricted."""
    if mission_id:
        missions = await missions_in(session, scope, start, end, mission_id=mission_id)
        return [m["mission_id"] for m in missions], [m["drone_id"] for m in missions if m.get("drone_id")]
    if scope.unrestricted and not drone_ids:
        return None, None
    missions = await missions_in(session, scope, start, end, drone_ids=drone_ids)
    m_ids = None if scope.unrestricted else [m["mission_id"] for m in missions]
    d_ids = drone_ids or sorted({m["drone_id"] for m in missions if m.get("drone_id")})
    return m_ids, d_ids


# ── Report collectors ─────────────────────────────────────────────────────────
async def collect_daily_sitrep(session, params: dict) -> dict:
    scope, (start, end) = Scope.from_params(params), _period(params)
    jur, drones = params.get("jurisdiction"), params.get("drone_ids") or None
    missions = await missions_in(session, scope, start, end, drone_ids=drones, jurisdiction=jur)
    alerts = await alerts_in(session, scope, start, end, drone_ids=drones, jurisdiction=jur)
    m_ids, d_ids = await _analytics_scope(session, scope, start, end, drone_ids=drones)
    evidence = await evidence_in(session, scope, start, end, drone_ids=drones, jurisdiction=jur)
    return {
        "missions": missions,
        "open_missions": [m for m in missions if m["open"]],
        "alerts": alerts,
        "alerts_by_severity": count_by(alerts, "severity"),
        "alerts_by_type": count_by(alerts, "type"),
        "alerts_by_jurisdiction": count_by(alerts, "jurisdiction"),
        "ack": ack_stats(alerts),
        "incidents": await incidents_in(session, start, end,
                                        mission_ids=None if scope.unrestricted else [m["mission_id"] for m in missions]),
        "evidence": evidence,
        "evidence_by_type": count_by(evidence, "event_type"),
        "analytics": await analytics_summary(start, end, mission_ids=m_ids, drone_ids=d_ids),
        "map_points": map_points((evidence, "evidence", "event_type"), (alerts, "alert", "type")),
    }


async def collect_mission_after_action(session, params: dict) -> dict:
    scope = Scope.from_params(params)
    mission_id = params["mission_id"]
    m = (await session.execute(_where(select(Mission), Mission.mission_id == mission_id,
                                      scope.jur_clause(Mission.jurisdiction)))).scalar_one_or_none()
    if m is None:
        raise ReportInputError("Mission not found")
    mission = _row(m)
    start = parse_ts(m.started_at) or parse_ts(params["data_cutoff"]) - timedelta(days=1)
    end = parse_ts(m.ended_at) or parse_ts(params["data_cutoff"])
    mission["open"] = m.ended_at is None
    mission["hours"] = round(max(0.0, (end - start).total_seconds()) / 3600, 2) if m.started_at else None
    far_past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    alerts = await alerts_in(session, scope, far_past, parse_ts(params["data_cutoff"]), mission_id=mission_id)
    window_alerts = []
    if m.drone_id and m.started_at and m.ended_at:
        untagged = select(Alert).where(Alert.drone_id == m.drone_id,
                                       or_(Alert.mission_id.is_(None), Alert.mission_id == ""),
                                       Alert.ts >= start.isoformat(), Alert.ts <= end.isoformat())
        window_alerts = [_row(a) for a in (await session.execute(untagged.order_by(Alert.ts))).scalars().all()]
    evidence = await evidence_in(session, scope, mission_id=mission_id)
    incidents = await incidents_in(session, far_past, parse_ts(params["data_cutoff"]), mission_id=mission_id)
    timeline = sorted(
        [{"ts": a["ts"], "kind": "alert", "text": a.get("title") or a.get("type"), "severity": a.get("severity")}
         for a in alerts]
        + [{"ts": i["timestamp"], "kind": "incident", "text": i.get("title"), "severity": (i.get("severity") or "").lower()}
           for i in incidents]
        + [{"ts": e["ts"], "kind": "evidence", "text": f"{e['id']} {e.get('event_type')}", "severity": e.get("severity")}
           for e in evidence],
        key=lambda x: parse_ts(x["ts"]) or far_past)
    analytics = await analytics_summary(start, end, mission_ids=[mission_id],
                                        drone_ids=[m.drone_id] if m.drone_id else [])
    intrusion_pts = (analytics.get("intrusion") or {}).get("points") or []
    return {
        "mission": mission, "alerts": alerts, "ack": ack_stats(alerts),
        "alerts_by_type": count_by(alerts, "type"), "window_alerts": window_alerts,
        "incidents": incidents, "evidence": evidence, "evidence_by_type": count_by(evidence, "event_type"),
        "timeline": timeline, "analytics": analytics,
        "map_points": map_points((evidence, "evidence", "event_type"), (alerts, "alert", "type"),
                                 (intrusion_pts, "intrusion", "track_class")),
    }


async def collect_evidence_register(session, params: dict) -> dict:
    from routers.alerts import _case_visible
    scope = Scope.from_params(params)
    case = await session.get(Case, params["case_id"])
    if case is None or not _case_visible(case, {"role": scope.role, "jurisdiction": scope.jurisdiction}):
        raise ReportInputError("Case not found")
    links = (await session.execute(select(CaseEvidence).where(CaseEvidence.case_id == case.id)
                                   .order_by(CaseEvidence.added_at))).scalars().all()
    ids = [link.evidence_id for link in links]
    rows = (await session.execute(select(Evidence).where(Evidence.id.in_(ids)))).scalars().all() if ids else []
    by_id = {r.id: _row(r) for r in rows}
    evidence = [dict(by_id[i], added_by=link.added_by, added_at=link.added_at)
                for i, link in zip(ids, links) if i in by_id]
    audit = (await session.execute(select(AuditLog).where(
        or_(AuditLog.target_id.in_(ids + [case.id]) if ids else AuditLog.target_id == case.id))
        .order_by(AuditLog.ts))).scalars().all()
    exports = (await session.execute(select(Export).where(Export.case_id == case.id)
                                     .order_by(Export.requested_at))).scalars().all()
    return {
        "case": _row(case), "evidence": evidence,
        "custody": [{"ts": a.ts, "action": a.action, "actor": a.actor_email or a.actor_name,
                     "target_id": a.target_id, "details": a.details, "hash": a.hash} for a in audit],
        "exports": [_row(x) for x in exports],
        "map_points": map_points((evidence, "evidence", "event_type")),
    }


async def collect_anpr(session, params: dict) -> dict:
    scope, (start, end) = Scope.from_params(params), _period(params)
    drones = params.get("drone_ids") or None
    m_ids, d_ids = await _analytics_scope(session, scope, start, end, drone_ids=drones)
    evidence = await evidence_in(session, scope, start, end, drone_ids=drones, event_types=["theft_alert"])
    return {
        "analytics": await analytics_summary(start, end, mission_ids=m_ids, drone_ids=d_ids,
                                             sections=("plates", "theft")),
        "theft_evidence": evidence,
        "map_points": map_points((evidence, "theft", "event_type")),
    }


async def collect_crowd(session, params: dict) -> dict:
    scope, (start, end) = Scope.from_params(params), _period(params)
    drones = params.get("drone_ids") or None
    m_ids, d_ids = await _analytics_scope(session, scope, start, end, drone_ids=drones)
    evidence = await evidence_in(session, scope, start, end, drone_ids=drones, event_types=["crowd_threshold"])
    return {
        "analytics": await analytics_summary(start, end, mission_ids=m_ids, drone_ids=d_ids, sections=("crowd",)),
        "surges": evidence,
        "map_points": map_points((evidence, "crowd", "event_type")),
    }


async def collect_intrusion(session, params: dict) -> dict:
    scope, (start, end) = Scope.from_params(params), _period(params)
    drones = params.get("drone_ids") or None
    m_ids, d_ids = await _analytics_scope(session, scope, start, end, drone_ids=drones)
    alerts = [a for a in await alerts_in(session, scope, start, end, drone_ids=drones, categories=("ai",))
              if (a.get("type") or "").startswith("intrusion")]
    analytics = await analytics_summary(start, end, mission_ids=m_ids, drone_ids=d_ids, sections=("intrusion",))
    return {
        "analytics": analytics, "alerts": alerts, "response": ack_stats(alerts),
        "map_points": map_points(((analytics.get("intrusion") or {}).get("points") or [], "intrusion", "track_class")),
    }


CONFIDENCE_BANDS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01))


async def collect_detection_quality(session, params: dict) -> dict:
    scope, (start, end) = Scope.from_params(params), _period(params)
    alerts = await alerts_in(session, scope, start, end, drone_ids=params.get("drone_ids") or None,
                             categories=("ai",))

    def precision(rows):
        tp = sum(1 for a in rows if a.get("disposition") == "true_positive")
        fp = sum(1 for a in rows if a.get("disposition") == "false_positive")
        reviewed = sum(1 for a in rows if a.get("disposition"))
        return {"alerts": len(rows), "reviewed": reviewed, "true_positive": tp, "false_positive": fp,
                "precision_pct": round(100 * tp / (tp + fp), 1) if tp + fp else None}

    by_rule: Dict[str, list] = {}
    for a in alerts:
        by_rule.setdefault(a.get("rule") or a.get("type") or "unknown", []).append(a)
    bands = []
    for lo, hi in CONFIDENCE_BANDS:
        rows = [a for a in alerts if a.get("confidence") is not None and lo <= a["confidence"] < hi]
        bands.append(dict(precision(rows), band=f"{lo:.2f}–{min(hi, 1.0):.2f}"))
    return {"overall": precision(alerts),
            "by_rule": [dict(precision(v), rule=k) for k, v in sorted(by_rule.items())],
            "by_confidence": bands}


async def collect_weekly_response(session, params: dict) -> dict:
    scope, (start, end) = Scope.from_params(params), _period(params)
    drones = params.get("drone_ids") or None
    alerts = await alerts_in(session, scope, start, end, drone_ids=drones)
    missions = await missions_in(session, scope, start, end, drone_ids=drones)
    per_day = []
    day = datetime.fromisoformat(params["start_day"]).date()
    while day.isoformat() <= params["end_day"]:
        s, e = ist_day_bounds(day)
        rows = [a for a in alerts if s <= parse_ts(a["ts"]) < e]
        per_day.append({"day": day.isoformat(), **ack_stats(rows)})
        day += timedelta(days=1)
    return {
        "alerts": alerts, "ack": ack_stats(alerts), "per_day": per_day,
        "by_severity": {sev: ack_stats([a for a in alerts if (a.get("severity") or "") == sev])
                        for sev in SEVERITIES},
        "incidents": await incidents_in(session, start, end,
                                        mission_ids=None if scope.unrestricted else [m["mission_id"] for m in missions]),
        "missions": missions,
    }


async def collect_drone_utilisation(session, params: dict) -> dict:
    scope, (start, end) = Scope.from_params(params), _period(params)
    missions = await missions_in(session, scope, start, end, drone_ids=params.get("drone_ids") or None)
    per_drone: Dict[str, dict] = {}
    for m in missions:
        d = per_drone.setdefault(m.get("drone_id") or "unassigned",
                                 {"drone_id": m.get("drone_id") or "unassigned", "missions": 0,
                                  "closed_hours": 0.0, "open_missions": 0, "open_hours_so_far": 0.0})
        d["missions"] += 1
        if m["open"]:
            d["open_missions"] += 1
            d["open_hours_so_far"] = round(d["open_hours_so_far"] + m["hours_in_period"], 2)
        else:
            d["closed_hours"] = round(d["closed_hours"] + m["hours_in_period"], 2)
    period_hours = (end - start).total_seconds() / 3600
    for d in per_drone.values():
        d["utilisation_pct"] = round(100 * d["closed_hours"] / period_hours, 1) if period_hours else None
    return {"missions": missions, "per_drone": sorted(per_drone.values(), key=lambda d: -d["closed_hours"]),
            "period_hours": round(period_hours, 1)}
