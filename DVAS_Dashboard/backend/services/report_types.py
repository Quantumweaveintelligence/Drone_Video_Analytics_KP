"""
Report types (evidence plan R5): one entry per report with its period kind, its
data function (services/report_data.py — the same data feeds preview-data) and
its PDF template. Seniors generate directly; CROs request and a senior approves.
"""
import logging
from typing import Callable, Dict, List, Optional

from core.timeutil import fmt_ist
from services import report_data as rd
from services.pdf.document import ReportPDF
from services.pdf.mapimg import render_map

logger = logging.getLogger("dvas.report_types")

GALLERY_LIMIT = 12


def _when(row: dict, *keys) -> str:
    for k in keys:
        if row.get(k):
            return fmt_ist(row[k], with_zone=False)
    return "—"


def _period_label(params: dict) -> str:
    if params.get("start_day"):
        if params["start_day"] == params["end_day"]:
            return f"{params['start_day']} (IST day)"
        return f"{params['start_day']} to {params['end_day']} (IST, inclusive)"
    return ""


def _evidence_images(evidence: List[dict], limit: int = GALLERY_LIMIT) -> List[tuple]:
    """(bytes, caption) for the primary object of each evidence item, read from the vault."""
    from services import evidence_vault
    items = []
    for ev in evidence[:limit]:
        obj = next((o for o in (ev.get("objects") or []) if o.get("role") == "primary"
                    and o.get("bucket") and o.get("key")), None)
        if obj is None and ev.get("primary_image_key"):
            obj = {"bucket": ev.get("image_bucket"), "key": ev["primary_image_key"], "sha256": None}
        data = None
        if obj:
            try:
                data = evidence_vault.read_object(obj["bucket"], obj["key"])
            except Exception:
                data = None
        sha = (obj or {}).get("sha256") or ""
        caption = f"{ev['id']} · {ev.get('event_type')} · {_when(ev, 'capture_ts', 'ts')}"
        if sha:
            caption += f" · SHA-256 {sha[:12]}…"
        if ev.get("image_match") == "heuristic":
            caption += " · image matched heuristically (pre-E1)"
        items.append((data, caption))
    return items


def _analytics_error(section) -> Optional[str]:
    return section.get("error") if isinstance(section, dict) else None


def _module_sections(pdf: ReportPDF, analytics: dict):
    """Shared analytics block: plates, theft, crowd, intrusion, vessel, anomaly."""
    for name, title in (("plates", "Number plates read"), ("theft", "Watchlist (stolen vehicle) hits"),
                        ("crowd", "Crowd density"), ("intrusion", "Intrusion / geofence"),
                        ("vessel", "Maritime (vessels)"), ("anomaly", "Anomalies")):
        sec = analytics.get(name)
        if sec is None:
            continue
        err = _analytics_error(sec)
        if err:
            pdf.h1(title)
            pdf.para(f"Not included: {err}.", italic_note=True)
            continue
        if name == "plates":
            pdf.h1(title)
            pdf.stat_tiles([("plate reads", sec["reads"]), ("distinct plates", sec["distinct_plates"])])
            pdf.data_table("Most-read plates", [("Plate", "plate_text", 40), ("Reads", "reads", 20),
                                                ("Best OCR conf.", lambda r: f"{r['best_conf']:.2f}" if r.get('best_conf') is not None else None, 30),
                                                ("First seen", lambda r: _when(r, "first_seen"), 45),
                                                ("Last seen", lambda r: _when(r, "last_seen"), 45)], sec["top"], inline=15)
        elif name == "theft":
            pdf.data_table(title, [("Plate", "plate_text", 35), ("Drone", "drone_id", 40),
                                   ("Detected", lambda r: _when(r, "detected_at"), 45),
                                   ("Conf.", "confidence", 20), ("Vehicle", "vehicle_type", 40)], sec["hits"])
        elif name == "crowd":
            pdf.data_table(f"{title}: peak per drone", [("Drone", "drone_id", 60), ("Peak count", "peak", 35),
                                                        ("Average", "avg", 35), ("Readings", "readings", 30)],
                           sec["per_drone"])
            if sec["hourly"]:
                pdf.bar_chart("Crowd peak per hour (IST)",
                              [(str(h["hour_ist"])[5:16].replace("T", " "), h["peak"]) for h in sec["hourly"]],
                              max_bars=24)
        elif name == "intrusion":
            pdf.data_table(f"{title}: breaches per zone", [("Zone", "zone_id", 55), ("Entries", "entries", 25),
                                                           ("Breaches", "breaches", 25), ("Max dwell s", "max_dwell_s", 30),
                                                           ("Avg dwell s", "avg_dwell_s", 30)], sec["zones"])
        else:
            pdf.data_table(title, [("Type", "event_type", 70), ("Class / severity",
                                                              lambda r: r.get("class_name") or r.get("severity"), 60),
                                   ("Count", "n", 25)], sec["by_type"])


