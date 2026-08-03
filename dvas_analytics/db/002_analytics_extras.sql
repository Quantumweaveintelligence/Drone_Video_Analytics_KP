-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics Extras — 002_analytics_extras.sql
-- Additional tables for: face embeddings, plate watchlist
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── Face Embeddings (ResNet50 2048-dim, stored as float array) ──────────────
-- Best approach: FLOAT4[] for broad compatibility without pgvector requirement.
-- If pgvector is installed, you can ALTER this later to vector(2048) for ANN search.
CREATE TABLE IF NOT EXISTS face_embeddings (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id     UUID REFERENCES tracks(track_id) ON DELETE CASCADE,
    image_path   TEXT,                    -- local path or object storage key
    embedding    FLOAT4[] NOT NULL,       -- 2048-dim ResNet50 embedding
    drone_id     TEXT REFERENCES drones(drone_id),
    cluster_id   INT,                     -- set after deduplication clustering
    is_duplicate BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_face_embeddings_track ON face_embeddings (track_id);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_cluster ON face_embeddings (cluster_id) WHERE cluster_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_face_embeddings_dedup ON face_embeddings (is_duplicate);

-- ─── Plate Watchlist (theft/stolen vehicle registry) ─────────────────────────
-- Managed via ANPR service API — dashboard can add/remove plates
CREATE TABLE IF NOT EXISTS plate_watchlist (
    plate_text   TEXT PRIMARY KEY,
    reason       TEXT,                    -- 'theft' | 'wanted' | 'suspicious' | custom
    case_ref     TEXT,                    -- FIR / case number
    added_by     UUID REFERENCES users(user_id),
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,            -- NULL = never expires
    is_active    BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_watchlist_active ON plate_watchlist (plate_text) WHERE is_active = true;

-- Seed with a sample entry (remove in production)
-- INSERT INTO plate_watchlist (plate_text, reason, case_ref) VALUES
--     ('KL07AB1234', 'theft', 'FIR/2026/001') ON CONFLICT DO NOTHING;

-- ─── Theft Alerts (logged when a watchlisted plate is confirmed) ──────────────
CREATE TABLE IF NOT EXISTS theft_alerts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plate_text   TEXT NOT NULL,
    track_id     UUID REFERENCES tracks(track_id),
    drone_id     TEXT REFERENCES drones(drone_id),
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    frame_ts     TIMESTAMPTZ,
    confidence   REAL,
    vehicle_type TEXT,
    is_confirmed BOOLEAN NOT NULL DEFAULT false,
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS idx_theft_alerts_plate ON theft_alerts (plate_text);
CREATE INDEX IF NOT EXISTS idx_theft_alerts_time  ON theft_alerts (detected_at DESC);

-- Grant access to dvas_app
GRANT ALL PRIVILEGES ON face_embeddings, plate_watchlist, theft_alerts TO dvas_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas_app;

-- person_identities: global Re-ID across cameras
CREATE TABLE IF NOT EXISTS person_identities (
    identity_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id       INT NOT NULL,
    best_image_key   TEXT,                    -- MinIO object key for best face crop
    drone_id         TEXT REFERENCES drones(drone_id),
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding_count  INT NOT NULL DEFAULT 1,
    notes            TEXT
);
CREATE INDEX IF NOT EXISTS idx_person_identities_cluster ON person_identities(cluster_id);
GRANT ALL PRIVILEGES ON person_identities TO dvas_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas_app;
