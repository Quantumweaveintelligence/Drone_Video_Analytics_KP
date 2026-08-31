-- =============================================================================
-- migration_audit_v2.sql
-- Audit Log Explorer — Phase 1: Database Foundation
-- =============================================================================
-- Purpose: Expand audit_logs table with enriched fields for production-grade
--          audit trail, create filter presets table, backfill existing rows.
--
-- Target DB: dvas_postgres (dashboard stack)
-- Run as:    docker exec dvas_postgres psql -U dvas -d dvas -f /tmp/audit_v2.sql
--
-- IMPORTANT: This migration is backward-compatible. All new columns accept NULL.
--            Existing ~585 rows are preserved and backfilled with inferred values.
-- =============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Add new columns to audit_logs
-- ─────────────────────────────────────────────────────────────────────────────

-- Actor enrichment
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_name VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_session_id VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_device_id VARCHAR;

-- Action classification
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS action_type VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS category VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS severity VARCHAR DEFAULT 'info';

-- Target resource
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS target_type VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS target_id VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS target_name VARCHAR;

-- Before/after diff values (JSONB)
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS before_values JSONB;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS after_values JSONB;

-- Result / status
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'success';
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS error_code VARCHAR;

-- Tracing / correlation
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS source_module VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS source_endpoint VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_id VARCHAR;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS correlation_id VARCHAR;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Performance indexes
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_audit_action_type     ON audit_logs(action_type);
CREATE INDEX IF NOT EXISTS idx_audit_category        ON audit_logs(category);
CREATE INDEX IF NOT EXISTS idx_audit_severity        ON audit_logs(severity);
CREATE INDEX IF NOT EXISTS idx_audit_status          ON audit_logs(status);
CREATE INDEX IF NOT EXISTS idx_audit_request_id      ON audit_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_audit_correlation_id  ON audit_logs(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts_desc         ON audit_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor_id        ON audit_logs(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor_role      ON audit_logs(actor_role);
CREATE INDEX IF NOT EXISTS idx_audit_target_type     ON audit_logs(target_type);
CREATE INDEX IF NOT EXISTS idx_audit_action          ON audit_logs(action);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Create audit_filter_presets table
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS audit_filter_presets (
    id          VARCHAR PRIMARY KEY,
    name        VARCHAR NOT NULL,
    owner_id    VARCHAR NOT NULL,
    filters     JSONB   NOT NULL,
    is_shared   BOOLEAN DEFAULT FALSE,
    created_at  VARCHAR,
    updated_at  VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_preset_owner ON audit_filter_presets(owner_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Backfill existing rows with inferred classification
-- ─────────────────────────────────────────────────────────────────────────────

-- action_type: infer from action string
UPDATE audit_logs SET action_type = CASE
    WHEN action = 'login'                                   THEN 'login'
    WHEN action = 'logout'                                  THEN 'logout'
    WHEN action LIKE '%create%'                             THEN 'create'
    WHEN action LIKE '%patch%' OR action LIKE '%update%'    THEN 'update'
    WHEN action LIKE '%delete%' OR action LIKE '%remove%'
         OR action = 'user_suspend'                         THEN 'delete'
    WHEN action LIKE 'export%' OR action LIKE '%download%'
         OR action = 'bundle_download'                      THEN 'export'
    WHEN action = 'alert_ack'                               THEN 'update'
    WHEN action LIKE 'watchlist_add%'                       THEN 'create'
    WHEN action LIKE 'model_config%'                        THEN 'update'
    ELSE 'system'
END
WHERE action_type IS NULL;

-- category: infer from action string
UPDATE audit_logs SET category = CASE
    WHEN action IN ('login', 'logout')                      THEN 'authentication'
    WHEN action LIKE 'user%'                                THEN 'user_mgmt'
    WHEN action LIKE 'export%' OR action = 'bundle_download' THEN 'evidence'
    WHEN action LIKE 'model_config%'                        THEN 'configuration'
    WHEN action LIKE 'watchlist%'                           THEN 'operations'
    WHEN action = 'alert_ack'                               THEN 'operations'
    WHEN action = 'shift_summary_download'                  THEN 'operations'
    ELSE 'operations'
END
WHERE category IS NULL;

-- severity: infer from action string
UPDATE audit_logs SET severity = CASE
    WHEN action IN ('user_create', 'user_suspend', 'user_patch',
                    'export_approve', 'export_deny')        THEN 'critical'
    WHEN action IN ('export_request', 'model_config_update',
                    'watchlist_add', 'watchlist_remove',
                    'bundle_download')                       THEN 'warning'
    WHEN action IN ('login', 'logout', 'alert_ack',
                    'shift_summary_download')                THEN 'info'
    ELSE 'info'
END
WHERE severity IS NULL OR severity = 'info';

-- status: all existing rows are successful operations (failures weren't logged)
UPDATE audit_logs SET status = 'success' WHERE status IS NULL;

-- source_module: all existing entries are from dashboard backend
UPDATE audit_logs SET source_module = 'dashboard' WHERE source_module IS NULL;

-- source_endpoint: infer from action string
UPDATE audit_logs SET source_endpoint = CASE
    WHEN action = 'login'                   THEN 'POST /api/auth/login'
    WHEN action = 'logout'                  THEN 'POST /api/auth/logout'
    WHEN action = 'alert_ack'               THEN 'POST /api/alerts/{id}/ack'
    WHEN action = 'export_request'          THEN 'POST /api/exports'
    WHEN action LIKE 'export_%'             THEN 'POST /api/exports/{id}/decision'
    WHEN action = 'user_create'             THEN 'POST /api/users'
    WHEN action = 'user_suspend'            THEN 'DELETE /api/users/{id}'
    WHEN action = 'model_config_update'     THEN 'PUT /api/model-config/{scope}'
    WHEN action = 'watchlist_add'           THEN 'POST /api/analytics/watchlist'
    WHEN action = 'watchlist_remove'        THEN 'DELETE /api/analytics/watchlist/{plate}'
    WHEN action = 'shift_summary_download'  THEN 'GET /api/shift-summary'
    WHEN action = 'bundle_download'         THEN 'GET /api/exports/{id}/bundle'
    ELSE NULL
END
WHERE source_endpoint IS NULL;

COMMIT;
