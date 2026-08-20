-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — 004_mission_dedup.sql
-- Add mission_id to face_embeddings + dedup tracking support
-- ═══════════════════════════════════════════════════════════════════════════

-- Add mission_id column to face_embeddings for mission-scoped deduplication
ALTER TABLE face_embeddings ADD COLUMN IF NOT EXISTS mission_id TEXT;
CREATE INDEX IF NOT EXISTS idx_face_embeddings_mission ON face_embeddings (mission_id) WHERE mission_id IS NOT NULL;

-- Ensure tracks table has mission_id_str column and index
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS mission_id_str TEXT;
CREATE INDEX IF NOT EXISTS idx_tracks_mission_str
    ON tracks (mission_id_str, drone_id, first_seen DESC)
    WHERE mission_id_str IS NOT NULL;

-- Dedup runs table for tracking mission deduplication status & history
CREATE TABLE IF NOT EXISTS dedup_runs (
    id SERIAL PRIMARY KEY,
    mission_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',  -- pending, running, complete, error
    total_embeddings INTEGER DEFAULT 0,
    unique_found INTEGER DEFAULT 0,
    duplicates_removed INTEGER DEFAULT 0,
    progress_pct REAL DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ,
    error_message TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_runs_mission ON dedup_runs(mission_id);

GRANT ALL PRIVILEGES ON TABLE dedup_runs TO dvas_app;
GRANT ALL PRIVILEGES ON SEQUENCE dedup_runs_id_seq TO dvas_app;
