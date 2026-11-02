"""
Export bundle, built once at approval (evidence plan E4).

The bundle is a ZIP with the evidence objects (read from the vault, hashes
re-verified), manifest.json (files + SHA-256, evidence records, audit excerpt),
the s.63 certificate, README.txt and SHA256SUMS.txt. It is stored in the vault
under legal hold; every download streams those same bytes.
"""
import io
import json
import os
import socket
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from services import evidence_vault
from services.certificate import CERTIFICATE_TEMPLATE_VERSION, build_certificate
from services.evidence_integrity import sha256_hex

# Fixed timestamp inside the ZIP so the archive depends only on its content
_ZIP_DATE = (2024, 7, 1, 0, 0, 0)


class BundleIntegrityError(RuntimeError):
    """A stored object no longer matches its recorded hash; no bundle is built."""


def _objects_for(ev: Dict) -> List[Dict]:
    """Vault objects, or for pre-E1 rows the old primary/secondary keys (hashed at export)."""
    objs = [o for o in (ev.get("objects") or []) if o.get("bucket") and o.get("key")]
    if objs:
        return objs
    legacy = []
    if ev.get("primary_image_key") and not str(ev["primary_image_key"]).startswith("http"):
        legacy.append({"role": "primary", "bucket": ev.get("image_bucket") or "dvas-faces",
                       "key": ev["primary_image_key"], "sha256": None})
    if ev.get("secondary_image_key"):
        bucket = "dvas-plates" if str(ev["secondary_image_key"]).startswith("plates/") else "dvas-faces"
        legacy.append({"role": "secondary", "bucket": bucket, "key": ev["secondary_image_key"], "sha256": None})
    return legacy


def _zip_write(z: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    z.writestr(info, data)


def _system_info(evidence: List[Dict]) -> Dict:
    models = {}
    for ev in evidence:
        mi = ev.get("model_info") or {}
        if mi.get("service"):
            models[mi["service"]] = mi.get("weights_sha256")
    return {"name": "DVAS - Drone Video Analytics System, Kerala Police",
            "git_sha": os.getenv("DVAS_GIT_SHA") or None,
            "host": socket.gethostname(), "models": models}


def build_bundle(export: Dict, case: Dict, evidence: List[Dict], audit_rows: List[Dict],
                 approver: Dict) -> Tuple[bytes, Dict]:
    """Build the bundle bytes. Returns (zip_bytes, info) with info = {sha256, certificate_sha256, files}.

    Raises BundleIntegrityError when a stored object's bytes differ from its recorded hash.
    """
    files: List[Dict] = []
    blobs: List[Tuple[str, bytes]] = []
    for ev in evidence:
        for obj in _objects_for(ev):
            try:
                data = evidence_vault.read_object(obj["bucket"], obj["key"])
            except Exception as exc:
                raise BundleIntegrityError(f"{ev['id']} {obj.get('role')}: object unreadable ({exc})")
            digest = sha256_hex(data)
            if obj.get("sha256") and obj["sha256"] != digest:
                raise BundleIntegrityError(f"{ev['id']} {obj.get('role')}: SHA-256 mismatch")
            ext = os.path.splitext(obj["key"])[1] or ".bin"
            path = f"evidence/{ev['id']}/{obj.get('role') or 'object'}{ext}"
            files.append({"path": path, "sha256": digest, "bytes": len(data), "evidence_id": ev["id"],
                          "role": obj.get("role"), "kind": obj.get("kind"),
                          "hash_source": "capture" if obj.get("sha256") else "export"})
            blobs.append((path, data))

    records = []
    for ev in evidence:
        rec = {k: ev.get(k) for k in (
            "id", "label", "event_type", "drone_id", "mission_id", "jurisdiction", "ts", "capture_ts",
            "lat", "lon", "confidence", "severity", "attributes", "model_info", "image_match",
            "record_sha256", "integrity", "source_event_id", "legal_hold", "retention_until")}
        rec["audit"] = [a for a in audit_rows if a.get("target_id") == ev["id"]]
        records.append(rec)

    certificate = build_certificate(export, case, evidence, files, _system_info(evidence), approver)
    cert_sha = sha256_hex(certificate)
    manifest = {
        "format": "dvas-evidence-bundle/1",
        "certificate_template": CERTIFICATE_TEMPLATE_VERSION,
        "export": {k: export.get(k) for k in (
            "id", "case_id", "case_number", "justification", "requested_by", "requested_at",
            "decision_by", "decision_at", "self_approved")},
        "case": case or None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hash_algorithm": "SHA-256",
        "files": files + [{"path": "certificate_BSA63.pdf", "sha256": cert_sha, "bytes": len(certificate)}],
        "evidence": records,
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    readme = (
        f"DVAS evidence bundle\nExport {export.get('id')}\n"
        f"Case {(case or {}).get('case_number') or export.get('case_number') or '-'}\n"
        f"Approved by {export.get('decision_by') or '-'} on {export.get('decision_at') or '-'}"
        f"{' (direct export, self-approved)' if export.get('self_approved') else ''}\n"
        f"Evidence items: {len(evidence)}, files: {len(files)}\n\n"
        "Verify: sha256sum -c SHA256SUMS.txt\n"
        "manifest.json lists every file, its SHA-256 and the evidence records with their audit trail.\n"
    ).encode("utf-8")

    contents = sorted(blobs) + [("certificate_BSA63.pdf", certificate), ("manifest.json", manifest_bytes),
                                ("README.txt", readme)]
    sums = "".join(f"{sha256_hex(data)}  {name}\n" for name, data in contents).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in contents + [("SHA256SUMS.txt", sums)]:
            _zip_write(z, name, data)
    data = buf.getvalue()
    return data, {"sha256": sha256_hex(data), "certificate_sha256": cert_sha, "files": files,
                  "manifest_sha256": sha256_hex(manifest_bytes)}
