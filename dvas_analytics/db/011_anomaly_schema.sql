-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — Anomaly & Suspicious Activity Schema (011_anomaly_schema.sql)
-- Dedicated tables for cognitive anomaly detection, tracks, events, and summaries
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── Anomaly Tracks (per-track persistent state) ──────────────────────────
CREATE TABLE IF NOT EXISTS anomaly_tracks (
    id                  BIGSERIAL PRIMARY KEY,
    track_id            INT NOT NULL,
    drone_id            TEXT NOT NULL,
    mission_id          TEXT,
    class_name          TEXT NOT NULL,               -- pedestrian, car, backpack, etc.
    class_category      TEXT NOT NULL,               -- person, vehicle, object
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen           TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_s          REAL DEFAULT 0,
    trajectory          JSONB DEFAULT '[]'::jsonb,   -- screen coords [[x,y], ...]
    ground_trajectory   JSONB DEFAULT '[]'::jsonb,   -- ego-motion compensated ground coords
    attributes          JSONB DEFAULT '{}'::jsonb,   -- kinematics, aspect ratio, heading
    suspicion_level     TEXT DEFAULT 'NORMAL',       -- NORMAL, OBSERVE, LOW, MEDIUM, HIGH, CRITICAL
    suspicion_score     REAL DEFAULT 0.0,
    pipeline_stage      TEXT DEFAULT 'DETECTION',    -- DETECTION, OBSERVATION, CANDIDATE, CONFIRMATION, ALERT
    owner_track_id      INT,                         -- associated person track ID
    owner_distance_px   REAL,
    is_stationary       BOOLEAN DEFAULT FALSE,
    stationary_seconds  REAL DEFAULT 0,
    snapshot_key        TEXT DEFAULT '',             -- MinIO crop key in dvas-anomaly
    bbox_xyxy           JSONB DEFAULT '[]'::jsonb,   -- latest bbox [x1, y1, x2, y2]
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_anomaly_tracks_unique
    ON anomaly_tracks (drone_id, track_id, COALESCE(mission_id, ''));

CREATE INDEX IF NOT EXISTS idx_anomaly_tracks_drone_lastseen
    ON anomaly_tracks (drone_id, last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_anomaly_tracks_suspicion
    ON anomaly_tracks (suspicion_level) WHERE suspicion_level != 'NORMAL';

CREATE INDEX IF NOT EXISTS idx_anomaly_tracks_mission
    ON anomaly_tracks (mission_id);

CREATE INDEX IF NOT EXISTS idx_anomaly_tracks_category
    ON anomaly_tracks (class_category);


-- ─── Anomaly Events (confirmed alerts passing 8-stage gating) ─────────────
CREATE TABLE IF NOT EXISTS anomaly_events (
    event_id            UUID NOT NULL DEFAULT gen_random_uuid(),
    drone_id            TEXT NOT NULL,
    mission_id          TEXT,
    track_id            INT,
    event_type          TEXT NOT NULL,               -- abandoned_object, suspicious_loitering, person_fall, etc.
    severity            TEXT NOT NULL,               -- LOW, MEDIUM, HIGH, CRITICAL
    class_name          TEXT,
    confidence_detector REAL,
    confidence_rule     REAL,
    confidence_event    REAL,
    confidence_alert    REAL,
    evidence_groups     TEXT[] DEFAULT '{}',         -- e.g. {'A','C','D','G'}
    reasons             TEXT[] DEFAULT '{}',         -- explainability reasons
    summary             TEXT,                        -- one-line summary
    snapshot_key        TEXT DEFAULT '',             -- MinIO snapshot key in dvas-anomaly
    owner_track_id      INT,
    owner_status        TEXT,                        -- NEARBY, DEPARTED, UNKNOWN
    temporal            JSONB DEFAULT '{}'::jsonb,   -- stationary_seconds, unattended_seconds
    bbox                JSONB DEFAULT '[]'::jsonb,   -- [x1, y1, x2, y2]
    gps_lat             DOUBLE PRECISION,
    gps_lon             DOUBLE PRECISION,
    details             JSONB DEFAULT '{}'::jsonb,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS anomaly_events_2026_09 PARTITION OF anomaly_events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS anomaly_events_2026_10 PARTITION OF anomaly_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS anomaly_events_2026_11 PARTITION OF anomaly_events
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS anomaly_events_2026_12 PARTITION OF anomaly_events
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS anomaly_events_2027_01 PARTITION OF anomaly_events
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE IF NOT EXISTS anomaly_events_2027_02 PARTITION OF anomaly_events
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE IF NOT EXISTS anomaly_events_2027_03 PARTITION OF anomaly_events
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE IF NOT EXISTS anomaly_events_default PARTITION OF anomaly_events DEFAULT;

CREATE INDEX IF NOT EXISTS idx_anomaly_events_drone
    ON anomaly_events (drone_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_type
    ON anomaly_events (event_type);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_severity
    ON anomaly_events (severity);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_mission
    ON anomaly_events (mission_id);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_evidence_groups
    ON anomaly_events USING GIN (evidence_groups);



-- ─── Anomaly Mission Summaries (per-mission aggregate) ────────────────────
CREATE TABLE IF NOT EXISTS anomaly_mission_summaries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id          TEXT,
    drone_id            TEXT NOT NULL,
    total_alerts        INT NOT NULL DEFAULT 0,
    total_tracks        INT NOT NULL DEFAULT 0,
    peak_suspicion      TEXT DEFAULT 'NORMAL',
    event_type_counts   JSONB DEFAULT '{}'::jsonb,
    avg_alert_confidence REAL DEFAULT 0,
    duration_s          REAL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, drone_id)
);

-- Permissions
GRANT ALL PRIVILEGES ON anomaly_tracks, anomaly_events, anomaly_mission_summaries TO dvas;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dvas_app') THEN
        GRANT ALL PRIVILEGES ON anomaly_tracks, anomaly_events, anomaly_mission_summaries TO dvas_app;
    END IF;
END $$;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas;
