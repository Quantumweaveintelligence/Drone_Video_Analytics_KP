"""
Certificate for an evidence export (evidence plan E4) — replaces the s.65B PDF.

Bharatiya Sakshya Adhiniyam 2023, s.63(4)(c) and its Schedule:
  Part A — by the person in charge of the system that produced the records:
           device/system identification, the records with their SHA-256 hash
           values, and a statement that the system operated properly.
  Part B — by an expert, left as a blank form.

The wording is a template (CERTIFICATE_TEMPLATE_VERSION) and must be approved
by the department's legal cell before court use (open question Q1). The PDF is
built once, when the export is approved, and stored inside the bundle.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fpdf import FPDF

from services.pdf_service import _s

CERTIFICATE_TEMPLATE_VERSION = "DVAS-BSA63-v1 (wording pending legal-cell approval)"
IST = timezone(timedelta(hours=5, minutes=30))


def _fmt_times(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return f"{dt.astimezone(IST):%d-%m-%Y %H:%M:%S} IST / {dt.astimezone(timezone.utc):%H:%M:%S} UTC"
    except (TypeError, ValueError):
        return str(iso or "-")


def build_certificate(export: Dict, case: Dict, evidence: List[Dict], files: List[Dict],
                      system: Dict, approver: Dict) -> bytes:
    """
    export:   export row (id, justification, requested_by, decision_by/at, self_approved)
    case:     case row or {} (case_number, fir_number, police_station)
    evidence: evidence rows in the bundle (with record_sha256)
    files:    manifest file entries [{path, sha256, bytes, evidence_id, role, hash_source}]
    system:   {"name", "git_sha", "host", "models": {service: weights_sha256}}
    approver: {"name", "email", "role"}
    """
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()

    def h(text, size=11):
        pdf.set_font("Helvetica", "B", size)
        pdf.multi_cell(0, 6, _s(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    def p(text, size=9):
        pdf.set_font("Helvetica", "", size)
        pdf.multi_cell(0, 5, _s(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    def kv(k, v):
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(50, 5, _s(k))
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(0, 5, _s(v), new_x="LMARGIN", new_y="NEXT")

    h("CERTIFICATE UNDER SECTION 63(4)(c) OF THE BHARATIYA SAKSHYA ADHINIYAM, 2023", 12)
    h("PART A  (to be filled by the party / person in charge of the system)", 10)

    kv("Export reference", export.get("id", "-"))
    kv("Case number", case.get("case_number") or export.get("case_number") or "-")
    kv("FIR number", case.get("fir_number") or "-")
    kv("Police station", case.get("police_station") or "-")
    kv("Purpose", export.get("justification") or "-")
    kv("Requested by", export.get("requested_by") or "-")
    approval = f"{export.get('decision_by') or '-'} on {_fmt_times(export.get('decision_at'))}"
    if export.get("self_approved"):
        approval += "  [DIRECT EXPORT - requested and approved by the same officer]"
    kv("Approved by", approval)
    pdf.ln(2)

    h("1. Identification of the system that produced the electronic records", 10)
    kv("System", system.get("name") or "DVAS - Drone Video Analytics System, Kerala Police")
    kv("Software revision", system.get("git_sha") or "not recorded")
    kv("Host", system.get("host") or "-")
    for service, digest in sorted((system.get("models") or {}).items()):
        kv(f"Model ({service})", digest or "not recorded")
    pdf.ln(2)

    h("2. Electronic records and their hash values (SHA-256)", 10)
    for ev in evidence:
        pdf.set_font("Helvetica", "B", 8)
        pdf.multi_cell(0, 5, _s(f"{ev.get('id')}  -  {ev.get('event_type')}"), new_x="LMARGIN", new_y="NEXT")
        kv("  Captured", _fmt_times(ev.get("capture_ts") or ev.get("ts")))
        kv("  Drone / mission", f"{ev.get('drone_id') or '-'} / {ev.get('mission_id') or '-'}")
        if ev.get("lat") is not None and ev.get("lon") is not None:
            kv("  Location (WGS84)", f"{float(ev['lat']):.6f}, {float(ev['lon']):.6f}")
        kv("  Record hash", ev.get("record_sha256") or "-")
        for f in [f for f in files if f.get("evidence_id") == ev.get("id")]:
            note = "hashed at capture" if f.get("hash_source") == "capture" else "hashed at export"
            kv(f"  {f['path']}", f"{f['sha256']}  ({f.get('bytes') or 0} bytes, {note})")
        pdf.ln(1)

    h("3. Statement", 10)
    p("I certify that the electronic records listed above were produced by the system identified in "
      "section 1 during its regular use for drone surveillance by Kerala Police; that during that period "
      "the system was operating properly, or that any period in which it was not operating properly did "
      "not affect the records or their accuracy; that the records were stored in write-once (object-lock) "
      "storage from the time they were recorded; and that the hash values above were computed with "
      "SHA-256 and match the files supplied in this export.")
    p("The hash values in section 2 can be recomputed from the files in this bundle (see SHA256SUMS.txt "
      "and manifest.json). Any difference indicates that a file has been altered.")
    pdf.ln(4)
    for label in ("Name", "Designation", "Signature", "Date", "Place"):
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(35, 8, _s(f"{label}:"))
        pdf.cell(0, 8, "_" * 60, ln=1)

    pdf.add_page()
    h("PART B  (to be filled by the expert)", 10)
    p("I, ____________________ (name), ____________________ (designation), state that the hash values "
      "of the electronic records listed in Part A were obtained through the algorithm SHA-256 and that "
      "I have verified them against the records supplied.")
    for label in ("Name", "Designation", "Signature", "Date", "Place"):
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(35, 8, _s(f"{label}:"))
        pdf.cell(0, 8, "_" * 60, ln=1)

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 7)
    pdf.multi_cell(0, 4, _s(f"Template {CERTIFICATE_TEMPLATE_VERSION}. Generated "
                            f"{_fmt_times(datetime.now(timezone.utc).isoformat())} by "
                            f"{approver.get('email') or '-'}."), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