def _alert_table(pdf, title, alerts):
    pdf.data_table(title, [("Time (IST)", lambda r: _when(r, "ts"), 30), ("Type", "type", 40),
                           ("Severity", "severity", 18), ("Drone", "drone_id", 30), ("Zone", "zone", 22),
                           ("Ack (min)", "minutes_to_ack", 16), ("Disposition", "disposition", 26)], alerts)


def _evidence_table(pdf, title, evidence):
    pdf.data_table(title, [("Evidence", "id", 32), ("Type", "event_type", 36),
                           ("Captured (IST)", lambda r: _when(r, "capture_ts", "ts"), 30),
                           ("Drone", "drone_id", 26), ("Image", "image_match", 16),
                           ("Record SHA-256", lambda r: (r.get("record_sha256") or "")[:16], 42)], evidence)


def _map(pdf, points, title="Map of located events"):
    png, caption = render_map(points)
    pdf.map_image(title, png, caption)


# ── templates ────────────────────────────────────────────────────────────────
def render_daily_sitrep(pdf: ReportPDF, data: dict, params: dict):
    ack = data["ack"]
    pdf.stat_tiles([("missions", len(data["missions"])), ("open missions", len(data["open_missions"])),
                    ("alerts", ack["total"]), ("acknowledged %", ack["ack_rate_pct"]),
                    ("incidents", len(data["incidents"])), ("evidence items", len(data["evidence"]))])
    pdf.bar_chart("Alerts by severity", list(data["alerts_by_severity"].items()))
    pdf.bar_chart("Alerts by district", list(data["alerts_by_jurisdiction"].items()))
    pdf.data_table("Missions", [("Mission", "mission_id", 45), ("Drone", "drone_id", 30),
                                ("District", "jurisdiction", 30), ("Start (IST)", lambda r: _when(r, "started_at"), 28),
                                ("End (IST)", lambda r: _when(r, "ended_at") if not r["open"] else "open", 28),
                                ("Hours today", "hours_in_period", 21)], data["missions"])
    _alert_table(pdf, "Alerts", data["alerts"])
    pdf.data_table("Operator incidents", [("Time (IST)", lambda r: _when(r, "timestamp"), 30), ("Title", "title", 70),
                                          ("Severity", "severity", 22), ("Mission", "mission_id", 40),
                                          ("Status", "status", 20)], data["incidents"])
    _module_sections(pdf, data["analytics"])
    _map(pdf, data["map_points"])


