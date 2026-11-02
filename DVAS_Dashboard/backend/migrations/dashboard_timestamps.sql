-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Dashboard — dashboard_timestamps.sql   (evidence plan E6)
--
-- 1. Every dashboard time column becomes timestamptz (was ISO-8601 text), so
--    ordering and date ranges compare real instants. The ORM type UTCDateTime
--    (core/timeutil.py) keeps the API output as ISO-8601 strings.
--    Values that cannot be parsed are logged into migration_issues first and
--    become NULL (or 1970-01-01 for NOT NULL columns — also logged).
--    audit_logs.ts stays text: the audit hash chain was computed over it.
-- 2. alerts gets the columns R1 fills (category, severity, rule, evidence link,
--    GPS, source event, disposition).
--
-- Runs at backend start inside the same transaction as create_all, so it applies
-- completely or not at all. Converting a column is skipped once it is timestamptz,
-- so the file is safe to run on every start.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS migration_issues (
    id          BIGSERIAL PRIMARY KEY,
    migration   TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    column_name TEXT NOT NULL,
    row_key     TEXT,
    bad_value   TEXT,
    replaced_by TEXT,
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION dvas_try_timestamptz(v TEXT) RETURNS TIMESTAMPTZ
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF v IS NULL OR btrim(v) = '' THEN
        RETURN NULL;
    END IF;
    RETURN v::timestamptz;           -- session TimeZone is UTC below: naive values = UTC
EXCEPTION WHEN others THEN
    RETURN NULL;
END$$;

DO $$
DECLARE
    spec   TEXT[];
    specs  TEXT[][] := ARRAY[
        ['missions', 'started_at', 'mission_id'], ['missions', 'ended_at', 'mission_id'],
        ['alerts', 'ts', 'id'], ['alerts', 'acknowledged_at', 'id'],
        ['evidence', 'ts', 'id'], ['evidence', 'retention_until', 'id'], ['evidence', 'legal_hold_at', 'id'],
        ['evidence', 'capture_ts', 'id'], ['evidence', 'disposed_at', 'id'], ['evidence', 'integrity_checked_at', 'id'],
        ['evidence_sync_state', 'last_ts', 'id'], ['evidence_sync_state', 'updated_at', 'id'],
        ['cases', 'created_at', 'id'], ['case_evidence', 'added_at', 'case_id'],
        ['exports', 'requested_at', 'id'], ['exports', 'decision_at', 'id'],
        ['incidents', 'timestamp', 'id'], ['ai_model_suggestions', 'timestamp', 'id'],
        ['reports', 'requested_at', 'id'], ['reports', 'generated_at', 'id']
    ];
    col_type TEXT;
    not_null BOOLEAN;
    fallback TEXT;
BEGIN
    SET LOCAL TimeZone = 'UTC';
    FOREACH spec SLICE 1 IN ARRAY specs LOOP
        SELECT data_type, is_nullable = 'NO' INTO col_type, not_null
          FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = spec[1] AND column_name = spec[2];
        CONTINUE WHEN col_type IS NULL OR col_type = 'timestamp with time zone';

        fallback := CASE WHEN not_null THEN '1970-01-01T00:00:00Z' ELSE NULL END;
        EXECUTE format(
            'INSERT INTO migration_issues (migration, table_name, column_name, row_key, bad_value, replaced_by)
             SELECT %L, %L, %L, %I::text, %I, %L FROM %I
              WHERE btrim(COALESCE(%I, '''')) <> '''' AND dvas_try_timestamptz(%I) IS NULL',
            'dashboard_timestamps', spec[1], spec[2], spec[3], spec[2], fallback, spec[1], spec[2], spec[2]);
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN %I TYPE timestamptz USING %s',
            spec[1], spec[2],
            CASE WHEN not_null
                 THEN format('COALESCE(dvas_try_timestamptz(%I), %L::timestamptz)', spec[2], fallback)
                 ELSE format('dvas_try_timestamptz(%I)', spec[2]) END);
    END LOOP;
END$$;

-- alerts: what R1 records for every alert
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS category         TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS severity         TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS rule             TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS title            TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS evidence_id      TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS lat              DOUBLE PRECISION;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS lon              DOUBLE PRECISION;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS source_event_id  TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS disposition      TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS disposition_note TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS alerts_source_event_id ON alerts (source_event_id) WHERE source_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS alerts_evidence_id ON alerts (evidence_id);
CREATE INDEX IF NOT EXISTS alerts_category_ts ON alerts (category, ts);

-- R1: alert rows that are copies of incidents / model suggestions (B16)
UPDATE alerts SET category = 'incident'   WHERE category IS NULL AND id LIKE 'INC-%';
UPDATE alerts SET category = 'suggestion' WHERE category IS NULL AND id LIKE 'SUGG-%';
UPDATE alerts SET category = 'manual'     WHERE category IS NULL;
