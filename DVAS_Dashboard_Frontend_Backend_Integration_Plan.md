# DVAS Dashboard — Wiring the Existing Frontend to the Real Backend

**Scope:** you already have a scaffolded fullstack dashboard (`DVAS_Full_Dashboard_initial/`) — a React
frontend with four pages (`CommandDashboard.js`, `EdgeDashboard.js`, `IngestDashboard.js`,
`AdminDashboard.js`) plus `Login.js`, and a FastAPI-style `backend/` with `server.py`, `database.py`,
`models.py`. This plan reuses that scaffold as-is rather than building a fresh dashboard folder — the
goal is to replace whatever mock/placeholder data it currently returns with real DVAS data (Redis
confirmed events, Postgres history, Keycloak auth), and get it running on the same Ubuntu laptop as
everything else.

**Assumption to confirm:** based on the file layout (`craco.config.js`, `design_guidelines.json`,
shadcn/ui components, a `health-check` webpack plugin, `testIds` constants) this looks like a
generated fullstack scaffold — typically FastAPI + MongoDB (`database.py` + `models.py` + Motor) with
mock/demo data and a self-contained auth flow. If that's right, the integration work below is mostly
about **adding new routes and a WebSocket to `server.py`** that talk to the *real* DVAS backend
(Redis/Postgres/Keycloak) rather than the scaffold's own mock DB — I'd keep Mongo only if you want it
for something DVAS doesn't already have a store for (e.g. dashboard-specific UI preferences), and
route everything else to the real system. Correct me if `server.py` already has real logic in it and
this assumption's wrong.

---

## 1. What "integrated" actually means, page by page

| Page | Currently (assumed) | After integration |
|---|---|---|
| `Login.js` | Mock/local auth | Redirects to Keycloak, or posts credentials to Keycloak's token endpoint directly; stores the real JWT |
| `CommandDashboard.js` | Static/mock alerts | Live feed from `drone:events:confirmed` over WebSocket + historical query against Postgres `confirmed_events` |
| `EdgeDashboard.js` | Static/mock | Edge laptop health (buffer status, stream continuity) — pulled from Prometheus metrics the edge laptop already exposes, or a lightweight `/api/edge/status` route on the ingest API |
| `IngestDashboard.js` | Static/mock | Queue depth, drone connection status — pulled from Valkey (`XLEN`, consumer group lag) via a new backend route |
| `AdminDashboard.js` | Static/mock | User/role management via Keycloak's admin REST API; audit log view from Postgres `audit_log` table |

Nothing about the *pages themselves* (layout, components, `LiveMap.js`) needs rebuilding — they were
scaffolded with the right shape already. This is a data-source swap, not a rewrite.

---

## 2. Backend integration (`backend/server.py`)

Add, without removing the existing app structure:

```python
# backend/redis_client.py                      (NEW)
import redis.asyncio as aioredis
redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://valkey:6379/0"))

# backend/routes/events.py                      (NEW)
router = APIRouter(prefix="/api/events")

@router.get("/confirmed")
async def recent_confirmed(limit: int = 50):
    # query Postgres confirmed_events table — historical/paged view for CommandDashboard.js on load
    ...

@router.websocket("/ws")
async def confirmed_events_ws(ws: WebSocket):
    await ws.accept()
    last_id = "$"
    while True:
        streams = await redis.xread({"drone:events:confirmed": last_id}, block=5000)
        for _, entries in streams:
            for entry_id, fields in entries:
                await ws.send_json(json.loads(fields[b"data"]))
                last_id = entry_id

# backend/routes/ops.py                         (NEW)
router = APIRouter(prefix="/api/ops")

@router.get("/queue-health")
async def queue_health():
    # XLEN on each drone:frames:{id} stream, consumer group lag → feeds IngestDashboard.js
    ...

@router.get("/edge-status")
async def edge_status():
    # scrape or proxy Prometheus edge metrics → feeds EdgeDashboard.js
    ...

# backend/auth/keycloak.py                       (NEW)
# validates the JWT on every request (same public key / realm the ingest API already trusts)
def verify_token(token: str = Depends(oauth2_scheme)) -> dict: ...
```

