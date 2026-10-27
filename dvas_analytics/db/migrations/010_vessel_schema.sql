-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — Vessel Tracking Schema (010_vessel_schema.sql)
-- Dedicated tables for maritime vessel tracking & telemetry-free analytics
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── Vessel Tracks (per-track persistent state) ───────────────────────────
CREATE TABLE IF NOT EXISTS vessel_tracks (
    id              BIGSERIAL PRIMARY KEY,
    drone_id        TEXT NOT NULL REFERENCES drones(drone_id),
    mission_id      TEXT,
    tracker_id      INT NOT NULL,               -- SeaDrone tracker track_id
    class_id        SMALLINT NOT NULL,           -- 0-8 taxonomy ID
    class_name      TEXT NOT NULL,               -- e.g. "Fishing Boat / Trawler"
    size_bucket     TEXT,                        -- small / medium / large
    direction       TEXT,                        -- e.g. "left→right", "stationary", "toward camera"
    is_stationary   BOOLEAN DEFAULT FALSE,
    confidence      REAL NOT NULL,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_s      REAL DEFAULT 0,
    bbox_xyxy       JSONB,                       -- latest bbox [x1, y1, x2, y2]
    trajectory      JSONB,                       -- array of centroid coordinates [[x,y], ...]
    thumbnail_key   TEXT                         -- MinIO object key in dvas-vessels-maritime
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_vessel_tracks_unique
    ON vessel_tracks (drone_id, tracker_id, COALESCE(mission_id, ''));

CREATE INDEX IF NOT EXISTS idx_vessel_tracks_drone_lastseen
    ON vessel_tracks (drone_id, last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_vessel_tracks_class
    ON vessel_tracks (class_name);

CREATE INDEX IF NOT EXISTS idx_vessel_tracks_mission
    ON vessel_tracks (mission_id);

-- ─── Vessel Events (confirmed maritime events & alerts) ────────────────────
CREATE TABLE IF NOT EXISTS vessel_events (
    event_id        UUID NOT NULL DEFAULT gen_random_uuid(),
    drone_id        TEXT NOT NULL REFERENCES drones(drone_id),
    mission_id      TEXT,
    tracker_id      INT NOT NULL,
    event_type      TEXT NOT NULL,                -- 'vessel_detected', 'new_track', 'proximity_alert', 'loitering'
    class_name      TEXT NOT NULL,
    confidence      REAL NOT NULL,
    snapshot_key    TEXT,                         -- MinIO snapshot key
    details         JSONB,                        -- event-specific metadata (size, direction, speed_profile)
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS vessel_events_2026_09 PARTITION OF vessel_events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS vessel_events_2026_10 PARTITION OF vessel_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS vessel_events_default PARTITION OF vessel_events DEFAULT;

CREATE INDEX IF NOT EXISTS idx_vessel_events_drone
    ON vessel_events (drone_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_vessel_events_type
    ON vessel_events (event_type);
CREATE INDEX IF NOT EXISTS idx_vessel_events_mission
    ON vessel_events (mission_id);

-- ─── Vessel Mission Summaries (per-mission analytics aggregate) ───────────
CREATE TABLE IF NOT EXISTS vessel_mission_summaries (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id                TEXT,
    drone_id                  TEXT NOT NULL REFERENCES drones(drone_id),
    total_detections_by_class JSONB,
    total_unique_vessels      INT NOT NULL DEFAULT 0,
    peak_vessels_in_frame     INT NOT NULL DEFAULT 0,
    proximity_events_count    INT NOT NULL DEFAULT 0,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, drone_id)
);

-- Permissions
GRANT ALL PRIVILEGES ON vessel_tracks, vessel_events, vessel_mission_summaries TO dvas;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas;
