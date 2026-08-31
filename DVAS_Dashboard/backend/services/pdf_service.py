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
        "This bundle contains: (1) the confirmed-event video clip, (2) event metadata JSON, "
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