def render_mission_after_action(pdf: ReportPDF, data: dict, params: dict):
    m = data["mission"]
    pdf.kv([("Mission", f"{m.get('name')} ({m['mission_id']})"), ("Drone", m.get("drone_id")),
            ("Operator", m.get("operator_name")), ("Supervisor", m.get("supervisor")),
            ("District", m.get("jurisdiction")), ("Zone", m.get("zone")), ("Objective", m.get("objective")),
            ("Start (IST)", _when(m, "started_at")),
            ("End (IST)", "still open" if m["open"] else _when(m, "ended_at")),
            ("Duration", f"{m['hours']} h" if m.get("hours") is not None else None)])
    ack = data["ack"]
    pdf.stat_tiles([("alerts", ack["total"]), ("acknowledged %", ack["ack_rate_pct"]),
                    ("median min to ack", ack["median_minutes_to_ack"]), ("incidents", len(data["incidents"])),
                    ("evidence", len(data["evidence"]))])
    pdf.data_table("Timeline", [("Time (IST)", lambda r: _when(r, "ts"), 32), ("Kind", "kind", 22),
                                ("Event", "text", 100), ("Severity", "severity", 26)], data["timeline"], inline=40)
    _alert_table(pdf, "Alerts tagged to this mission", data["alerts"])
    if data["window_alerts"]:
        pdf.data_table(f"Other alerts from {m.get('drone_id')} during the mission (not tagged to it)",
                       [("Time (IST)", lambda r: _when(r, "ts"), 32), ("Type", "type", 60),
                        ("Severity", "severity", 30)], data["window_alerts"])
    _evidence_table(pdf, "Evidence", data["evidence"])
    pdf.image_grid("Evidence gallery", _evidence_images(data["evidence"]),
                   note=f"First {GALLERY_LIMIT} evidence items; all are listed in the table above.")
    _module_sections(pdf, data["analytics"])
    _map(pdf, data["map_points"])


def render_evidence_register(pdf: ReportPDF, data: dict, params: dict):
    c = data["case"]
    pdf.kv([("Case number", c["case_number"]), ("FIR", c.get("fir_number")), ("Police station", c.get("police_station")),
            ("District", c.get("jurisdiction")), ("Title", c.get("title")), ("Status", c.get("status")),
            ("Opened", f"{_when(c, 'created_at')} by {c.get('created_by')}")])
    pdf.data_table("Evidence in this case", [
        ("Evidence", "id", 30), ("Type", "event_type", 30), ("Captured (IST)", lambda r: _when(r, "capture_ts", "ts"), 28),
        ("Added by", "added_by", 34), ("Integrity", "integrity", 18),
        ("Record SHA-256", lambda r: (r.get("record_sha256") or "")[:24], 42)], data["evidence"], inline=60)
    pdf.data_table("Chain of custody (audit log)", [
        ("Time", lambda r: _when(r, "ts"), 28), ("Action", "action", 36), ("By", "actor", 38),
        ("Item", "target_id", 30), ("Audit hash", lambda r: (r.get("hash") or "")[:16], 50)], data["custody"], inline=60)
    pdf.data_table("Exports", [("Export", "id", 40), ("Status", "status", 20), ("Requested by", "requested_by", 40),
                               ("Decided by", "decision_by", 40), ("Bundle SHA-256", lambda r: (r.get("bundle_sha256") or "")[:24], 42)],
                   data["exports"])
    pdf.image_grid("Evidence images", _evidence_images(data["evidence"]))
    _map(pdf, data["map_points"])


def render_anpr(pdf: ReportPDF, data: dict, params: dict):
    pdf.para("Contains personal data (vehicle registration numbers). Handle under the DPDP Act 2023 and "
             "department policy; do not circulate outside the investigation.", 7.5, italic_note=True)
    _module_sections(pdf, data["analytics"])
    _evidence_table(pdf, "Watchlist hits recorded as evidence", data["theft_evidence"])
    pdf.image_grid("Watchlist hit images", _evidence_images(data["theft_evidence"]))
    _map(pdf, data["map_points"], "Locations of watchlist hits")


def render_crowd(pdf: ReportPDF, data: dict, params: dict):
    _module_sections(pdf, data["analytics"])
    _evidence_table(pdf, "Crowd surges (threshold events)", data["surges"])
    pdf.image_grid("Surge frames and heatmaps", _evidence_images(data["surges"]))
    _map(pdf, data["map_points"], "Locations of crowd surges")


