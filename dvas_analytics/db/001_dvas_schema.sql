-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics Database Schema — 001_dvas_schema.sql
-- Postgres 16 — single instance, 'dvas' database
-- Based on DVAS_Postgres_Schema.md
-- ═══════════════════════════════════════════════════════════════════════════

-- Extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- pgvector for face embeddings (install separately if available)
-- CREATE EXTENSION IF NOT EXISTS vector;

-- ─── Users (Keycloak sync cache) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id       UUID PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    full_name     TEXT,
    badge_number  TEXT,
    role          TEXT NOT NULL CHECK (role IN (
                      'drone_operator','control_room_officer','supervisor',
                      'senior_officer','system_admin')),
    jurisdiction  TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT true,
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Drones ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS drones (
    drone_id              TEXT PRIMARY KEY,
    label                 TEXT,
    model                 TEXT,
    registration_number   TEXT,
    status                TEXT NOT NULL DEFAULT 'inactive'
                              CHECK (status IN ('active','inactive','maintenance')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed known drones (will also be done by application on startup)
INSERT INTO drones (drone_id, label, status) VALUES
    ('DRONE_KL_001', 'KL Drone 001', 'inactive'),
    ('DRONE_KL_002', 'KL Drone 002', 'inactive'),
    ('DRONE_KL_003', 'KL Drone 003', 'inactive'),
    ('DRONE_KL_004', 'KL Drone 004', 'inactive'),
    ('DRONE_KL_005', 'KL Drone 005', 'inactive')
ON CONFLICT (drone_id) DO NOTHING;

-- ─── Missions ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS missions (
    mission_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  TEXT NOT NULL,
    mission_type          TEXT,
    location_description  TEXT,
    jurisdiction          TEXT,
    status                TEXT NOT NULL DEFAULT 'planned'
                              CHECK (status IN ('planned','active','completed','cancelled')),
    started_at            TIMESTAMPTZ,
    ended_at              TIMESTAMPTZ,
    created_by            UUID REFERENCES users(user_id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Mission–Drone Assignments ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mission_drone_assignments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id    UUID NOT NULL REFERENCES missions(mission_id),
    drone_id      TEXT NOT NULL REFERENCES drones(drone_id),
    operator_id   UUID REFERENCES users(user_id),
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, drone_id)
);

-- ─── Zones ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS zones (
    zone_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id        UUID NOT NULL REFERENCES missions(mission_id),
    name              TEXT NOT NULL,
    zone_type         TEXT,
    boundary_geojson  JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Analytics Presets ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analytics_presets (
    preset_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT UNIQUE NOT NULL,
    description  TEXT,
    modules      TEXT[] NOT NULL,
    created_by   UUID REFERENCES users(user_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO analytics_presets (name, description, modules) VALUES
    ('Public Event',    'Crowd + person + face tracking', ARRAY['crowd','person','face']),
    ('Highway Patrol',  'Vehicle + plate recognition',    ARRAY['vehicle','anpr']),
    ('Full Surveillance','All modules active',             ARRAY['crowd','person','face','vehicle','anpr']),
    ('Coastal Watch',   'Vessel + person tracking',       ARRAY['person','vehicle'])
ON CONFLICT (name) DO NOTHING;

-- ─── Mission Analytics Config ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mission_analytics_config (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id      UUID NOT NULL REFERENCES missions(mission_id),
    drone_id        TEXT NOT NULL REFERENCES drones(drone_id),
    preset_id       UUID REFERENCES analytics_presets(preset_id),
    active_modules  TEXT[] NOT NULL,
    set_by          UUID REFERENCES users(user_id),
    set_by_role     TEXT NOT NULL DEFAULT 'system_admin',
    set_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mac_drone_latest ON mission_analytics_config (drone_id, set_at DESC);

-- ─── Tracks ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tracks (
    track_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drone_id           TEXT NOT NULL REFERENCES drones(drone_id),
    mission_id         UUID REFERENCES missions(mission_id),
    track_type         TEXT NOT NULL CHECK (track_type IN ('person','vehicle','vessel')),
    first_seen         TIMESTAMPTZ NOT NULL,
    last_seen          TIMESTAMPTZ NOT NULL,
    reidentified_from  UUID REFERENCES tracks(track_id),
    metadata           JSONB
);
CREATE INDEX IF NOT EXISTS idx_tracks_drone_time ON tracks (drone_id, first_seen);

-- ─── Detections (raw, short-retention) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS detections (
    detection_id     BIGSERIAL,
    track_id         UUID REFERENCES tracks(track_id),
    drone_id         TEXT NOT NULL,
    ts               TIMESTAMPTZ NOT NULL,
    frame_sequence   BIGINT,
    bbox_x1 INT, bbox_y1 INT, bbox_x2 INT, bbox_y2 INT,
    confidence       REAL NOT NULL,
    module           TEXT NOT NULL,
    PRIMARY KEY (detection_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS detections_2026_07 PARTITION OF detections
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS detections_2026_08 PARTITION OF detections
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- ─── Confirmed Events (authoritative) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS confirmed_events (
    event_id      UUID NOT NULL DEFAULT gen_random_uuid(),
    mission_id    UUID REFERENCES missions(mission_id),
    drone_id      TEXT NOT NULL REFERENCES drones(drone_id),
    zone_id       UUID REFERENCES zones(zone_id),
    track_id      UUID REFERENCES tracks(track_id),
    event_type    TEXT NOT NULL,
    confidence    REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    ts            TIMESTAMPTZ NOT NULL,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    attributes    JSONB,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reviewed','dismissed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS confirmed_events_2026_07 PARTITION OF confirmed_events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS confirmed_events_2026_08 PARTITION OF confirmed_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE INDEX IF NOT EXISTS idx_confirmed_events_drone_ts ON confirmed_events (drone_id, ts);
CREATE INDEX IF NOT EXISTS idx_confirmed_events_mission  ON confirmed_events (mission_id);
CREATE INDEX IF NOT EXISTS idx_confirmed_events_type     ON confirmed_events (event_type);

-- ─── Vehicle Attributes ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vehicle_attributes (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id         UUID REFERENCES tracks(track_id),
    event_id         UUID,    -- references confirmed_events(event_id) but event_id is part of composite PK
    vehicle_type     TEXT,
    colour_top3      JSONB,
    make_model_top3  JSONB,
    speed_kph        REAL,
    speed_reliability TEXT,
    direction_label  TEXT,
    traffic_state    TEXT
);

-- ─── Plate Reads ─────────────────────────────────────────────────────────────
-- One row per unique track_id (deduplication via ON CONFLICT DO UPDATE)
CREATE TABLE IF NOT EXISTS plate_reads (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id            UUID UNIQUE REFERENCES tracks(track_id),
    event_id            UUID,
    plate_text          TEXT,
    ocr_confidence      REAL NOT NULL,
    is_reliable         BOOLEAN NOT NULL DEFAULT false,
    vehicle_image_b64   TEXT,       -- base64 JPEG crop of the vehicle
    times_read          INT NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_plate_reads_text ON plate_reads (plate_text);

-- ─── Crowd Density Readings ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crowd_density_readings (
    id                BIGSERIAL,
    drone_id          TEXT NOT NULL,
    zone_id           UUID REFERENCES zones(zone_id),
    ts                TIMESTAMPTZ NOT NULL,
    estimated_count   REAL,
    density_per_sqm   REAL,
    heatmap_object_key TEXT,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS crowd_density_readings_2026_07 PARTITION OF crowd_density_readings
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS crowd_density_readings_2026_08 PARTITION OF crowd_density_readings
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- ─── Evidence ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evidence_clips (
    evidence_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id           UUID NOT NULL,
    drone_id           TEXT NOT NULL REFERENCES drones(drone_id),
    storage_backend    TEXT NOT NULL DEFAULT 'seaweedfs',
    object_key         TEXT NOT NULL,
    checksum_sha256    TEXT NOT NULL,
    file_size_bytes    BIGINT,
    duration_seconds   REAL,
    pre_event_seconds  REAL,
    post_event_seconds REAL,
    codec              TEXT,
    resolution         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    retention_until    TIMESTAMPTZ,
    legal_hold         BOOLEAN NOT NULL DEFAULT false,
    legal_hold_reason  TEXT,
    is_deleted         BOOLEAN NOT NULL DEFAULT false,
    deleted_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_evidence_retention ON evidence_clips (retention_until) WHERE NOT legal_hold;

CREATE TABLE IF NOT EXISTS evidence_frames (
    frame_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID,
    evidence_id     UUID REFERENCES evidence_clips(evidence_id),
    object_key      TEXT NOT NULL,
    frame_type      TEXT NOT NULL CHECK (frame_type IN
                        ('annotated_frame','face_crop','plate_crop','person_crop','vehicle_crop')),
    checksum_sha256 TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Audit Log ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id       BIGSERIAL,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id        UUID REFERENCES users(user_id),
    user_role      TEXT,
    action         TEXT NOT NULL,
    resource_type  TEXT,
    resource_id    TEXT,
    source_system  TEXT,
    source_ip      INET,
    outcome        TEXT NOT NULL CHECK (outcome IN ('allow','deny')),
    details        JSONB,
    PRIMARY KEY (audit_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS audit_log_2026_07 PARTITION OF audit_log
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS audit_log_2026_08 PARTITION OF audit_log
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- ─── Retention Policies ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS retention_policies (
    policy_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_category   TEXT NOT NULL UNIQUE,
    retention_days  INT NOT NULL,
    configurable    BOOLEAN NOT NULL DEFAULT true,
    updated_by      UUID REFERENCES users(user_id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO retention_policies (data_category, retention_days) VALUES
    ('evidence_clip',        90),
    ('structured_analytics', 730),
    ('crowd_density',        270),
    ('audit_log',            1825)
ON CONFLICT (data_category) DO NOTHING;

-- Grant application user full access
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dvas_app') THEN
        CREATE ROLE dvas_app LOGIN PASSWORD 'dvas_secret';
    END IF;
END$$;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO dvas_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO dvas_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO dvas_app;

-- Append-only audit log
REVOKE UPDATE, DELETE ON audit_log FROM dvas_app;
