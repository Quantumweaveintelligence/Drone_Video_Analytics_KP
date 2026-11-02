"""
PDF generation services for Shift summaries and Section 65B bundles.
"""
from datetime import datetime, timezone
from fpdf import FPDF

# ---------- Edge Operator: End-of-Shift PDF summary ----------
def _make_shift_pdf(
    user: dict, missions: list, buffer_stats: dict, anomalies: list
) -> bytes:
    def _s(x):
        return str(x).encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "END-OF-SHIFT SUMMARY", ln=1, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Drone Edge Operator - Session Handover Report", ln=1, align="C")
    pdf.ln(3)
    pdf.set_draw_color(120, 120, 120)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)

    def kv(k, v):
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(55, 6, _s(f"{k}:"), ln=0)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(135, 6, _s(v))

    kv("Operator", f"{user.get('name', '-')} ({user.get('email', '-')})")
    kv("Role", user.get("role", "-"))
    kv("Jurisdiction", user.get("jurisdiction", "-"))
    kv("Shift ended (UTC)", datetime.now(timezone.utc).isoformat())
    kv("Missions covered", ", ".join(m.get("name", "-") for m in missions) or "—")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Buffer & Stream Metrics", ln=1)
    pdf.set_font("Helvetica", "", 10)
    kv("Buffer utilisation (avg)", f"{buffer_stats.get('avg_pct', '—')}%")
    kv("Overwrite cycles", str(buffer_stats.get("overwrite_cycles", 0)))
    kv("Stream drops", str(buffer_stats.get("drops", 0)))
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Anomalies / Malfunctions reported", ln=1)
    pdf.set_font("Helvetica", "", 9)
    if not anomalies:
        pdf.multi_cell(0, 5, "None during this shift.")
    else:
        for a in anomalies:
            pdf.multi_cell(
                0,
                5,
                _s(f"- [{a.get('ts', '')}] {a.get('type', '-')}: {a.get('note', '-')}"),
            )
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        0,
        4,
        "This handover is generated at the edge and relayed to Central Ingest. "
        "No decisions are taken on device. All operator actions during the shift are recorded in the tamper-evident DVAS audit log.",
    )
    pdf.ln(6)
    pdf.cell(90, 6, "_______________________", ln=0)
    pdf.cell(0, 6, "_______________________", ln=1)
    pdf.cell(90, 5, "Operator Signature", ln=0)
    pdf.cell(0, 5, "Ingest Supervisor Ack.", ln=1)
    return bytes(pdf.output(dest="S"))


# ---------- Section 65B Evidence Bundle ----------
def _make_65b_pdf(evidence: dict, export: dict, decision_by: str) -> bytes:
    def _s(x):
        return str(x).encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "SECTION 65B(4) CERTIFICATE", ln=1, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Indian Evidence Act, 1872 - Electronic Records", ln=1, align="C")
    pdf.ln(4)
    pdf.set_draw_color(120, 120, 120)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Issuing Authority", ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(
        0,
        5,
        "Kerala Police - Drone Video Analytics System (DVAS)\nCentral Evidence Vault, Police Headquarters, Thiruvananthapuram",
    )
    pdf.ln(2)

    def kv(k, v):
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(55, 6, _s(f"{k}:"), ln=0)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(135, 6, _s(v))

    kv("Evidence ID", evidence["id"])
    kv("Label", evidence["label"])
    kv("Event Type", evidence["event_type"])
    kv("Confidence", f"{evidence['confidence']}")
    kv("Drone / Mission", f"{evidence['drone_id']} / {evidence['mission_id']}")
    kv("Jurisdiction", evidence["jurisdiction"])
    kv("Captured At", evidence["ts"])
    kv("Duration (s)", evidence["duration_s"])
    kv("Retention Until", evidence.get("retention_until", "-"))
    pdf.ln(2)
    kv("Export Request ID", export["id"])
    kv("Requested By", export["requested_by"])
    kv("Justification", export["justification"])
    kv("Approved By", decision_by)
    kv("Approved At", export.get("decision_at", "-"))
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Statement of Compliance", ln=1)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(
        0,
        5,
        "I hereby certify that (a) the electronic record referred to above was produced by a computer "
        "system used regularly to store and process information for the purposes of activities lawfully "
        "carried on by Kerala Police; (b) during the material period of production, the computer was "
        "operating properly; and (c) the information contained in the electronic record reproduces "
        "information fed into the computer in the ordinary course of the said activities.\n\n"
        "Chain of custody is maintained by the DVAS audit log (tamper-evident, hash-chained). "
        "This bundle contains: (1) the confirmed-event image evidence, (2) event metadata JSON, "
        "(3) this Section 65B(4) certificate.",
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Bundle generated: {datetime.now(timezone.utc).isoformat()}", ln=1)
    pdf.ln(8)
    pdf.cell(90, 6, "_______________________", ln=0)
    pdf.cell(0, 6, "_______________________", ln=1)
    pdf.cell(90, 5, "Signature (Supervisory Officer)", ln=0)
    pdf.cell(0, 5, "Official Seal", ln=1)
    return bytes(pdf.output(dest="S"))



