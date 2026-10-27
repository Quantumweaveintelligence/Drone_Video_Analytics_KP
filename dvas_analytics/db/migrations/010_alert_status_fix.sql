-- Migration 010: Alert Status and Track Lookup Optimization
-- Phase 5 of DVAS Person Tracking & Alert Hardening

-- 1. Add is_dismissed and dismissed_at to person_watchlist_alerts
ALTER TABLE person_watchlist_alerts 
    ADD COLUMN IF NOT EXISTS is_dismissed BOOLEAN DEFAULT false;

ALTER TABLE person_watchlist_alerts 
    ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ;

-- 2. Index for efficient pending and active alert queries
CREATE INDEX IF NOT EXISTS idx_watchlist_alerts_status
    ON person_watchlist_alerts (is_dismissed, is_confirmed, detected_at DESC);

-- 3. Composite expression index for fast tracker upsert lookups
CREATE INDEX IF NOT EXISTS idx_tracks_tracker_lookup 
    ON tracks (drone_id, track_type, ((metadata->>'tracker_id')), mission_id_str);

-- 4. Permissions
GRANT ALL PRIVILEGES ON TABLE person_watchlist_alerts TO dvas, dvas_app, keycloak;
