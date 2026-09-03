-- 006_person_embeddings.sql
-- Dedicated storage for Person whole-body appearance embeddings (Person Re-ID)

CREATE TABLE IF NOT EXISTS person_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id UUID REFERENCES tracks(track_id) ON DELETE CASCADE,
    tracker_id INTEGER,
    image_path TEXT,
    object_key TEXT NOT NULL,
    encrypted_embedding BYTEA,
    drone_id TEXT,
    mission_id TEXT,
    cluster_id INTEGER,
    is_duplicate BOOLEAN DEFAULT FALSE,
    sharpness_score REAL DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_person_embeddings_cluster ON person_embeddings(cluster_id);
CREATE INDEX IF NOT EXISTS idx_person_embeddings_mission ON person_embeddings(mission_id);
CREATE INDEX IF NOT EXISTS idx_person_embeddings_drone ON person_embeddings(drone_id);
CREATE INDEX IF NOT EXISTS idx_person_embeddings_created ON person_embeddings(created_at DESC);

GRANT ALL PRIVILEGES ON TABLE person_embeddings TO keycloak;
GRANT ALL PRIVILEGES ON TABLE person_embeddings TO dvas_app;
GRANT ALL PRIVILEGES ON TABLE person_embeddings TO dvas;