def _s(x):
    return str(x if x is not None else "").encode("latin-1", "replace").decode("latin-1")


def _format_officer_info(user: dict):
    name = user.get("name") or "Officer"
    role = user.get("role", "")
    role_labels = {
        "senior_officer": "Senior Officer",
        "control_room_officer": "Control Room Officer",
        "drone_operator": "Drone Operator",
        "system_administrator": "System Administrator"
    }
    role_label = role_labels.get(role, role.replace("_", " ").title()) if role else "Senior Officer"
    email = user.get("email") or "—"
    jurisdiction = user.get("jurisdiction") or "—"
    return name, role_label, email, jurisdiction


def _add_watermark(pdf: FPDF, text: str):
    clean_text = _s(text)
    font_size = 18 if len(clean_text) > 40 else (24 if len(clean_text) > 25 else 30)
    for page_num in range(1, pdf.pages_count + 1):
        pdf.page = page_num
        pdf.set_font("Helvetica", "B", font_size)
        pdf.set_text_color(225, 225, 225)
        try:
            pdf.rotate(45, pdf.w / 2, pdf.h / 2)
            offset_x = len(clean_text) * (font_size * 0.28)
            pdf.text(pdf.w / 2 - offset_x, pdf.h / 2, clean_text)
            pdf.rotate(0)
        except AttributeError:
            pass


def _render_police_header(pdf: FPDF, report_title: str, subtitle: str, user: dict):
    name, role_label, email, jur = _format_officer_info(user)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "KERALA POLICE - DRONE VIDEO ANALYTICS SYSTEM (DVAS)", ln=1, align="C")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, _s(report_title), ln=1, align="C")
    if subtitle:
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, _s(subtitle), ln=1, align="C")
    pdf.ln(2)
    pdf.set_draw_color(180, 180, 180)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    
    # Metadata credentials block
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(32, 5, "Authorised Officer:", ln=0)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(65, 5, _s(f"{name} ({role_label})"), ln=0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(22, 5, "Jurisdiction:", ln=0)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, _s(jur), ln=1)
    
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(32, 5, "Official Email:", ln=0)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(65, 5, _s(email), ln=0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(22, 5, "Classification:", ln=0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 5, "RESTRICTED / OFFICIAL POLICE RECORD", ln=1)
    pdf.ln(2)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)


def _render_police_footer(pdf: FPDF, user: dict):
    name, role_label, email, jur = _format_officer_info(user)
    pdf.ln(6)
    pdf.set_draw_color(180, 180, 180)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 7)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pdf.cell(0, 4, _s(f"Generated by: {name} ({role_label}) <{email}> at {now_str} | System: DVAS Central Command"), ln=1)
    pdf.cell(0, 4, "Tamper-evident audit chain logged under Indian Evidence Act Section 65B and Kerala Police Digital Protocols.", ln=1)


