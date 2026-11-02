-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Dashboard — reports_v2.sql   (evidence plan R4)
-- Reports become jobs: requested (CRO) → queued → running → ready | failed,
-- or rejected. Applied at backend startup, safe to re-run.
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE reports ADD COLUMN IF NOT EXISTS attempts    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS started_at  TIMESTAMPTZ;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS decided_by  TEXT;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS decided_at  TIMESTAMPTZ;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS file_bucket TEXT;
CREATE INDEX IF NOT EXISTS reports_status_requested ON reports (status, requested_at);

-- Rows left 'generating' by the old fire-and-forget task can never finish
UPDATE reports SET status = 'failed', error_message = 'interrupted (generated before report jobs)'
 WHERE status = 'generating';