def render_intrusion(pdf: ReportPDF, data: dict, params: dict):
    r = data["response"]
    pdf.stat_tiles([("breach alerts", r["total"]), ("acknowledged %", r["ack_rate_pct"]),
                    ("median min to ack", r["median_minutes_to_ack"]), ("90th pct min", r["p90_minutes_to_ack"])])
    _module_sections(pdf, data["analytics"])
    _alert_table(pdf, "Breach alerts and response", data["alerts"])
    _map(pdf, data["map_points"], "Breach locations (ground position of the target)")


def render_detection_quality(pdf: ReportPDF, data: dict, params: dict):
    o = data["overall"]
    pdf.para("Precision = true positives / (true positives + false positives), from the disposition officers "
             "record when acknowledging an alert. Alerts without a disposition are not counted.", 7.5, italic_note=True)
    pdf.stat_tiles([("AI alerts", o["alerts"]), ("reviewed", o["reviewed"]), ("true positive", o["true_positive"]),
                    ("false positive", o["false_positive"]), ("precision %", o["precision_pct"])])
    cols = [("Alerts", "alerts", 20), ("Reviewed", "reviewed", 22), ("TP", "true_positive", 18),
            ("FP", "false_positive", 18), ("Precision %", "precision_pct", 26)]
    pdf.data_table("By detection rule", [("Rule", "rule", 70)] + cols, data["by_rule"])
    pdf.data_table("By confidence band", [("Confidence", "band", 70)] + cols, data["by_confidence"])


def render_weekly_response(pdf: ReportPDF, data: dict, params: dict):
    a = data["ack"]
    pdf.stat_tiles([("alerts", a["total"]), ("acknowledged %", a["ack_rate_pct"]),
                    ("median min to ack", a["median_minutes_to_ack"]), ("90th pct min", a["p90_minutes_to_ack"]),
                    ("incidents", len(data["incidents"]))])
    pdf.bar_chart("Alerts per day (IST)", [(d["day"], d["total"]) for d in data["per_day"]])
    pdf.data_table("Response per day", [("Day", "day", 30), ("Alerts", "total", 22), ("Acked", "acknowledged", 22),
                                        ("Ack %", "ack_rate_pct", 22), ("Median min", "median_minutes_to_ack", 30),
                                        ("90th pct min", "p90_minutes_to_ack", 30)], data["per_day"])
    pdf.data_table("Response by severity", [("Severity", lambda r: r["sev"], 30), ("Alerts", "total", 22),
                                            ("Ack %", "ack_rate_pct", 22), ("Median min", "median_minutes_to_ack", 30),
                                            ("90th pct min", "p90_minutes_to_ack", 30)],
                   [dict(v, sev=k) for k, v in data["by_severity"].items()])
    _alert_table(pdf, "All alerts", data["alerts"])


def render_drone_utilisation(pdf: ReportPDF, data: dict, params: dict):
    pdf.para(f"Hours are counted inside the period only ({data['period_hours']} h); open missions are listed "
             "separately and not counted as utilisation.", 7.5, italic_note=True)
    pdf.data_table("Per drone", [("Drone", "drone_id", 40), ("Missions", "missions", 22), ("Closed h", "closed_hours", 24),
                                 ("Utilisation %", "utilisation_pct", 28), ("Open missions", "open_missions", 28),
                                 ("Open h so far", "open_hours_so_far", 28)], data["per_drone"])
    pdf.bar_chart("Closed flight hours per drone", [(d["drone_id"], d["closed_hours"]) for d in data["per_drone"]],
                  unit=" h")
    pdf.data_table("Missions", [("Mission", "mission_id", 50), ("Drone", "drone_id", 30),
                                ("Start (IST)", lambda r: _when(r, "started_at"), 32),
                                ("End (IST)", lambda r: "open" if r["open"] else _when(r, "ended_at"), 32),
                                ("Hours in period", "hours_in_period", 30)], data["missions"])