# 1. Daily Incident Summary
def _make_daily_incident_pdf(user: dict, alerts: list, evidence_items: list, missions: list, target_date: str) -> bytes:
    name, role_label, email, jur = _format_officer_info(user)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(True, margin=15)
    
    _render_police_header(pdf, f"DAILY INCIDENT SUMMARY - {target_date}", f"District Command: {jur} | Operational Sorties & Alerts Audit", user)
    
    # KPIs
    total_alerts = len(alerts)
    acknowledged = sum(1 for a in alerts if a.get('acknowledged_at'))
    unacknowledged = total_alerts - acknowledged
    ack_rate = (acknowledged / total_alerts * 100) if total_alerts else 100.0
    
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Operational Summary KPIs", ln=1)
    pdf.set_font("Helvetica", "", 9)
    
    col_kpi = [38, 38, 38, 38, 38]
    pdf.cell(col_kpi[0], 6, _s(f"Total Alerts: {total_alerts}"), border=1)
    pdf.cell(col_kpi[1], 6, _s(f"Acknowledged: {acknowledged}"), border=1)
    pdf.cell(col_kpi[2], 6, _s(f"Ack Rate: {ack_rate:.1f}%"), border=1)
    pdf.cell(col_kpi[3], 6, _s(f"Missions: {len(missions)}"), border=1)
    pdf.cell(col_kpi[4], 6, _s(f"Evidence: {len(evidence_items)}"), border=1, ln=1)
    pdf.ln(4)
    
    # By Type Table
    by_type = {}
    for a in alerts:
        t = a.get("type", a.get("event_type", "unknown")).replace("_", " ").title()
        by_type[t] = by_type.get(t, 0) + 1
        
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Incident Classification Breakdown", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(80, 6, "Incident Type", border=1)
    pdf.cell(40, 6, "Count", border=1)
    pdf.cell(40, 6, "Percentage Share", border=1, ln=1)
    
    pdf.set_font("Helvetica", "", 8)
    if not by_type:
        pdf.cell(160, 6, "No analytical alerts recorded for this specific date filter.", border=1, ln=1)
    else:
        for t, c in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            pct = (c / total_alerts * 100) if total_alerts else 0
            pdf.cell(80, 6, _s(t), border=1)
            pdf.cell(40, 6, _s(str(c)), border=1)
            pdf.cell(40, 6, _s(f"{pct:.1f}%"), border=1, ln=1)
    pdf.ln(4)
    
    # Active Missions Table
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Active Missions & Assigned Drone Operators", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(55, 6, "Mission Name", border=1)
    pdf.cell(35, 6, "Mission ID", border=1)
    pdf.cell(35, 6, "Operator Name", border=1)
    pdf.cell(35, 6, "Drone ID", border=1)
    pdf.cell(30, 6, "Zone / Status", border=1, ln=1)
    
    pdf.set_font("Helvetica", "", 8)
    if not missions:
        pdf.cell(190, 6, "No missions scheduled or logged for this specific target date.", border=1, ln=1)
    else:
        for m in missions[:10]:
            name = m.get("name", m.get("mission_id", ""))[:25]
            mid = m.get("mission_id", "")[:18]
            op = (m.get("operator_name") or "—")[:18]
            did = (m.get("drone_id") or "—")[:18]
            zone = (m.get("zone") or m.get("status") or "—")[:15]
            pdf.cell(55, 6, _s(name), border=1)
            pdf.cell(35, 6, _s(mid), border=1)
            pdf.cell(35, 6, _s(op), border=1)
            pdf.cell(35, 6, _s(did), border=1)
            pdf.cell(30, 6, _s(zone), border=1, ln=1)
    pdf.ln(4)
    
    # Alerts Sample Table (top 20)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, f"Incident Log (Showing Top {min(20, total_alerts)} Events)", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(35, 6, "Timestamp", border=1)
    pdf.cell(40, 6, "Incident Type", border=1)
    pdf.cell(40, 6, "Drone / Mission", border=1)
    pdf.cell(35, 6, "Zone", border=1)
    pdf.cell(20, 6, "Confidence", border=1)
    pdf.cell(20, 6, "Ack Status", border=1, ln=1)
    
    pdf.set_font("Helvetica", "", 8)
    if not alerts:
        pdf.cell(190, 6, "No specific incident logs for this date.", border=1, ln=1)
    else:
        for a in alerts[:20]:
            ts = str(a.get("ts", ""))[:19]
            typ = str(a.get("type", a.get("event_type", ""))).replace("_", " ")[:20]
            drone = str(a.get("drone_id", a.get("mission_id", "")))[:20]
            zone = str(a.get("zone", a.get("zone_id", "")))[:18]
            conf = f"{float(a.get('confidence', 0)):.2f}"
            ack = "Yes" if a.get("acknowledged_at") else "No"
            pdf.cell(35, 6, _s(ts), border=1)
            pdf.cell(40, 6, _s(typ), border=1)
            pdf.cell(40, 6, _s(drone), border=1)
            pdf.cell(35, 6, _s(zone), border=1)
            pdf.cell(20, 6, _s(conf), border=1)
            pdf.cell(20, 6, _s(ack), border=1, ln=1)
            
    _render_police_footer(pdf, user)
    _add_watermark(pdf, f"CONFIDENTIAL - {name} ({role_label})")
    return bytes(pdf.output(dest="S"))


