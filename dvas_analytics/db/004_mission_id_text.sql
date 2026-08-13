-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — 004_mission_id_text.sql
-- Add mission_id as TEXT to key tables so string-format mission IDs
-- (e.g. MSN-20260813-1530-01-ALPHA) can be stored and queried directly
-- without requiring a FK to the missions table UUID primary key.
-- ═══════════════════════════════════════════════════════════════════════════

-- Add mission_id TEXT column to tracks
ALTER TABLE tracks ADD COLUMN IF NOT EXISTS mission_id_str TEXT;

-- Add mission_id TEXT column to plate_reads
ALTER TABLE plate_reads ADD COLUMN IF NOT EXISTS mission_id_str TEXT;

-- Add mission_id TEXT column to vehicle_plate_links
ALTER TABLE vehicle_plate_links ADD COLUMN IF NOT EXISTS mission_id_str TEXT;

-- Add mission_id TEXT column to vehicle_attributes (for easy join-free queries)
ALTER TABLE vehicle_attributes ADD COLUMN IF NOT EXISTS mission_id_str TEXT;

-- Indexes for fast mission-scoped queries
CREATE INDEX IF NOT EXISTS idx_tracks_mission_str
    ON tracks (mission_id_str, drone_id, first_seen DESC)
    WHERE mission_id_str IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_plate_reads_mission_str
    ON plate_reads (mission_id_str, created_at DESC)
    WHERE mission_id_str IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vpl_mission_str
    ON vehicle_plate_links (mission_id_str, linked_at DESC)
    WHERE mission_id_str IS NOT NULL;

-- Composite index for the /plates/by-mission endpoint
CREATE INDEX IF NOT EXISTS idx_plate_reads_mission_plate
    ON plate_reads (mission_id_str, plate_text)
    WHERE mission_id_str IS NOT NULL;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO dvas_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas_app;
