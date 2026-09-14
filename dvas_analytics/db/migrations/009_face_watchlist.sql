-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — Face Watchlist & Alerts Schema (009_face_watchlist.sql)
-- Multi-embedding watchlist, temporal alerts, and Section 65B evidence chain
-- Kerala Police | Drone Video Analytics System
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── 1. Person Watchlist Table ──────────────────────────────────────────────
-- Stores enrolled subjects of interest (wanted, missing, VIP, POI)
CREATE TABLE IF NOT EXISTS person_watchlist (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT NOT NULL,                           -- "John Doe" / "Suspect A"
    category                TEXT NOT NULL DEFAULT 'wanted',          -- 'wanted' | 'missing' | 'vip' | 'poi'
    case_ref                TEXT,                                    -- FIR / Crime / General Diary number
    description             TEXT,                                    -- Identifying marks, notes, station info
    reference_image_key     TEXT,                                    -- MinIO object key for enrolled photo
    reference_image_sha256  TEXT,                                    -- SHA-256 for Section 65B integrity
    added_by                UUID REFERENCES users(user_id) ON DELETE SET NULL,
    added_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at              TIMESTAMPTZ,                             -- NULL = never expires
    is_active               BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_person_watchlist_active 
    ON person_watchlist (is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_person_watchlist_category 
    ON person_watchlist (category);
CREATE INDEX IF NOT EXISTS idx_person_watchlist_name 
    ON person_watchlist (name);
CREATE INDEX IF NOT EXISTS idx_person_watchlist_added 
    ON person_watchlist (added_at DESC);


-- ─── 2. Watchlist Embeddings Table ──────────────────────────────────────────
-- Stores multi-embeddings per enrolled subject (original, horizontal flip, brightness-adjusted)
-- Embeddings are AES-256-GCM encrypted for DPDP Act compliance (via shared.crypto)
CREATE TABLE IF NOT EXISTS watchlist_embeddings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_id        UUID NOT NULL REFERENCES person_watchlist(id) ON DELETE CASCADE,
    encrypted_embedding BYTEA NOT NULL,                              -- AES-256-GCM encrypted ArcFace 512-dim
    augmentation_type   TEXT NOT NULL DEFAULT 'original',            -- 'original' | 'flip' | 'bright'
    quality_score       REAL DEFAULT 0.0,                            -- Laplacian sharpness score
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_watchlist_embeddings_watchlist 
    ON watchlist_embeddings (watchlist_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_embeddings_quality 
    ON watchlist_embeddings (quality_score DESC);


-- ─── 3. Person Watchlist Alerts Table ────────────────────────────────────────
-- Real-time match alerts generated from drone stream with Section 65B evidence chain
CREATE TABLE IF NOT EXISTS person_watchlist_alerts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_id            UUID NOT NULL REFERENCES person_watchlist(id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    category                TEXT NOT NULL,
    drone_id                TEXT REFERENCES drones(drone_id) ON DELETE SET NULL,
    mission_id              TEXT,
    tracker_id              INTEGER,
    confidence              REAL NOT NULL,                           -- Cosine similarity (0.0 to 1.0)
    face_crop_key           TEXT,                                    -- MinIO key for detected face crop
    frame_crop_key          TEXT,                                    -- MinIO key for full aerial frame snapshot
    face_crop_sha256        TEXT,                                    -- Section 65B: SHA-256 of face crop
    frame_sha256            TEXT,                                    -- Section 65B: SHA-256 of full frame
    evidence_packet_key     TEXT,                                    -- Section 65B: MinIO key for bundle
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    frame_ts                TIMESTAMPTZ,
    is_confirmed            BOOLEAN NOT NULL DEFAULT false,          -- Operator confirmation status
    confirmed_by            UUID REFERENCES users(user_id) ON DELETE SET NULL,
    confirmed_at            TIMESTAMPTZ,
    notes                   TEXT                                     -- Operator remarks / action taken
);

CREATE INDEX IF NOT EXISTS idx_person_watchlist_alerts_time 
    ON person_watchlist_alerts (detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_person_watchlist_alerts_watchlist 
    ON person_watchlist_alerts (watchlist_id);
CREATE INDEX IF NOT EXISTS idx_person_watchlist_alerts_mission 
    ON person_watchlist_alerts (mission_id) WHERE mission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_person_watchlist_alerts_drone 
    ON person_watchlist_alerts (drone_id);
CREATE INDEX IF NOT EXISTS idx_person_watchlist_alerts_confirmed 
    ON person_watchlist_alerts (is_confirmed);


-- ─── 4. pgvector ANN Support (Optional/Graceful Fallback) ────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        -- Add 512-dim ArcFace vector column to face_embeddings for reverse search
        ALTER TABLE face_embeddings 
            ADD COLUMN IF NOT EXISTS arcface_vec vector(512);
        
        -- Create HNSW index for fast cosine similarity search
        CREATE INDEX IF NOT EXISTS idx_face_arcface_hnsw
            ON face_embeddings USING hnsw (arcface_vec vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);

        -- Add vector column to watchlist_embeddings
        ALTER TABLE watchlist_embeddings
            ADD COLUMN IF NOT EXISTS embedding_vec vector(512);
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector vector(512) columns skipped; encrypted_embedding BYTEA will be used';
END$$;


-- ─── 5. Role Permissions ───────────────────────────────────────────────────
GRANT ALL PRIVILEGES ON TABLE person_watchlist TO dvas, dvas_app, keycloak;
GRANT ALL PRIVILEGES ON TABLE watchlist_embeddings TO dvas, dvas_app, keycloak;
GRANT ALL PRIVILEGES ON TABLE person_watchlist_alerts TO dvas, dvas_app, keycloak;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas, dvas_app, keycloak;
