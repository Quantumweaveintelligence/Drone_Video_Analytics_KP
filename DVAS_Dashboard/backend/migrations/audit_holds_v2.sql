-- Audit legal holds: record the legal authority and contact given when a hold is placed
-- (Audit page "Place Legal Hold" form). Idempotent; applied at backend startup.
ALTER TABLE audit_legal_holds ADD COLUMN IF NOT EXISTS legal_authority TEXT;
ALTER TABLE audit_legal_holds ADD COLUMN IF NOT EXISTS contact_email TEXT;
