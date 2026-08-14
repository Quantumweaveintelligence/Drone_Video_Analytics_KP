# DVAS Repository Suitability Check Result

📋 RESULTS OUTPUT

```text
=== REPO SUITABILITY OUTPUT ===

### Current Branch
fatal: not a git repository (or any of the parent directories): .git
### Latest Commit
fatal: not a git repository (or any of the parent directories): .git
### Remote Origin
fatal: not a git repository (or any of the parent directories): .git
### Version Check
No version file found
### Top-level Directories
total 28
drwxrwxr-x  6 govind-v-kartha govind-v-kartha 4096 Aug 10 18:31 .
drwxr-xr-x 14 govind-v-kartha govind-v-kartha 4096 Aug 11 12:32 ..
-rw-rw-r--  1 govind-v-kartha govind-v-kartha  486 Aug 10 18:31 .gitignore
drwxrwxr-x  8 govind-v-kartha govind-v-kartha 4096 Aug 10 18:31 DVAS_Dashboard
drwxrwxr-x  9 govind-v-kartha govind-v-kartha 4096 Aug 10 18:31 dvas_analytics
drwxrwxr-x  8 govind-v-kartha govind-v-kartha 4096 Aug 10 18:31 dvas_edge
drwxrwxr-x  6 govind-v-kartha govind-v-kartha 4096 Aug 10 18:31 dvas_ingest
### Backend Structure
Backend folder not found
### Frontend Structure
Frontend folder not found
### Edge Structure
Edge folder not found
### Python Dependencies
./DVAS_Dashboard/pyproject.toml
./DVAS_Dashboard/backend/requirements.txt
./DVAS_Dashboard/backend/pyproject.toml
./dvas_edge/requirements.txt
./dvas_edge/pyproject.toml
./dvas_analytics/crowd/requirements.txt
./dvas_analytics/anpr/requirements.txt
./dvas_analytics/vehicle/requirements.txt
./dvas_analytics/person/requirements.txt
./dvas_analytics/gateway/requirements.txt
### JavaScript Dependencies
./DVAS_Dashboard/frontend/package.json
### Python Package Count (if requirements.txt exists)
33 ./DVAS_Dashboard/backend/requirements.txt
66 ./dvas_edge/requirements.txt
13 ./dvas_analytics/crowd/requirements.txt
19 ./dvas_analytics/anpr/requirements.txt
16 ./dvas_analytics/vehicle/requirements.txt
19 ./dvas_analytics/person/requirements.txt
7 ./dvas_analytics/gateway/requirements.txt
27 ./dvas_ingest/ingest/api/requirements.txt
### Docker Files
./DVAS_Dashboard/frontend/Dockerfile
./DVAS_Dashboard/docker-compose.yml
./DVAS_Dashboard/backend/Dockerfile
./dvas_edge/Dockerfile
./dvas_analytics/crowd/Dockerfile
./dvas_analytics/anpr/Dockerfile
./dvas_analytics/vehicle/Dockerfile
./dvas_analytics/person/Dockerfile
./dvas_analytics/gateway/Dockerfile
./dvas_ingest/Dockerfile.gpu_consumer
./dvas_ingest/docker-compose.yml
./dvas_ingest/ingest/api/Dockerfile
### Environment Files (Tracked)
### Hardcoded Passwords/Secrets
./DVAS_Dashboard/backend/server.py:            return _user_to_dict(user_obj, exclude_password=True)
./DVAS_Dashboard/backend/server.py:                user = _user_to_dict(user_obj, exclude_password=True)
./DVAS_Dashboard/backend/server.py:        return [_user_to_dict(r, exclude_password=True) for r in result.scalars().all()]
./DVAS_Dashboard/backend/server.py:                user = _user_to_dict(user_obj, exclude_password=True)
./dvas_edge/main.py:            password   = ing_cfg.password,
./dvas_edge/edge_api_server.py:                password=cfg.ingest.password,
./dvas_edge/ingest/edge_sender.py:PASSWORD    = os.getenv("EDGE_PASSWORD", "Operator@001")  # rotate after pilot
./dvas_edge/ingest/edge_sender.py:        self.password      = password
./dvas_edge/ingest/edge_sender.py:        password   = args.password,
./dvas_edge/ingest/edge_sender.py:        password   = args.password,
./dvas_edge/config/config.py:        password=_env("INGEST__PASSWORD", ing.get("password", IngestConfig.password)),
./dvas_ingest/ingest/scripts/setup_keycloak.py:ADMIN_PASSWORD = "Admin@KP2025!"
### JWT Secrets References
./DVAS_Dashboard/backend/server.py:JWT_SECRET = os.environ["JWT_SECRET"]
./DVAS_Dashboard/backend/server.py:    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
./DVAS_Dashboard/backend/server.py:        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
./DVAS_Dashboard/backend/server.py:            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
./DVAS_Dashboard/backend/server.py:        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
./DVAS_Dashboard/backend/server.py:            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
./DVAS_Dashboard/backend/server.py:        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
### API Key References
### FastAPI Endpoint Count (Python)
75
### Swagger/OpenAPI Config

=== END OF OUTPUT ===
```

---

## ⚠️ Summary Checklist

- **Branch check: Is it qa or uat?**: N (Root is not a Git repository)
- **Backend found?**: Y (Located in `DVAS_Dashboard/backend/`, `dvas_analytics/`, and `dvas_ingest/`)
- **Frontend found?**: Y (Located in `DVAS_Dashboard/frontend/`)
- **Edge found?**: Y (Located in `dvas_edge/`)
- **Docker files found?**: Y (Multiple Dockerfiles & `docker-compose.yml` files present)
- **Did you find real secrets (non-placeholder)?**: Y (Hardcoded default credential `Operator@001` in `./dvas_edge/ingest/edge_sender.py` and `Admin@KP2025!` in `./dvas_ingest/ingest/scripts/setup_keycloak.py`)
- **Endpoint count (approx)**: 75
