-- ─── 008: Intrusion Detection Presets & Module Registration ─────────────────
-- Adds 'intrusion' module to the Full Surveillance preset and creates
-- dedicated perimeter security presets for geofence-based intrusion detection.
--
-- Depends on: 001_dvas_schema.sql (analytics_presets table)
-- Depends on: 007_intrusion_schema.sql (intrusion_events table)
-- Safe to re-run (uses ON CONFLICT and conditional array operations).

BEGIN;

-- 1. Add 'intrusion' to the Full Surveillance preset (if not already present)
UPDATE analytics_presets
   SET modules = array_append(modules, 'intrusion'),
       description = 'All modules active (crowd, person, face, vehicle, ANPR, intrusion)'
 WHERE name = 'Full Surveillance'
   AND NOT ('intrusion' = ANY(modules));

-- 2. Add new intrusion-specific presets
INSERT INTO analytics_presets (name, description, modules) VALUES
    ('Perimeter Security',
     'Geofence intrusion detection with vehicle and person tracking for restricted area surveillance',
     ARRAY['intrusion', 'vehicle', 'person']),
    ('Intrusion Only',
     'Standalone geofence intrusion detection — lightweight single-model deployment',
     ARRAY['intrusion']),
    ('Critical Infrastructure',
     'Full perimeter and identity monitoring for high-security installations',
     ARRAY['intrusion', 'person', 'face', 'vehicle', 'anpr'])
ON CONFLICT (name) DO NOTHING;

COMMIT;
