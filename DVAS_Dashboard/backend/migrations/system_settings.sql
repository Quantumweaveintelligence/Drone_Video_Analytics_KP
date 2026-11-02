-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Dashboard — system_settings.sql   (Admin Policy / System Settings, ADM-04/05/06)
-- Settings the backend enforces at run time; every change is audited with the
-- before / after values. Unset keys fall back to the defaults in
-- services/settings_service.py. Safe to re-run.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS system_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_by TEXT,
    updated_at TIMESTAMPTZ
);
