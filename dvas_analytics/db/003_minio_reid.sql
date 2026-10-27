-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — 003_minio_reid.sql
-- MinIO/object-storage linkage + vehicle-plate join + pgvector support
-- ═══════════════════════════════════════════════════════════════════════════

-- Enable pgvector if available (graceful fallback — migration still succeeds without it)
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector not available; face_embeddings will use FLOAT4[]';
END$$;

-- Add MinIO object key column to face_embeddings (replaces image_path for object storage)
ALTER TABLE face_embeddings ADD COLUMN IF NOT EXISTS object_key TEXT;
ALTER TABLE face_embeddings ADD COLUMN IF NOT EXISTS thumbnail_key TEXT;

-- If pgvector is installed, add a vector column for ANN search alongside FLOAT4[] for compat
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        ALTER TABLE face_embeddings ADD COLUMN IF NOT EXISTS embedding_vec vector(2048);
        CREATE INDEX IF NOT EXISTS idx_face_embeddings_vec
            ON face_embeddings USING ivfflat (embedding_vec vector_cosine_ops)
            WITH (lists = 100);
    END IF;
END$$;

-- vehicle_plate_links: associates vehicle tracker_id with plate text
-- Written by ANPR service after successful OCR; read by vehicle service
CREATE TABLE IF NOT EXISTS vehicle_plate_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tracker_id      BIGINT NOT NULL,
    drone_id        TEXT NOT NULL REFERENCES drones(drone_id),
    plate_text      TEXT NOT NULL,
    plate_conf      REAL NOT NULL,
    vehicle_type    TEXT,
    colour          TEXT,
    plate_image_key TEXT,              -- MinIO key for plate crop
    vehicle_image_key TEXT,            -- MinIO key for vehicle crop
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tracker_id, drone_id, plate_text)
);
CREATE INDEX IF NOT EXISTS idx_vpl_tracker ON vehicle_plate_links(tracker_id, drone_id);
CREATE INDEX IF NOT EXISTS idx_vpl_plate   ON vehicle_plate_links(plate_text);
CREATE INDEX IF NOT EXISTS idx_vpl_time    ON vehicle_plate_links(linked_at DESC);

-- plate_reads: add MinIO image key column (replaces vehicle_image_b64 for production)
ALTER TABLE plate_reads ADD COLUMN IF NOT EXISTS plate_image_key   TEXT;
ALTER TABLE plate_reads ADD COLUMN IF NOT EXISTS vehicle_image_key TEXT;

-- crowd_density_readings: already has heatmap_object_key — add density_per_zone JSONB
ALTER TABLE crowd_density_readings ADD COLUMN IF NOT EXISTS zone_densities JSONB;

-- evidence_frames: already exists — add MinIO bucket column
ALTER TABLE evidence_frames ADD COLUMN IF NOT EXISTS bucket TEXT DEFAULT 'dvas-evidence';

-- Additional indexes for common dashboard queries
CREATE INDEX IF NOT EXISTS idx_plate_reads_text_time
    ON plate_reads (plate_text, created_at DESC);
-- vehicle_attributes has no created_at in 001; added here so this index can be
-- built on a fresh database (RunPod plan F2 — fresh-DB init used to fail here).
ALTER TABLE vehicle_attributes ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_vehicle_attrs_type
    ON vehicle_attributes (vehicle_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_crowd_drone_time
    ON crowd_density_readings (drone_id, ts DESC);

-- Grant access
GRANT ALL PRIVILEGES ON vehicle_plate_links TO dvas_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO dvas_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO dvas_app;