`server.py` then just does `app.include_router(events.router)`, `app.include_router(ops.router)`, and
applies `Depends(verify_token)` to every route except `/health`. This is additive — whatever the
scaffold's existing routes do for the demo UI shell can stay until you're ready to remove them.

---

## 3. Frontend integration

```javascript
// frontend/src/lib/useDvasSocket.js            (IMPLEMENT — currently likely a stub)
export function useDvasSocket(onEvent) {
  useEffect(() => {
    const token = getAuthToken();                       // from AuthContext
    const ws = new WebSocket(`${WS_BASE}/api/events/ws?token=${token}`);
    ws.onmessage = (msg) => onEvent(JSON.parse(msg.data));
    return () => ws.close();
  }, []);
}
```

```javascript
// frontend/src/pages/CommandDashboard.js        (WIRE UP)
useDvasSocket((event) => setLiveAlerts(prev => [event, ...prev].slice(0, 100)));
useEffect(() => { api.get('/api/events/confirmed').then(setHistoricalEvents); }, []);
```

```javascript
// frontend/src/context/AuthContext.js           (REPLACE mock auth)
async function login(username, password) {
  const res = await fetch(`${KEYCLOAK_URL}/realms/dvas/protocol/openid-connect/token`, {
    method: 'POST',
    body: new URLSearchParams({ grant_type: 'password', client_id: 'dashboard', username, password }),
  });
  const { access_token } = await res.json();
  setToken(access_token);   // attached to every subsequent api.js call and the WebSocket
}
```

```javascript
// frontend/src/lib/api.js                       (POINT AT REAL BACKEND)
const API_BASE = process.env.REACT_APP_API_URL;   // set per environment, see §5
axios.defaults.baseURL = API_BASE;
axios.interceptors.request.use(cfg => { cfg.headers.Authorization = `Bearer ${getToken()}`; return cfg; });
```

`LiveMap.js` needs `lat`/`lon` on confirmed events to plot pins — confirm your `FusionEngine` output
(from the AI integration plan) includes drone GPS position in the event payload; if drones don't
report GPS today, this is a gap to flag rather than something the frontend can invent.

---

## 4. Directory structure — where this sits inside the two-laptop layout

This slots directly into `central/` from the V1 plan, replacing the placeholder `dashboard-backend/`
and `dashboard-frontend/` folders with the real scaffold, now wired:

```
central/
├── docker-compose.yml                          # gains 2 new services: dashboard-backend, dashboard-frontend
│
├── dashboard-backend/                          ← was DVAS_Full_Dashboard_initial/backend/
│   ├── server.py                                # existing app + new routers included
│   ├── database.py                              # existing — keep only if still needed for non-DVAS data
│   ├── models.py
│   ├── redis_client.py                          # NEW
│   ├── routes/
│   │   ├── events.py                            # NEW — confirmed events REST + WebSocket
│   │   ├── ops.py                                # NEW — queue health, edge status
│   │   └── admin.py                              # NEW — proxies Keycloak admin API for AdminDashboard.js
│   ├── auth/
│   │   └── keycloak.py                           # NEW — JWT verification shared logic
│   ├── requirements.txt                          # add: redis, python-jose (or authlib), asyncpg
│   ├── pyproject.toml
│   ├── pytest.ini
│   └── tests/backend_test.py                      # extend with tests for new routes
│
└── dashboard-frontend/                          ← was DVAS_Full_Dashboard_initial/frontend/
    ├── .env.production                            # REACT_APP_API_URL, REACT_APP_WS_URL, KEYCLOAK_URL
    ├── package.json
    ├── craco.config.js
    ├── src/
    │   ├── pages/
    │   │   ├── CommandDashboard.js                 # WIRED — live + historical events
    │   │   ├── EdgeDashboard.js                     # WIRED — edge health
    │   │   ├── IngestDashboard.js                   # WIRED — queue/ops health
    │   │   ├── AdminDashboard.js                    # WIRED — users/roles/audit
    │   │   └── Login.js                             # WIRED — real Keycloak auth
    │   ├── lib/
    │   │   ├── api.js                               # MODIFIED — real base URL + auth header
    │   │   └── useDvasSocket.js                     # IMPLEMENTED — real WebSocket client
    │   ├── context/
    │   │   ├── AuthContext.js                       # MODIFIED — Keycloak token flow
    │   │   ├── ThemeContext.js                       # unchanged
    │   │   └── I18nContext.js                        # unchanged
    │   └── components/
    │       ├── LiveMap.js                            # needs drone GPS in event payload — see §3
    │       └── layout/AppShell.js                    # unchanged
    └── build/                                        # `npm run build` output, mounted into NGINX
```