# 2. Weekly Response-Time Report
def _make_weekly_response_pdf(user: dict, alerts: list, missions: list, week_start_date: str) -> bytes:
    name, role_label, email, jur = _format_officer_info(user)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(True, margin=15)
    
    _render_police_header(pdf, "WEEKLY RESPONSE-TIME & SLA PERFORMANCE AUDIT", f"Week Commencing: {week_start_date} | State HQ Operations", user)
    
    # Calculate response times
    response_times = []
    for a in alerts:
        if a.get('acknowledged_at') and a.get('ts'):
            try:
                ts = datetime.fromisoformat(a['ts'].replace("Z", "+00:00"))
                ack = datetime.fromisoformat(a['acknowledged_at'].replace("Z", "+00:00"))
                response_times.append(max(0, (ack - ts).total_seconds()))
            except Exception:
                pass

    total_alerts = len(alerts)
    total_ack = sum(1 for a in alerts if a.get('acknowledged_at'))
    ack_rate = (total_ack / total_alerts * 100) if total_alerts else 100.0
    mean_rt = sum(response_times) / len(response_times) if response_times else 0.0
    median_rt = sorted(response_times)[len(response_times)//2] if response_times else 0.0
    sla_compliant = sum(1 for rt in response_times if rt <= 60)
    sla_pct = (sla_compliant / len(response_times) * 100) if response_times else 0.0
    
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Executive SLA & Dispatch Metrics", ln=1)
    pdf.set_font("Helvetica", "", 9)
    
    col_w = [47, 47, 47, 47]
    pdf.cell(col_w[0], 6, _s(f"Total Incidents: {total_alerts}"), border=1)
    pdf.cell(col_w[1], 6, _s(f"Acknowledged: {total_ack} ({ack_rate:.1f}%)"), border=1)
    pdf.cell(col_w[2], 6, _s(f"Mean Response: {mean_rt:.1f}s"), border=1)
    pdf.cell(col_w[3], 6, _s(f"60s SLA Compliance: {sla_pct:.1f}%"), border=1, ln=1)
    pdf.ln(4)
    
    # Daily Breakdown
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Daily Incident & Response Latency Log", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(45, 6, "Date", border=1)
    pdf.cell(35, 6, "Incident Count", border=1)
    pdf.cell(35, 6, "Acknowledged", border=1)
    pdf.cell(40, 6, "Avg Response Time", border=1)
    pdf.cell(35, 6, "SLA Compliance", border=1, ln=1)
    
    daily = {}
    for a in alerts:
        d = str(a.get("ts", ""))[:10]
        if not d:
            continue
        if d not in daily:
            daily[d] = {"count": 0, "ack": 0, "rts": []}
        daily[d]["count"] += 1
        if a.get("acknowledged_at") and a.get("ts"):
            daily[d]["ack"] += 1
            try:
                ts = datetime.fromisoformat(a['ts'].replace("Z", "+00:00"))
                ack = datetime.fromisoformat(a['acknowledged_at'].replace("Z", "+00:00"))
                daily[d]["rts"].append(max(0, (ack - ts).total_seconds()))
            except Exception:
                pass
                
    pdf.set_font("Helvetica", "", 8)
    if not daily:
        pdf.cell(190, 6, "No daily log available for the requested period.", border=1, ln=1)
    else:
        for d, st in sorted(daily.items())[:7]:
            avg_rt = sum(st["rts"])/len(st["rts"]) if st["rts"] else 0.0
            sla_cnt = sum(1 for r in st["rts"] if r <= 60)
            c_sla = (sla_cnt / len(st["rts"]) * 100) if st["rts"] else 0.0
            pdf.cell(45, 6, _s(d), border=1)
            pdf.cell(35, 6, _s(str(st["count"])), border=1)
            pdf.cell(35, 6, _s(str(st["ack"])), border=1)
            pdf.cell(40, 6, _s(f"{avg_rt:.1f} seconds"), border=1)
            pdf.cell(35, 6, _s(f"{c_sla:.1f}%"), border=1, ln=1)
    pdf.ln(4)
    
    # Operator Performance Table
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Drone Operator Deployment & Mission Activity", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(60, 6, "Operator Name", border=1)
    pdf.cell(45, 6, "Assigned Missions", border=1)
    pdf.cell(45, 6, "Jurisdiction", border=1)
    pdf.cell(40, 6, "Cumulative Flight Time", border=1, ln=1)
    
    op_summary = {}
    for m in missions:
        op = m.get("operator_name") or "—"
        if op not in op_summary:
            op_summary[op] = {"missions": 0, "jur": m.get("jurisdiction") or "—", "hours": 0.0}
        op_summary[op]["missions"] += 1
        if m.get("started_at") and m.get("ended_at"):
            try:
                s = datetime.fromisoformat(m["started_at"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(m["ended_at"].replace("Z", "+00:00"))
                op_summary[op]["hours"] += max(0.5, (e - s).total_seconds() / 3600)
            except Exception:
                op_summary[op]["hours"] += 0.0
        else:
            op_summary[op]["hours"] += 0.0
            
    pdf.set_font("Helvetica", "", 8)
    for op, st in sorted(op_summary.items(), key=lambda x: x[1]["hours"], reverse=True)[:8]:
        pdf.cell(60, 6, _s(f"{op} (Drone Operator)"), border=1)
        pdf.cell(45, 6, _s(str(st["missions"])), border=1)
        pdf.cell(45, 6, _s(st["jur"]), border=1)
        pdf.cell(40, 6, _s(f"{st['hours']:.1f} hrs"), border=1, ln=1)
        
    _render_police_footer(pdf, user)
    _add_watermark(pdf, f"CONFIDENTIAL - {name} ({role_label})")
    return bytes(pdf.output(dest="S"))


# 3. Drone Utilisation Report
def _make_drone_utilisation_pdf(user: dict, missions: list, drones: list, period_days: int = 30) -> bytes:
    name, role_label, email, jur = _format_officer_info(user)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(True, margin=15)
    
    _render_police_header(pdf, f"DRONE FLEET UTILISATION & READINESS AUDIT", f"{period_days}-Day Audit Period | State Airborne Surveillance Wing", user)
    
    total_drones = len(drones)
    active_drones = sum(1 for d in drones if d.get('status') in ('active', 'online', 'draining'))
    total_missions = len(missions)
    total_hours = 0.0
    
    drone_stats = {}
    for d in drones:
        did = d.get("id", "DRN-UNKNOWN")
        drone_stats[did] = {
            "count": 0,
            "hours": 0.0,
            "operator": d.get("operator_name") or "—",
            "jurisdiction": d.get("jurisdiction") or "—",
            "battery": d.get("battery_pct", 0),
            "status": d.get("status", "Active").capitalize()
        }
        
    for m in missions:
        did = m.get("drone_id")
        h = 0.0
        if m.get("started_at") and m.get("ended_at"):
            try:
                s = datetime.fromisoformat(m["started_at"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(m["ended_at"].replace("Z", "+00:00"))
                h = max(0.5, (e - s).total_seconds() / 3600)
            except Exception:
                pass
        total_hours += h
        if did and did in drone_stats:
            drone_stats[did]["count"] += 1
            drone_stats[did]["hours"] += h
            if m.get("operator_name"):
                drone_stats[did]["operator"] = m.get("operator_name")
            if m.get("jurisdiction"):
                drone_stats[did]["jurisdiction"] = m.get("jurisdiction")
                
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Airborne Fleet Overview KPIs", ln=1)
    pdf.set_font("Helvetica", "", 9)
    
    col_w = [47, 47, 47, 47]
    pdf.cell(col_w[0], 6, _s(f"Total Airframes: {total_drones}"), border=1)
    pdf.cell(col_w[1], 6, _s(f"Operational Sorties: {total_missions}"), border=1)
    pdf.cell(col_w[2], 6, _s(f"Flight Hours: {total_hours:.1f}h"), border=1)
    pdf.cell(col_w[3], 6, _s(f"Fleet Readiness: {((active_drones/total_drones*100) if total_drones else 100):.1f}%"), border=1, ln=1)
    pdf.ln(4)
    
    # Per Drone Table
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Airframe Sortie Registry & Assigned Operators", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(40, 6, "Drone Identifier", border=1)
    pdf.cell(40, 6, "Primary Operator", border=1)
    pdf.cell(35, 6, "Jurisdiction", border=1)
    pdf.cell(25, 6, "Sorties", border=1)
    pdf.cell(25, 6, "Flight Hrs", border=1)
    pdf.cell(25, 6, "Readiness", border=1, ln=1)
    
    pdf.set_font("Helvetica", "", 8)
    for did, st in sorted(drone_stats.items(), key=lambda x: x[1]["hours"], reverse=True)[:12]:
        pdf.cell(40, 6, _s(did)[:20], border=1)
        pdf.cell(40, 6, _s(st["operator"])[:20], border=1)
        pdf.cell(35, 6, _s(st["jurisdiction"])[:18], border=1)
        pdf.cell(25, 6, _s(str(st["count"])), border=1)
        pdf.cell(25, 6, _s(f"{st['hours']:.1f}h"), border=1)
        pdf.cell(25, 6, _s(f"{st['status']} ({st['battery']}%)"), border=1, ln=1)
    pdf.ln(4)
    
    # Operator Standings
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Certified Drone Operator Activity Rankings", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(60, 6, "Officer Name", border=1)
    pdf.cell(45, 6, "Assigned Role", border=1)
    pdf.cell(45, 6, "Home Command", border=1)
    pdf.cell(40, 6, "Logged Airtime", border=1, ln=1)
    
    op_ranks = {}
    for st in drone_stats.values():
        op = st["operator"]
        if op not in op_ranks:
            op_ranks[op] = {"hours": 0.0, "jur": st["jurisdiction"]}
        op_ranks[op]["hours"] += st["hours"]
        
    pdf.set_font("Helvetica", "", 8)
    for op, st in sorted(op_ranks.items(), key=lambda x: x[1]["hours"], reverse=True):
        pdf.cell(60, 6, _s(op), border=1)
        pdf.cell(45, 6, "Drone Operator", border=1)
        pdf.cell(45, 6, _s(st["jur"]), border=1)
        pdf.cell(40, 6, _s(f"{st['hours']:.1f} hours"), border=1, ln=1)
        
    _render_police_footer(pdf, user)
    _add_watermark(pdf, f"CONFIDENTIAL - {name} ({role_label})")
    return bytes(pdf.output(dest="S"))


# 4. Mission After-Action Report (AAR)
def _make_mission_aar_pdf(user: dict, mission: dict, alerts: list, evidence_items: list,
                          window_alerts: list = None) -> bytes:
    name, role_label, email, jur = _format_officer_info(user)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(True, margin=15)
    
    mid = mission.get("mission_id", "MSN-UNKNOWN")
    m_name = mission.get("name", mid)
    
    _render_police_header(pdf, "MISSION AFTER-ACTION REPORT (AAR)", f"Operation: {m_name} ({mid})", user)
    
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Mission Command & Operational Profile", ln=1)
    pdf.set_font("Helvetica", "", 9)
    
    # Calculate duration
    dur_str = "—"
    if mission.get("started_at") and mission.get("ended_at"):
        try:
            s = datetime.fromisoformat(mission["started_at"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(mission["ended_at"].replace("Z", "+00:00"))
            total_sec = max(0, (e - s).total_seconds())
            dur_str = f"{int(total_sec // 3600)}h {int((total_sec % 3600) // 60)}m ({total_sec / 3600:.2f}h)"
        except Exception:
            pass
    elif alerts:
        dur_str = "Continuous Surveillance (Active Sortie)"

    op_name = mission.get("operator_name") or "—"
    if op_name in ("System Recovered", "", None):
        op_name = "—"
    supervisor_name = mission.get("supervisor") or "—"
    drone_id = mission.get("drone_id") or "—"
    zone = mission.get("zone") or "—"
    jurisdiction = mission.get("jurisdiction") or jur
    objective = mission.get("objective") or "—"
    
    def kv_box(k, v, w1=45, w2=145):
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(w1, 5, _s(f"{k}:"), border=0)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(w2, 5, _s(v), border=0, ln=1)

    kv_box("Mission Title", m_name)
    kv_box("Mission Identifier", mid)
    kv_box("Primary Drone Operator", f"{op_name} (Drone Operator)")
    kv_box("Supervising Officer", f"{supervisor_name} (Senior Officer)")
    kv_box("Reporting Officer", f"{name} ({role_label})")
    kv_box("Airframe Deployed", drone_id)
    kv_box("Operational Zone", zone)
    kv_box("District Jurisdiction", jurisdiction)
    kv_box("Mission Timeline", f"Start: {mission.get('started_at', '—')} | End: {mission.get('ended_at', '—')}")
    kv_box("Sortie Duration", dur_str)
    kv_box("Strategic Objective", objective)
    pdf.ln(3)
    
    # Mission KPIs
    total_alerts = len(alerts)
    ack_alerts = sum(1 for a in alerts if a.get("acknowledged_at"))
    ack_rate = (ack_alerts / total_alerts * 100) if total_alerts else 100.0
    
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Operational Performance & Incident Metrics", ln=1)
    pdf.set_font("Helvetica", "", 8)
    
    col_w = [47, 47, 47, 47]
    pdf.cell(col_w[0], 6, _s(f"Total Alerts Logged: {total_alerts}"), border=1)
    pdf.cell(col_w[1], 6, _s(f"Acknowledged Rate: {ack_rate:.1f}%"), border=1)
    pdf.cell(col_w[2], 6, _s(f"Evidence Vault Clips: {len(evidence_items)}"), border=1)
    sla = f"{(ack_alerts/total_alerts*100):.0f}%" if total_alerts else "N/A"
    pdf.cell(col_w[3], 6, _s(f"Compliance SLA: {sla}"), border=1, ln=1)
    pdf.ln(4)
    
    # Incident Log
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, f"Incident Audit Log (Displaying Top {min(15, total_alerts)} Confirmed Events)", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(40, 6, "Timestamp", border=1)
    pdf.cell(45, 6, "Incident Classification", border=1)
    pdf.cell(40, 6, "Operational Zone", border=1)
    pdf.cell(30, 6, "Confidence", border=1)
    pdf.cell(35, 6, "Disposition", border=1, ln=1)
    
    pdf.set_font("Helvetica", "", 8)
    if not alerts:
        pdf.cell(190, 6, "No high-priority alerts triggered during this operational window.", border=1, ln=1)
    else:
        for a in alerts[:15]:
            ts = str(a.get("ts", ""))[:19]
            typ = str(a.get("type", a.get("event_type", "anomaly"))).replace("_", " ").title()[:20]
            az = str(a.get("zone", a.get("zone_id", zone)))[:20]
            conf = f"{float(a.get('confidence', 0)):.2f}"
            status = "Verified & Closed" if a.get("acknowledged_at") else "Under Investigation"
            pdf.cell(40, 6, _s(ts), border=1)
            pdf.cell(45, 6, _s(typ), border=1)
            pdf.cell(40, 6, _s(az), border=1)
            pdf.cell(30, 6, _s(conf), border=1)
            pdf.cell(35, 6, _s(status), border=1, ln=1)
    pdf.ln(4)

    # Untagged alerts from the same drone inside the mission window — listed apart,
    # not counted in the KPIs above, because they are not linked to this mission
    if window_alerts:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, _s(f"Other Alerts from {drone_id} in the Mission Window (not tagged to this mission)"), ln=1)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(40, 6, "Timestamp", border=1)
        pdf.cell(60, 6, "Incident Classification", border=1)
        pdf.cell(30, 6, "Confidence", border=1)
        pdf.cell(60, 6, "Disposition", border=1, ln=1)
        pdf.set_font("Helvetica", "", 8)
        for a in window_alerts[:15]:
            pdf.cell(40, 6, _s(str(a.get("ts", ""))[:19]), border=1)
            pdf.cell(60, 6, _s(str(a.get("type", "anomaly")).replace("_", " ").title()[:28]), border=1)
            pdf.cell(30, 6, _s(f"{float(a.get('confidence') or 0):.2f}"), border=1)
            pdf.cell(60, 6, _s("Acknowledged" if a.get("acknowledged_at") else "Open"), border=1, ln=1)
        pdf.ln(4)
    
    # Evidence Vault Items
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Captured Evidence Registry (Chain of Custody Preserved)", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(45, 6, "Evidence Identifier", border=1)
    pdf.cell(50, 6, "Event Classification", border=1)
    pdf.cell(30, 6, "Clip Length", border=1)
    pdf.cell(35, 6, "Legal Hold Status", border=1)
    pdf.cell(30, 6, "Retention Until", border=1, ln=1)
    
    pdf.set_font("Helvetica", "", 8)
    if not evidence_items:
        pdf.cell(190, 6, "No evidence items recorded for this mission", border=1, ln=1)
    else:
        for ev in evidence_items[:8]:
            eid = ev.get("id", "EVI-UNKNOWN")
            et = str(ev.get("event_type", "evidence")).replace("_", " ").title()[:22]
            dur = f"{ev.get('duration_s', 0)}s"
            hold = "LEGAL HOLD" if ev.get("legal_hold") else "Standard Retention"
            ret = str(ev.get("retention_until", "90 Days"))[:10]
            pdf.cell(45, 6, _s(eid), border=1)
            pdf.cell(50, 6, _s(et), border=1)
            pdf.cell(30, 6, _s(dur), border=1)
            pdf.cell(35, 6, _s(hold), border=1)
            pdf.cell(30, 6, _s(ret), border=1, ln=1)
            
    # Legal Sign-off block
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 5, "Official Verification & Chain of Custody Sign-Off", ln=1)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 4, "I hereby verify that this After-Action Report and all associated electronic telemetry, image evidence, and analytical alerts were generated in accordance with official Kerala Police standard operating procedures. The chain of custody has been cryptographically preserved.")
    pdf.ln(8)
    
    pdf.cell(90, 5, "________________________________________", ln=0)
    pdf.cell(0, 5, "________________________________________", ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(90, 4, _s(f"Primary Operator: {op_name}"), ln=0)
    pdf.cell(0, 4, _s(f"Supervising Officer: {supervisor_name}"), ln=1)
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(90, 4, "Drone Surveillance Wing, Kerala Police", ln=0)
    pdf.cell(0, 4, "District Command / Senior Supervisory Authority", ln=1)
    
    _render_police_footer(pdf, user)
    _add_watermark(pdf, f"CONFIDENTIAL - {name} ({role_label})")
    return bytes(pdf.output(dest="S"))

