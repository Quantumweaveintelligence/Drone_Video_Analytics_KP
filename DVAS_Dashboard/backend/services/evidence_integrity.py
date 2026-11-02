"""
Evidence integrity (evidence plan E3): canonical record hash and byte hashes.

The record hash covers what the evidence *is* (source event, capture time, place,
analyzer facts and the SHA-256 of every stored object). It is stored on the row
and written into the creation audit entry, so the audit hash chain covers it.
"""
import hashlib
import json
from typing import Any, Dict

# Fields that define an evidence record. Workflow fields (legal hold, status,
# retention) are excluded: they change by design and are audited separately.
RECORD_FIELDS = (
    "id", "source_event_id", "event_type", "drone_id", "mission_id", "ts", "capture_ts",
    "confidence", "lat", "lon", "severity", "attributes", "objects", "model_info", "image_match",
)


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, UTF-8 kept, non-JSON types as str."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_dict(evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {k: evidence.get(k) for k in RECORD_FIELDS}


def record_sha256(evidence: Dict[str, Any]) -> str:
    return sha256_hex(canonical_json(record_dict(evidence)).encode("utf-8"))
