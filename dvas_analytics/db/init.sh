#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# DVAS Database Initialisation — runs inside Postgres container on first start
# Creates the 'dvas' database and runs all migration scripts.
# ═══════════════════════════════════════════════════════════════════════════
set -e

echo "[INIT] Creating DVAS analytics database..."

# Create the dvas database on the same Postgres instance as keycloak
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE dvas'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dvas')\gexec

    \c dvas

    -- Create application user if not exists
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dvas') THEN
            CREATE ROLE dvas LOGIN PASSWORD '${DVAS_DB_PASSWORD:-dvas_secret}';
        END IF;
    END\$\$;

    GRANT ALL PRIVILEGES ON DATABASE dvas TO dvas;
EOSQL

echo "[INIT] Running schema migrations on 'dvas' database..."

# Run migrations in order
for f in /docker-entrypoint-initdb.d/migrations/*.sql; do
    echo "[INIT] Applying: $f"
    psql -v ON_ERROR_STOP=1 --username "dvas" --dbname "dvas" -f "$f"
done

echo "[INIT] DVAS database setup complete!"
