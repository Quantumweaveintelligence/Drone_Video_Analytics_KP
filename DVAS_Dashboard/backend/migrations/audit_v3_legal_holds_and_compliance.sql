-- =============================================================================
-- audit_v3_legal_holds_and_compliance.sql
-- DVAS Audit Log Explorer Phase 3: Legal Holds, Compliance & Controlled Purge
-- =============================================================================

BEGIN;

-- 1. Add compliance_tags column to audit_logs if not already present
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'audit_logs' AND column_name = 'compliance_tags'
    ) THEN
        ALTER TABLE audit_logs ADD COLUMN compliance_tags JSONB DEFAULT '[]'::jsonb;
        CREATE INDEX IF NOT EXISTS idx_audit_compliance_tags ON audit_logs USING gin (compliance_tags);
    END IF;
END $$;

-- 2. Create audit_legal_holds table
CREATE TABLE IF NOT EXISTS audit_legal_holds (
    id VARCHAR PRIMARY KEY,
    audit_log_id VARCHAR NOT NULL REFERENCES audit_logs(id) ON DELETE RESTRICT,
    case_number VARCHAR NOT NULL,
    hold_reason TEXT NOT NULL,
    placed_by VARCHAR NOT NULL,
    placed_by_name VARCHAR,
    placed_at VARCHAR NOT NULL,
    active BOOLEAN DEFAULT TRUE NOT NULL,
    released_by VARCHAR,
    released_at VARCHAR,
    release_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_legal_holds_audit_log_id ON audit_legal_holds(audit_log_id);
CREATE INDEX IF NOT EXISTS idx_legal_holds_active ON audit_legal_holds(active);
CREATE INDEX IF NOT EXISTS idx_legal_holds_case_number ON audit_legal_holds(case_number);

-- 3. Update database immutability trigger to allow controlled purge with session variable
CREATE OR REPLACE FUNCTION enforce_audit_log_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'DVAS-SEC-001: Audit logs are legally immutable under Indian Evidence Act Sec 65B. UPDATE operations are strictly prohibited.';
    ELSIF TG_OP = 'DELETE' THEN
        -- Allow DELETE only if explicit maintenance session variable is set
        IF current_setting('dvas.allow_audit_purge', true) = 'true' THEN
            -- Check if this specific row is protected by an active legal hold
            IF EXISTS (SELECT 1 FROM audit_legal_holds WHERE audit_log_id = OLD.id AND active = TRUE) THEN
                RAISE EXCEPTION 'DVAS-SEC-002: Cannot purge audit log % because it is protected by an active Legal Hold.', OLD.id;
            END IF;
            RETURN OLD;
        ELSE
            RAISE EXCEPTION 'DVAS-SEC-001: Audit logs are legally immutable under Indian Evidence Act Sec 65B. DELETE operations are strictly prohibited without authorized statutory purge procedure.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
