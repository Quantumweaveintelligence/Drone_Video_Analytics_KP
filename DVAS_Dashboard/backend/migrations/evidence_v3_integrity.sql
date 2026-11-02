-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Dashboard — evidence_v3_integrity.sql   (evidence plan E3)
-- record_sha256: SHA-256 of the canonical evidence record (services/evidence_integrity.py),
-- also written into the creation audit entry, so the audit hash chain covers it.
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS record_sha256        TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS integrity            TEXT NOT NULL DEFAULT 'unverified';  -- ok | mismatch | missing | unverified
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS integrity_checked_at TEXT;
ALTER TABLE reports  ADD COLUMN IF NOT EXISTS sha256               TEXT;