Nothing outside `central/dashboard-backend/` and `central/dashboard-frontend/` changes — the rest of
`central/` (gateway, analytics, evidence, db) stays exactly as scoped in the V1 plan; this dashboard
just becomes a consumer of what those already produce.

---

## 5. How you'll actually run it

### Local development (iterating on the dashboard itself, fastest feedback loop)

```bash
# Terminal 1 — backend, against the already-running central stack's Redis/Postgres
cd central/dashboard-backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --reload --port 8001

# Terminal 2 — frontend, hot-reload
cd central/dashboard-frontend
npm install
REACT_APP_API_URL=http://localhost:8001 \
REACT_APP_WS_URL=ws://localhost:8001 \
npm start
```

Open `http://localhost:3000` — this talks to your already-running `docker compose` ingest/analytics
stack directly (same laptop, so `localhost` reaches Valkey/Postgres containers via their published
ports).

### Production-style run (what you'd actually demo)

Add to the existing `central/docker-compose.yml`:

```yaml
  dashboard-backend:
    build: ./dashboard-backend
    environment:
      - REDIS_URL=redis://valkey:6379/0
      - DATABASE_URL=postgresql://dvas:${DB_PASSWORD}@postgres:5432/dvas
      - KEYCLOAK_URL=http://keycloak:8080
    depends_on: [valkey, postgres, keycloak]
    networks: [dvas-internal]

  dashboard-frontend:
    build: ./dashboard-frontend
    # multi-stage Dockerfile: npm run build → nginx serves /build as static files
    ports: []                          # not exposed directly — NGINX gateway serves it
    networks: [dvas-internal]
```

And one addition to `gateway/nginx/default.conf`:

```nginx
location / {
    root /usr/share/nginx/html/dashboard;   # dashboard-frontend's build output, volume-mounted here
    try_files $uri /index.html;
}
location /api/ {
    proxy_pass http://dashboard-backend:8001/;
}
location /api/events/ws {
    proxy_pass http://dashboard-backend:8001/api/events/ws;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

Then, on the Ubuntu laptop:

```bash
cd central
docker compose up -d --build
```

Everyone on the same WireGuard VPN (or LAN, per the V1 networking options) opens a browser to
`https://<ubuntu-laptop-vpn-ip>/` and gets the real dashboard — logging in through Keycloak, seeing
live confirmed events as `gpu_consumer.py` produces them, with no separate frontend server or port to
manage; it's served by the same NGINX that's already the single exposed entry point for the whole
system.

---

## 6. Open items to confirm

- Confirm whether `backend/database.py` currently talks to MongoDB or something else — determines
  whether it's worth keeping for anything, or removable once the real routes are in.
- Confirm drones report GPS coordinates today, or `LiveMap.js` has nothing to plot yet.
- Decide the Keycloak client setup for the dashboard specifically (a `dashboard` client alongside
  whatever client the edge laptops/ingest API already use) — needed before `Login.js`'s token request
  will work.
