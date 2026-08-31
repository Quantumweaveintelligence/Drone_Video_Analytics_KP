-- =============================================================================
-- audit_immutability.sql
-- Phase 6: Security Hardening — Cryptographic & Database Immutability
-- =============================================================================
-- Purpose: Enforce strict append-only immutability on audit_logs table.
--          Prevents all UPDATE and DELETE statements with a PostgreSQL trigger.
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION enforce_audit_log_immutability()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'DVAS-SEC-001: Audit logs are legally immutable under Indian Evidence Act Sec 65B. UPDATE and DELETE operations are strictly prohibited.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_log_no_update ON audit_logs;
CREATE TRIGGER trg_audit_log_no_update
BEFORE UPDATE ON audit_logs
FOR EACH ROW
EXECUTE FUNCTION enforce_audit_log_immutability();

DROP TRIGGER IF EXISTS trg_audit_log_no_delete ON audit_logs;
CREATE TRIGGER trg_audit_log_no_delete
BEFORE DELETE ON audit_logs
FOR EACH ROW
EXECUTE FUNCTION enforce_audit_log_immutability();

COMMIT;
