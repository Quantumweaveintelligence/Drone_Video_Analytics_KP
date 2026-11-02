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
def _s(x):
    return str(x if x is not None else "").encode("latin-1", "replace").decode("latin-1")

