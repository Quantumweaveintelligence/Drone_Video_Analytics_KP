-- ═══════════════════════════════════════════════════════════════════════════
-- DVAS Analytics — 012_monthly_partitions.sql   (RunPod plan Phase 1.3 / 12.5)
--
-- The baseline migrations only create monthly partitions up to 2026-08
-- (detections, confirmed_events, crowd_density_readings, audit_log) or 2026-10
-- (intrusion_events, vessel_events). On a fresh database any insert after that
-- month fails with "no partition of relation ... found for row".
--
-- This migration adds dvas_ensure_monthly_partitions(months_ahead):
--   * creates <table>_YYYY_MM for the current month + `months_ahead` months
--   * creates a <table>_default partition when the table has no DEFAULT
--     partition, so a missed month can never drop rows
-- It is idempotent and safe to call repeatedly (the RunPod bootstrap calls it
-- on every boot; the monthly housekeeping job calls it on the 25th).
-- Failures for an individual partition are reported as WARNINGs, not errors.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION dvas_ensure_monthly_partitions(months_ahead INT DEFAULT 3)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    parent     TEXT;
    month_from DATE;
    part_name  TEXT;
    default_id OID;
BEGIN
    FOREACH parent IN ARRAY ARRAY[
        'detections', 'confirmed_events', 'crowd_density_readings', 'audit_log',
        'intrusion_events', 'vessel_events', 'anomaly_events'
    ] LOOP
        -- Skip tables that do not exist or are not partitioned
        SELECT pt.partdefid INTO default_id
          FROM pg_partitioned_table pt
         WHERE pt.partrelid = to_regclass('public.' || parent);
        IF NOT FOUND THEN
            CONTINUE;
        END IF;

        FOR i IN 0..months_ahead LOOP
            month_from := (date_trunc('month', now()) + make_interval(months => i))::date;
            part_name  := format('%s_%s', parent, to_char(month_from, 'YYYY_MM'));
            IF to_regclass('public.' || part_name) IS NULL THEN
                BEGIN
                    EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS public.%I PARTITION OF public.%I FOR VALUES FROM (%L) TO (%L)',
                        part_name, parent, month_from, (month_from + INTERVAL '1 month')::date
                    );
                EXCEPTION WHEN others THEN
                    RAISE WARNING 'dvas_ensure_monthly_partitions: % not created: %', part_name, SQLERRM;
                END;
            END IF;
        END LOOP;

        IF default_id = 0 THEN
            BEGIN
                EXECUTE format('CREATE TABLE IF NOT EXISTS public.%I PARTITION OF public.%I DEFAULT',
                               parent || '_default', parent);
            EXCEPTION WHEN others THEN
                RAISE WARNING 'dvas_ensure_monthly_partitions: %_default not created: %', parent, SQLERRM;
            END;
        END IF;
    END LOOP;
END;
$$;

DO $$ BEGIN PERFORM dvas_ensure_monthly_partitions(3); END $$;
