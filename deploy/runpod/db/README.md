# deploy/runpod/db — database baseline for the pod

| File | Created by | Loaded by |
|---|---|---|
| `analytics_000_baseline_schema.sql` | `tools/dump_baseline_schema.sh` (Phase 1.3, from the live `drone_postgres`) | `bin/db-bootstrap.sh` on first boot |
| `analytics_001_reference_data.sql` | same tool (reference rows only) | `bin/db-bootstrap.sh` |
| `dashboard_000_baseline_schema.sql` | same tool (from the live `dvas_postgres`) | `bin/db-bootstrap.sh` |
| `migrations/analytics/NNN_*.sql`, `migrations/dashboard/NNN_*.sql` | you, for every schema change after first deploy (Phase 12.2) | `bin/dvas-bootstrap.sh` on every boot, once each (table `deploy_migrations`) |

Without the dumps the pod still builds a working schema: every
`dvas_analytics/db/migrations/*.sql` (verified on a fresh PostgreSQL 16) and the
dashboard backend's own `create_all` + `DVAS_Dashboard/backend/migrations/audit_*.sql`.
The dumps are preferred because they match exactly what the code runs against today.

Rules: never edit a baseline after the first deploy; never put detections, faces,
plates or audit rows in any file here.
