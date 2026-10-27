-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — Intrusion Detection Schema (007_intrusion_schema.sql)
-- Dedicated tables for geofence intrusion state tracking
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── Intrusion Events (state transitions with rich metadata) ──────────────
CREATE TABLE IF NOT EXISTS intrusion_events (
    event_id         UUID NOT NULL DEFAULT gen_random_uuid(),
    mission_id       TEXT,
    drone_id         TEXT NOT NULL REFERENCES drones(drone_id),
    zone_id          TEXT,
    track_id         INT NOT NULL,
    track_class      TEXT NOT NULL,
    state_from       TEXT NOT NULL,
    state_to         TEXT NOT NULL,
    confidence       REAL NOT NULL,
    latitude         DOUBLE PRECISION,
    longitude        DOUBLE PRECISION,
    entry_ts         TIMESTAMPTZ,
    exit_ts          TIMESTAMPTZ,
    dwell_duration_s REAL,
    trajectory       JSONB,
    snapshot_key     TEXT,  -- MinIO object key for event snapshot JPEG
    frame_sequence   BIGINT,
    ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS intrusion_events_2026_09 PARTITION OF intrusion_events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS intrusion_events_2026_10 PARTITION OF intrusion_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

CREATE INDEX IF NOT EXISTS idx_intrusion_events_drone
    ON intrusion_events (drone_id, ts);
CREATE INDEX IF NOT EXISTS idx_intrusion_events_mission
    ON intrusion_events (mission_id);
CREATE INDEX IF NOT EXISTS idx_intrusion_events_state
    ON intrusion_events (state_to);
CREATE INDEX IF NOT EXISTS idx_intrusion_events_track
    ON intrusion_events (track_id, drone_id);

-- ─── Intrusion Mission Summaries (per-mission aggregate) ──────────────────
CREATE TABLE IF NOT EXISTS intrusion_mission_summaries (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id                TEXT,
    drone_id                  TEXT NOT NULL REFERENCES drones(drone_id),
    zone_id                   TEXT,
    total_detections_by_class JSONB,
    total_intrusion_events    INT NOT NULL DEFAULT 0,
    avg_dwell_time_s          REAL,
    max_concurrent_intrusions INT DEFAULT 0,
    busiest_entry_point       JSONB,
    analytics_json_key        TEXT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, drone_id)
);

-- Grant access to dvas_app role
GRANT ALL PRIVILEGES ON intrusion_events, intrusion_mission_summaries TO dvas_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas_app;
