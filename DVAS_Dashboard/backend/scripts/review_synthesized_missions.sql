-- One-off review (B26, docs/evidence-reports/03): missions rows that GET /api/missions
-- used to create on its own, with a made-up name, operator and jurisdiction.
-- Read-only. Do NOT delete automatically: each row needs a human decision
-- (keep and correct, or remove) because reports may already cite it.
--
--   psql "$DASHBOARD_DATABASE_URL" -f scripts/review_synthesized_missions.sql
SELECT m.mission_id,
       m.name,
       m.operator_name,
       m.jurisdiction,
       m.drone_id,
       m.started_at,
       (SELECT count(*) FROM alerts   a WHERE a.mission_id = m.mission_id) AS alerts,
       (SELECT count(*) FROM evidence e WHERE e.mission_id = m.mission_id) AS evidence,
       (SELECT count(*) FROM reports  r WHERE r.params::text LIKE '%' || m.mission_id || '%') AS reports_citing
FROM missions m
WHERE m.operator_ids = '[]'::jsonb
  AND m.name = 'Mission ' || m.mission_id
  AND m.status = 'completed'
ORDER BY m.started_at NULLS LAST, m.mission_id;
