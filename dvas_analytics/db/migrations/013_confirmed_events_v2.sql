-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — 013_confirmed_events_v2.sql   (evidence plan E1, B28/B30)
--
-- 1. confirmed_events carries the text mission id, a text zone reference and
--    the GPS of the frame, so the dashboard copies them instead of guessing.
--    The UUID columns mission_id / zone_id stay (unused by new writers).
-- 2. Columns the analytics code already writes but no migration created
--    (schema drift found on the RunPod pod, which builds from migrations only):
--    face_embeddings.encrypted_embedding / person_object_key / arcface_embedding,
--    person_identities.best_person_image_key / mission_id.
-- Idempotent: every statement is IF NOT EXISTS / safe to re-run.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE confirmed_events ADD COLUMN IF NOT EXISTS mission_id_str TEXT;
ALTER TABLE confirmed_events ADD COLUMN IF NOT EXISTS zone_ref       TEXT;

CREATE INDEX IF NOT EXISTS idx_confirmed_events_mission_str
    ON confirmed_events (mission_id_str, ts) WHERE mission_id_str IS NOT NULL;
-- Cursor paging for the dashboard evidence sync: (ts, event_id) > (after_ts, after_id)
CREATE INDEX IF NOT EXISTS idx_confirmed_events_cursor
    ON confirmed_events (ts, event_id);

-- ── Schema drift (B30) ───────────────────────────────────────────────────────
ALTER TABLE face_embeddings ADD COLUMN IF NOT EXISTS encrypted_embedding BYTEA;
ALTER TABLE face_embeddings ADD COLUMN IF NOT EXISTS person_object_key   TEXT;
ALTER TABLE face_embeddings ADD COLUMN IF NOT EXISTS arcface_embedding   BYTEA;
-- write_face_embedding() stores the AES-GCM encrypted vector only
ALTER TABLE face_embeddings ALTER COLUMN embedding DROP NOT NULL;

ALTER TABLE person_identities ADD COLUMN IF NOT EXISTS best_person_image_key TEXT;
ALTER TABLE person_identities ADD COLUMN IF NOT EXISTS mission_id            TEXT;
CREATE INDEX IF NOT EXISTS idx_person_identities_mission
    ON person_identities (mission_id, cluster_id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dvas_app') THEN
        GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO dvas_app;
    END IF;
END$$;
