-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Dashboard — exports_v2.sql   (evidence plan E4)
-- Cases group evidence; an export freezes the evidence list at approval and the
-- bundle is built once, stored in the evidence vault, and hashed.
-- exports.evidence_id stays for single-evidence exports and old rows.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS cases (
    id             TEXT PRIMARY KEY,
    case_number    TEXT NOT NULL,
    fir_number     TEXT,
    police_station TEXT,
    jurisdiction   TEXT,
    title          TEXT,
    created_by     TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open'          -- open | closed
);
CREATE UNIQUE INDEX IF NOT EXISTS cases_case_number ON cases (case_number);

CREATE TABLE IF NOT EXISTS case_evidence (
    case_id     TEXT NOT NULL REFERENCES cases(id),
    evidence_id TEXT NOT NULL REFERENCES evidence(id),
    added_by    TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (case_id, evidence_id)
);

ALTER TABLE exports ADD COLUMN IF NOT EXISTS case_id            TEXT REFERENCES cases(id);
ALTER TABLE exports ADD COLUMN IF NOT EXISTS case_number        TEXT;
ALTER TABLE exports ADD COLUMN IF NOT EXISTS evidence_ids       JSONB;        -- frozen at approval
ALTER TABLE exports ADD COLUMN IF NOT EXISTS bundle_key         TEXT;
ALTER TABLE exports ADD COLUMN IF NOT EXISTS bundle_sha256      TEXT;
ALTER TABLE exports ADD COLUMN IF NOT EXISTS bundle_bytes       BIGINT;
ALTER TABLE exports ADD COLUMN IF NOT EXISTS certificate_sha256 TEXT;
ALTER TABLE exports ADD COLUMN IF NOT EXISTS bundle_error       TEXT;
ALTER TABLE exports ADD COLUMN IF NOT EXISTS self_approved      BOOLEAN NOT NULL DEFAULT false;
