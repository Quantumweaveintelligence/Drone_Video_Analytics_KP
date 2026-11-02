-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Dashboard — evidence_v2.sql   (evidence plan E1 + E2)
-- Applied at backend startup after create_all (services/seed_service.py) and
-- safe to re-run. Time columns stay ISO-8601 text like the rest of the
-- dashboard schema; E6 converts all of them to timestamptz together.
-- ═══════════════════════════════════════════════════════════════════════════

-- E1: what the analyzer recorded, copied verbatim from the confirmed event
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS attributes   JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS objects      JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS image_match  TEXT  NOT NULL DEFAULT 'none';  -- exact | heuristic | none
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS capture_ts   TEXT;                            -- frame capture time
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS lat          DOUBLE PRECISION;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS lon          DOUBLE PRECISION;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS severity     TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS model_info   JSONB;

-- E2: vault copy + disposal instead of hard delete
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS vault_status    TEXT;   -- copied | partial | failed | none; NULL = legacy row
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS status          TEXT NOT NULL DEFAULT 'active';  -- active | disposed
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS disposed_at     TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS disposed_by     TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS disposal_reason TEXT;

CREATE INDEX IF NOT EXISTS evidence_mission_capture ON evidence (mission_id, capture_ts);
CREATE INDEX IF NOT EXISTS evidence_status_retention ON evidence (status, retention_until);
CREATE INDEX IF NOT EXISTS evidence_attr_gin ON evidence USING gin (attributes jsonb_path_ops);

-- Q9 default: rows created before E1 whose image was looked up afterwards are
-- marked 'heuristic' (shown as such in the UI), never silently trusted.
UPDATE evidence SET image_match = 'heuristic'
 WHERE image_match = 'none' AND vault_status IS NULL
   AND objects = '[]'::jsonb AND COALESCE(primary_image_key, '') <> '';

-- Persistent sync cursor (one row, id = 1)
CREATE TABLE IF NOT EXISTS evidence_sync_state (
    id            INTEGER PRIMARY KEY,
    last_ts       TEXT,
    last_event_id TEXT,
    updated_at    TEXT
);