REQUESTERS = ("control_room_officer",)        # request → a senior approves
GENERATORS = ("senior_officer",)              # generate directly

REPORT_TYPES: Dict[str, dict] = {
    "daily_sitrep": {"title": "Daily Situation Report", "period": "day",
                     "description": "Missions, alerts, incidents, evidence and analytics for one IST day, by district.",
                     "collect": rd.collect_daily_sitrep, "render": render_daily_sitrep},
    "mission_after_action": {"title": "Mission After-Action Report", "period": "mission",
                             "description": "Timeline, alerts, evidence gallery, analytics and map for one mission.",
                             "collect": rd.collect_mission_after_action, "render": render_mission_after_action},
    "evidence_register": {"title": "Evidence Register (case)", "period": "case",
                          "description": "Evidence of one case with hashes, chain of custody and exports.",
                          "collect": rd.collect_evidence_register, "render": render_evidence_register},
    "anpr": {"title": "ANPR / Vehicle-of-Interest Report", "period": "range",
             "description": "Plate reads, most-read plates, watchlist hits with images and locations.",
             "collect": rd.collect_anpr, "render": render_anpr},
    "crowd": {"title": "Crowd Event Report", "period": "range",
              "description": "Peak counts per drone, hourly peaks, threshold surges with frames and heatmaps.",
              "collect": rd.collect_crowd, "render": render_crowd},
    "intrusion": {"title": "Intrusion / Geofence Report", "period": "range",
                  "description": "Breaches per zone, dwell times, response times and breach locations.",
                  "collect": rd.collect_intrusion, "render": render_intrusion},
    "detection_quality": {"title": "Detection Quality Report", "period": "range",
                          "description": "Precision of AI alerts by rule and confidence, from officers' dispositions.",
                          "collect": rd.collect_detection_quality, "render": render_detection_quality},
    "weekly_response": {"title": "Weekly Response Report", "period": "week",
                        "description": "Alert volume and time-to-acknowledge per day and severity for one IST week.",
                        "collect": rd.collect_weekly_response, "render": render_weekly_response},
    "drone_utilisation": {"title": "Drone Utilisation Report", "period": "range",
                          "description": "Flight hours per drone inside the period; open missions shown apart.",
                          "collect": rd.collect_drone_utilisation, "render": render_drone_utilisation},
}
# Old name kept so existing UI buttons / stored rows keep working
REPORT_TYPES["daily_incident"] = dict(REPORT_TYPES["daily_sitrep"], hidden=True)


def title_for(report_type: str, params: dict) -> str:
    spec = REPORT_TYPES[report_type]
    suffix = params.get("mission_id") or params.get("case_id") or _period_label(params)
    return f"{spec['title']} — {suffix}" if suffix else spec["title"]


def render(report_type: str, data: dict, params: dict, report_id: str, watermark: str) -> bytes:
    spec = REPORT_TYPES[report_type]
    pdf = ReportPDF(spec["title"], subtitle=title_for(report_type, params).split(" — ", 1)[-1]
                    if " — " in title_for(report_type, params) else "",
                    report_id=report_id, watermark=watermark)
    spec["render"](pdf, data, params)
    pdf.ln(4)
    pdf.para(f"Data up to {fmt_ist(params.get('data_cutoff'))}. Scope: "
             f"{(params.get('scope') or {}).get('jurisdiction') or 'statewide'}. "
             "Generated by DVAS; figures come from the same data as the on-screen preview.", 7, italic_note=True)
    return pdf.output_bytes()


def public_catalog(role: str) -> List[dict]:
    """Report types the UI may offer to this role."""
    if role not in REQUESTERS + GENERATORS:
        return []
    return [{"type": k, "title": v["title"], "period": v["period"], "description": v["description"],
             "mode": "generate" if role in GENERATORS else "request"}
            for k, v in REPORT_TYPES.items() if not v.get("hidden")]
