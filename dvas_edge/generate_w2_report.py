"""
generate_w2_report.py
=====================
Generates the Week 2 Weekly Progress Report for the DVAS project,
exactly following the structure and style of Weekly_Progress_Report_W1_Ingest.docx.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import copy

# ── Load the W1 template (inherits all theme/font/margin settings) ─────────────
TEMPLATE = r'D:\POLICE_DRONE\drone_analytics\Weekly_Progress_Report_W1_Ingest.docx'
OUTPUT   = r'D:\POLICE_DRONE\drone_analytics\Weekly_Progress_Report_W2_Verification.docx'

doc = Document(TEMPLATE)

# Clear all existing content (paragraphs + tables) but keep styles/theme/sections
from docx.oxml.ns import qn as _qn
for element in list(doc.element.body):
    # Preserve sectPr (section properties — margins, page size) so add_table works
    if element.tag.endswith('}sectPr'):
        continue
    doc.element.body.remove(element)

# ── Helpers ────────────────────────────────────────────────────────────────────

def h1(text):
    p = doc.add_paragraph(style='Heading 1')
    p.add_run(text)

def h2(text):
    p = doc.add_paragraph(style='Heading 2')
    p.add_run(text)

def h3(text):
    p = doc.add_paragraph(style='Heading 3')
    p.add_run(text)

def body(text):
    p = doc.add_paragraph(style='Normal')
    p.add_run(text)

def bullet(text):
    p = doc.add_paragraph(style='List Bullet')
    p.add_run(text)

def hr():
    doc.add_paragraph('---', style='Normal')

def table_2col(rows_data, header=True):
    """Two-column table matching W1 style."""
    t = doc.add_table(rows=len(rows_data), cols=2)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, (c0, c1) in enumerate(rows_data):
        row = t.rows[i]
        row.cells[0].text = c0
        row.cells[1].text = c1
        if i == 0 and header:
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.bold = True
    doc.add_paragraph()   # spacing after table

def table_3col(rows_data):
    """Three-column table matching W1 style."""
    t = doc.add_table(rows=len(rows_data), cols=3)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, (c0, c1, c2) in enumerate(rows_data):
        row = t.rows[i]
        row.cells[0].text = c0
        row.cells[1].text = c1
        row.cells[2].text = c2
        if i == 0:
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.bold = True
    doc.add_paragraph()

def table_4col(rows_data):
    """Four-column table."""
    t = doc.add_table(rows=len(rows_data), cols=4)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, row_data in enumerate(rows_data):
        row = t.rows[i]
        for j, val in enumerate(row_data):
            row.cells[j].text = val
            if i == 0:
                for para in row.cells[j].paragraphs:
                    for run in para.runs:
                        run.bold = True
    doc.add_paragraph()

def table_5col(rows_data):
    """Five-column table."""
    t = doc.add_table(rows=len(rows_data), cols=5)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, row_data in enumerate(rows_data):
        row = t.rows[i]
        for j, val in enumerate(row_data):
            row.cells[j].text = val
            if i == 0:
                for para in row.cells[j].paragraphs:
                    for run in para.runs:
                        run.bold = True
    doc.add_paragraph()

def table_6col(rows_data):
    """Six-column table (verify checklist)."""
    t = doc.add_table(rows=len(rows_data), cols=6)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, row_data in enumerate(rows_data):
        row = t.rows[i]
        for j, val in enumerate(row_data):
            row.cells[j].text = val
            if i == 0:
                for para in row.cells[j].paragraphs:
                    for run in para.runs:
                        run.bold = True
    doc.add_paragraph()

# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENT CONTENT
# ══════════════════════════════════════════════════════════════════════════════

# ── Title ─────────────────────────────────────────────────────────────────────
h1('Drone Video Analytics System (DVAS)')

p = doc.add_paragraph(style='Normal')
p.add_run('Weekly Progress Report')
p.add_run('12 June 2026 (Week 2 — Phase 1: Ingest Server Verification & Edge Integration)')

hr()

# ── Executive Summary ─────────────────────────────────────────────────────────
h2('Executive Summary')

bullet(
    'Ingest Server Verification (M3) — Adhish S Sujan & Nived M: '
    'This week focused on end-to-end functional verification of the Phase 1 ingest server stack '
    'implemented in Week 1. All 14 automated checks in scripts/verify.py passed (ALL CHECKS PASSED). '
    'The static verification confirms that the Redis Streams migration is consistent across '
    'main.py, models.py, queue_manager.py, config.py, and docker-compose.yml — with no residual '
    'RabbitMQ references in the codebase. The ingest API, authentication module, queue manager, '
    'and Docker Compose stack are confirmed correct and ready for live end-to-end testing.'
)

bullet(
    'Edge Sender & GPU Consumer Validation (M3/M4) — Adhish S Sujan & Nived M: '
    'The edge_sender.py client and gpu_consumer.py Redis Streams batch consumer were reviewed '
    'and validated for correctness. The edge sender implements auto-refresh of Keycloak JWT tokens '
    '30 seconds before expiry, exponential back-off retry on network failure, and loop/FPS mode '
    'for sustained load testing. The gpu_consumer.py DroneFrameConsumer was verified to handle '
    'XREAD incremental polling, BatchAccumulator flush logic, sentinel entry skipping, and '
    'confirmed-event publishing to drone:events:confirmed.'
)

bullet(
    'Models & API Contract Hardening — Adhish S Sujan: '
    'The FrameMetadata Pydantic model was extended this week with additional optional sensor '
    'fields (camera_id, heading_deg, speed_kmh) and strict field validators for drone_id '
    '(regex: ^[A-Z0-9_]{3,30}$) and timestamp (ISO 8601 format). '
    'The IngestResponse model now returns queue_name and message_id alongside status. '
    'The ReadinessResponse model correctly exposes redis and keycloak status fields '
    'without any residual rabbitmq_connected fields.'
)

# ── Section 1: Verification Results ──────────────────────────────────────────
h2('Section 1: Ingest Server Verification (M3) — Adhish S Sujan & Nived M')
h3('1.1 Automated Static Verification — scripts/verify.py')

body(
    'The automated verification script (scripts/verify.py) was executed against the full '
    'codebase this week. It runs 14 static checks across main.py, models.py, '
    'queue_manager.py, config.py, and ingest/docker-compose.yml to confirm the Redis Streams '
    'migration is complete and no RabbitMQ residue remains.'
)

table_2col([
    ('Check', 'Result'),
    ('rabbitmq_connected NOT in main.py',        'PASS'),
    ('redis_connected     IN main.py',            'PASS'),
    ('ReadinessResponse redis= in main',          'PASS'),
    ('redis_connected     IN models.py',          'PASS'),
    ('DroneQueueStats has no drone_id field',     'PASS'),
    ('DroneQueueStats has no messages_unacked',   'PASS'),
    ('redis image         IN compose',            'PASS'),
    ('REDIS_URL           IN compose',            'PASS'),
    ('rabbitmq service NOT IN compose',           'PASS'),
    ('REDIS_URL           IN config.py',          'PASS'),
    ('ALLOWED_ROLES       IN config.py',          'PASS'),
    ('last_frame_at ISO   IN main.py',            'PASS'),
    ('datetime imported   IN main.py',            'PASS'),
    ('redis.asyncio       IN queue_manager',      'PASS'),
    ('OVERALL RESULT',                            'ALL CHECKS PASSED ✓'),
])

h3('1.2 Code Review — Module-Level Findings')

body(
    'In addition to the automated checks, a full manual code review was conducted across all '
    'ingest API modules this week. Key findings and confirmations are documented below.'
)

table_3col([
    ('Module', 'Status', 'Key Details Confirmed This Week'),
    ('main.py (702 lines)',
     '✓ Verified',
     'Lifespan: connects Redis, declares 5 drone streams on startup. '
     '_record_frame() stores last_frame_at as ISO 8601 string via datetime.datetime.utcnow().strftime(). '
     'WS broadcast uses asyncio.create_task() — non-blocking. '
     'Global exception handler returns JSON 500 on unhandled errors. '
     'CORSMiddleware allows GET+POST with Authorization header.'),
    ('auth.py (147 lines)',
     '✓ Verified',
     'JWKS cache: 5-minute TTL via JWKS_CACHE_TTL_S (env-configurable). '
     'Stale cache served on Keycloak outage (jwks_using_stale_cache warning logged). '
     'ALLOWED_ROLES = {"drone-operator", "sys-admin"} — set intersection check. '
     'extract_username() returns preferred_username or sub fallback. '
     'validate_token_optional() returns None when no Bearer token is provided.'),
    ('queue_manager.py (231 lines)',
     '✓ Verified',
     'Uses redis.asyncio (aioredis); decode_responses=False for raw frame bytes. '
     'declare_drone_queue(): adds sentinel entry + deletes it to pre-create stream. '
     'publish_frame(): XADD with maxlen=QUEUE_MAX_LEN (~1000) and approximate=True. '
     'publish_event(): XADD to drone:events:confirmed (maxlen=500). '
     'is_healthy(): async ping check — used by /health/ready endpoint.'),
    ('models.py (151 lines)',
     '✓ Verified',
     'FrameMetadata: drone_id regex validator ^[A-Z0-9_]{3,30}$. '
     'timestamp validator: ISO 8601 format check. '
     'New optional fields: camera_id, heading_deg, speed_kmh. '
     'SystemStatusResponse.redis_connected (bool) — no rabbitmq_connected. '
     'DroneQueueStats: queue_name, messages_ready, consumers (no drone_id field). '
     'IngestResponse: now includes queue_name + message_id (Redis entry ID).'),
    ('config.py (59 lines)',
     '✓ Verified',
     'ALLOWED_ROLES: {"drone-operator", "sys-admin"} — matches auth.py check. '
     'REDIS_URL env var with default redis://redis:6379/0. '
     'KNOWN_DRONES: comma-separated env var; defaults to DRONE_KL_001 through 005. '
     'JWKS_CACHE_TTL_S: configurable via env (default 300 seconds). '
     'LOG_JSON: env-configurable structured logging flag.'),
    ('gpu_consumer.py (234 lines)',
     '✓ Verified (Stub)',
     'DroneFrameConsumer: XREAD with 500ms block across all drone:frames:* streams. '
     'BatchAccumulator: flushes at BATCH_SIZE=16 frames OR BATCH_TIMEOUT_S=2.0s. '
     'Sentinel _init entries skipped in the consumer loop. '
     'run_gpu_inference(): asyncio.sleep(0.01) stub — real inference pending Phase 3. '
     'Published results to drone:events:confirmed with maxlen=500.'),
])

h3('1.3 Docker Compose Stack Validation')

body(
    'The ingest/docker-compose.yml was reviewed for correctness of the 5-service stack. '
    'All services are confirmed to use health-check enforced startup ordering: '
    'postgres → keycloak → redis → api → nginx.'
)

table_3col([
    ('Service', 'Image / Build', 'Verified Configuration'),
    ('postgres',  'postgres:16-alpine',
     'POSTGRES_DB=keycloak. Health: pg_isready -U keycloak. Internal only (no external port).'),
    ('keycloak',  'keycloak:24.0',
     'start-dev mode. Depends on postgres (healthy). Port :8080. start_period=90s for first boot. '
     'KC_HTTP_ENABLED=true, KC_HOSTNAME_STRICT=false.'),
    ('redis',     'redis:7-alpine',
     'maxmemory=512mb, allkeys-lru eviction. appendonly=no (ephemeral). Port :6379 for debugging. '
     'Health: redis-cli ping.'),
    ('api',       'Built (./api/Dockerfile)',
     'REDIS_URL=redis://redis:6379/0. MAX_FRAME_MB=5. QUEUE_MAX_LENGTH=1000. '
     'Exposed on :8000 (internal). Health: curl /health.'),
    ('nginx',     'nginx:alpine',
     'Ports :80 (HTTP redirect) and :443 (HTTPS). TLS: self-signed cert from gen_certs.ps1. '
     'Rate limit: 30 r/s. WebSocket upgrade headers configured.'),
])

h3('1.4 API Contract — All Endpoints Confirmed')

table_3col([
    ('Endpoint', 'Method', 'Verified Behaviour'),
    ('POST /ingest/frame',       'POST',      'JWT required (drone-operator or sys-admin). JPEG/PNG only. ≤5 MB. Returns 202 + {status, drone_id, frame_sequence, queue_name, message_id}.'),
    ('GET /health',              'GET',        'No auth. Returns {status, version, uptime_s, components}. Docker HEALTHCHECK target.'),
    ('GET /health/ready',        'GET',        'No auth. Probes Redis (ping) + Keycloak (JWKS fetch). Returns 503 if either is down.'),
    ('GET /ingest/status',       'GET',        'JWT required. Returns aggregate: total_drones_tracked, total_frames_queued, total_frames_ingested, drones[], redis_connected.'),
    ('GET /ingest/status/{id}',  'GET',        'JWT required. Per-drone: queue (queue_name, messages_ready, consumers), ingest_stats, mission_id, online bool.'),
    ('POST /ingest/mission/start','POST',      'JWT required. Registers drone mission in _active_missions. Publishes mission_start event. Broadcasts to WS clients.'),
    ('POST /ingest/mission/end', 'POST',       'JWT required. Removes from _active_missions. Publishes mission_end event. Broadcasts to WS clients.'),
    ('GET /metrics',             'GET',        'No auth. Prometheus text/plain. 5 metric series: frames_received, bytes, latency histogram, queue_depth, active_missions.'),
    ('WS /ws/status',            'WebSocket',  'No auth (pending). Sends snapshot on connect. Broadcasts frame_queued, mission_start/end. 30s keepalive. Ping → pong handler.'),
])

h3('1.5 Current Status')

bullet(
    'scripts/verify.py: ALL 14 CHECKS PASSED — confirmed Redis migration complete across all modules.'
)
bullet(
    'main.py: Redis Streams queue manager fully integrated; datetime.datetime.utcnow() used for '
    'ISO 8601 last_frame_at timestamps; WebSocket broadcasts non-blocking via asyncio.create_task().'
)
bullet(
    'auth.py: JWKS cache TTL is env-configurable (JWKS_CACHE_TTL_S=300); stale-cache fallback '
    'prevents full outage on Keycloak restarts; validate_token_optional() available for semi-public endpoints.'
)
bullet(
    'models.py: FrameMetadata extended with camera_id, heading_deg, speed_kmh optional fields '
    'and strict validators; SystemStatusResponse uses redis_connected (bool) — no RabbitMQ residue.'
)
bullet(
    'gpu_consumer.py: BatchAccumulator correctly handles size (16) and timeout (2s) flush conditions; '
    'sentinel _init entries are skipped; confirmed-events published to drone:events:confirmed stream.'
)

h3('1.6 Next Steps — Ingest Server (Week of 13–19 June 2026)')

table_2col([
    ('Focus Area', 'Planned Tasks'),
    ('Live End-to-End Test',
     'Start full Docker stack (ingest/docker-compose.yml). '
     'Run edge_sender.py with a real JPEG frame from test_frame.jpg. '
     'Confirm HTTP 202 response, Redis XLEN > 0 in drone:frames:DRONE_KL_001, '
     'and gpu_consumer.py prints batch_processed log.'),
    ('Keycloak Auto-Config',
     'Run scripts/setup_keycloak.py against live stack. '
     'Verify realm kerala-police-drone, client drone-ingest-client, '
     'roles (drone-operator, control-room, supervisor, sys-admin), '
     'and 5 operator users (operator_001 – operator_005) are created.'),
    ('Load & Rate-Limit Test',
     'Run edge_sender.py --loop --fps 30 for 60 seconds. '
     'Confirm NGINX returns HTTP 429 when >30 r/s threshold is breached. '
     'Verify API does not crash under sustained load.'),
    ('WebSocket Client Test',
     'Connect a browser WebSocket client to wss://localhost/ws/status. '
     'Verify frame_queued events broadcast in real-time. '
     'Verify mission_start/end events after calling mission endpoints.'),
    ('Prometheus Metrics Validation',
     'Scrape GET /metrics under simulated load. '
     'Validate ingest_frames_received_total increments per drone. '
     'Validate ingest_frame_latency_seconds histogram distribution. '
     'Validate ingest_queue_depth gauge reflects Redis XLEN.'),
    ('WebSocket Auth (Pending)',
     'Add JWT validation to /ws/status — currently accepts unauthenticated connections. '
     'Required before field deployment per security policy.'),
    ('Redis Counter Persistence',
     'Persist _ingest_stats counters (frames_received, bytes_ingested) to Redis '
     'to survive API container restarts. Currently resets to zero on container restart.'),
])

# ── Section 2: Edge Sender & GPU Consumer ─────────────────────────────────────
h2('Section 2: Edge Sender & GPU Consumer Review — Adhish S Sujan & Nived M')
h3('2.1 Key Highlights This Week')

bullet(
    'scripts/verify.py: ALL 14 CHECKS PASSED — the complete static verification suite confirms '
    'Redis Streams migration is fully consistent across all 5 modules (main.py, models.py, '
    'queue_manager.py, config.py, docker-compose.yml).'
)
bullet(
    'edge_sender.py reviewed: JWT auto-refresh (30s pre-expiry buffer), exponential backoff retry '
    '(max 3 attempts, 1s base delay), --loop / --fps mode for sustained continuous sending, '
    'and --drone-id / --username / --password CLI arguments confirmed functional.'
)
bullet(
    'gpu_consumer.py reviewed: DroneFrameConsumer reads from 5 parallel drone:frames:* streams '
    'using XREAD (500ms block). BatchAccumulator correctly triggers flush at 16 frames OR 2s timeout. '
    'run_gpu_inference() stub returns structured results dict for each frame.'
)
bullet(
    'models.py hardened: FrameMetadata now validates drone_id with regex '
    '^[A-Z0-9_]{3,30}$ and timestamp with ISO 8601 pattern. '
    'Extra optional sensor fields (camera_id, heading_deg, speed_kmh) added for richer telemetry.'
)
bullet(
    'setup_keycloak.py reviewed: idempotent auto-configuration script handles existing realm/client/role/user '
    'gracefully (WARN-level skip). Prints client secret after setup for use in edge_sender.py.'
)

h3('2.2 Team Contribution')

table_5col([
    ('Team Member', 'Module / Area', 'Work Summary', 'Completed This Week', 'In Progress / Pending'),
    ('Adhish S Sujan',
     'Ingest Server Verification (M3)',
     'Led end-to-end static verification of the full ingest stack. '
     'Confirmed Redis migration consistency. Reviewed auth.py, config.py, '
     'docker-compose.yml, and models.py in detail.',
     'verify.py execution (ALL PASS). Code review of main.py, auth.py, config.py, models.py, '
     'docker-compose.yml. FrameMetadata validator review.',
     'Live Docker stack end-to-end test. Keycloak auto-config via setup_keycloak.py. '
     'WebSocket auth implementation. Redis counter persistence fix.'),
    ('Nived M',
     'GPU Consumer Validation (M4 Stub)',
     'Reviewed and validated gpu_consumer.py DroneFrameConsumer and BatchAccumulator. '
     'Reviewed setup_rabbitmq.py migration status. Planned Phase 3 GPU inference integration.',
     'gpu_consumer.py code review: XREAD loop, BatchAccumulator, sentinel skip, '
     'event publishing. verify.py review and results documentation.',
     'Live consumer test against real Docker Redis. Load testing with 5-drone parallel feed. '
     'Phase 3 GPU planning: Triton Inference Server evaluation.'),
])

h3('2.3 Verification Checklist — Expected Live Test Results')

body(
    'The following table documents the expected verification outcomes when the live Docker '
    'stack is tested in Week 3. These extend the Week 1 deliverables table with the '
    'additional endpoints and behaviours confirmed during this week\'s code review.'
)

table_6col([
    ('Test Case', 'Expected Result', 'HTTP Code', 'Flags', 'Priority', 'Notes'),
    ('POST /ingest/frame (valid JWT, JPEG ≤5MB)',
     'PASS', '202', '0', 'High',
     'Returns {status: queued, queue_name: drone:frames:DRONE_KL_001, message_id: <entry_id>}.'),
    ('POST /ingest/frame (no Authorization header)',
     'PASS', '401', '0', 'High',
     'NGINX pre-flight check + FastAPI HTTPBearer rejects missing token.'),
    ('POST /ingest/frame (wrong role — control-room)',
     'PASS', '403', '0', 'High',
     'Token valid but role not in ALLOWED_ROLES → 403 Forbidden.'),
    ('POST /ingest/frame (frame > 5 MB)',
     'PASS', '413', '0', 'High',
     'client_max_body_size=10M at NGINX; FastAPI size-check returns 413.'),
    ('POST /ingest/frame (non-JPEG MIME type)',
     'PASS', '415', '0', 'High',
     'ALLOWED_TYPES check in main.py rejects non-image content.'),
    ('POST /ingest/frame (invalid drone_id — lowercase)',
     'PASS', '422', '0', 'Medium',
     'FrameMetadata.validate_drone_id() regex fails → 422 Unprocessable.'),
    ('POST /ingest/frame (invalid timestamp format)',
     'PASS', '422', '0', 'Medium',
     'FrameMetadata.validate_timestamp() ISO 8601 check fails → 422 Unprocessable.'),
    ('GET /health',
     'PASS', '200', '0', 'High',
     'Returns {status: ok, version: 1.0.0, uptime_s: <float>, components: {api: {status: ok}}}.'),
    ('GET /health/ready (all services up)',
     'PASS', '200', '0', 'High',
     'Returns {ready: true, redis: connected, keycloak: reachable}.'),
    ('GET /health/ready (Redis container stopped)',
     'PASS', '503', '0', 'High',
     'Returns {ready: false, redis: disconnected, keycloak: reachable}.'),
    ('GET /ingest/status (valid JWT)',
     'PASS', '200', '0', 'Medium',
     'Returns aggregate status with redis_connected=true and 5 drone entries.'),
    ('GET /ingest/status/{id} (valid JWT)',
     'PASS', '200', '0', 'Medium',
     'Returns per-drone: queue_name, messages_ready (XLEN), ingest_stats, online=false initially.'),
    ('POST /ingest/mission/start (valid JWT)',
     'PASS', '201', '0', 'Medium',
     'Returns {status: registered, drone_id, mission_id, timestamp}. WS clients receive mission_start event.'),
    ('GET /metrics (no auth)',
     'PASS', '200', '0', 'Medium',
     'Prometheus text/plain. All 5 metric series present.'),
    ('WS /ws/status connect',
     'PASS', 'Connected', '0', 'Medium',
     'Snapshot event sent on connect. Keepalive every 30s. Ping → pong handler.'),
    ('Rate limit > 30 r/s via edge_sender --fps 30',
     'PASS', '429', '0', 'High',
     'NGINX rate limit zone=drone_limit:10m rate=30r/s triggers HTTP 429.'),
    ('gpu_consumer.py batch flush at 16 frames',
     'PASS', 'batch_processed log', '0', 'Medium',
     'BatchAccumulator triggers flush; run_gpu_inference stub runs; event published to confirmed stream.'),
    ('gpu_consumer.py timeout flush at 2s',
     'PASS', 'batch_processed log', '0', 'Medium',
     'If fewer than 16 frames arrive within 2s, BatchAccumulator deadline-based flush triggers.'),
])

h3('2.4 Ingest Server Technical Status (Updated)')

table_3col([
    ('Component', 'Status', 'Remarks'),
    ('FastAPI Ingest API (main.py)',
     '✓ Complete',
     'All 9 endpoints verified in code. CORSMiddleware, global exception handler in place. '
     'Non-blocking WS broadcast via asyncio.create_task().'),
    ('Keycloak JWT Auth (auth.py)',
     '✓ Complete',
     'RS256 JWKS cache (5-min TTL, env-configurable). Stale-cache fallback. '
     'ALLOWED_ROLES = {drone-operator, sys-admin}. extract_username() helper.'),
    ('Redis Streams (queue_manager.py)',
     '✓ Complete',
     'Per-drone streams: drone:frames:{id}. maxlen=1000, approximate=True. '
     'declare_drone_queue() pre-creates stream via sentinel on startup.'),
    ('Pydantic Models (models.py)',
     '✓ Complete',
     'FrameMetadata: regex validator for drone_id; ISO 8601 validator for timestamp. '
     'Extra optional fields: camera_id, heading_deg, speed_kmh. '
     'SystemStatusResponse.redis_connected (bool).'),
    ('Docker Compose Stack',
     '✓ Complete',
     '5 services (postgres → keycloak → redis → api → nginx). '
     'Redis 7-alpine replaces RabbitMQ. Health-check enforced startup ordering.'),
    ('GPU Consumer Stub (gpu_consumer.py)',
     '✓ Complete (Stub)',
     'BatchAccumulator (16 frames / 2s flush). XREAD incremental polling. '
     'Sentinel skip. Publishes to drone:events:confirmed. Real inference pending Phase 3.'),
    ('Prometheus Metrics',
     '✓ Complete',
     '5 metric series exposed at /metrics. Counter, Gauge, Histogram. Scrapeable without auth.'),
    ('WebSocket Broadcast',
     '✓ Complete',
     '_WSManager: async lock for connect/disconnect. asyncio.create_task() broadcast. '
     'Dead-connection cleanup. 30s keepalive. Ping-pong handler.'),
    ('setup_keycloak.py',
     '✓ Complete',
     'Idempotent: skips existing realm/client/role/user (409 / WARN). '
     'Creates 5 operator users with drone-operator role. Prints client secret.'),
    ('scripts/verify.py',
     '✓ Complete',
     '14 static checks across 5 modules. ALL CHECKS PASSED this week.'),
    ('In-Memory Ingest Stats',
     'In Progress',
     '_ingest_stats resets on API container restart. '
     'Need to persist frames_received + bytes_ingested counters in Redis for production.'),
    ('WebSocket Auth (/ws/status)',
     'Pending',
     '/ws/status currently accepts unauthenticated connections. '
     'JWT validation must be added before field deployment.'),
    ('TLS Certificate (CA-signed)',
     'Pending',
     'Currently using self-signed cert (gen_certs.ps1). '
     'CA-signed cert required for production field use.'),
    ('Live Docker Stack Test',
     'Pending (Week 3)',
     'Full end-to-end live test with edge_sender.py and real JPEG frame pending '
     'successful Docker stack startup in target environment.'),
    ('Real GPU Inference (M4)',
     'Pending — Phase 3',
     'run_gpu_inference() is an asyncio.sleep(0.01) stub. '
     'YOLOv8/YOLOv9 integration via Triton Inference Server scoped for Phase 3.'),
])

h3('2.5 Next Steps — GPU Analytics Consumer (13–19 June 2026)')

table_3col([
    ('Task Group', 'Planned Tasks', 'Priority'),
    ('Live End-to-End Smoke Test',
     'Start ingest/docker-compose.yml stack. Run scripts/setup_keycloak.py. '
     'Use edge_sender.py to send a real JPEG frame. '
     'Confirm 202 response, Redis entry, and gpu_consumer.py log output.',
     'High'),
    ('5-Drone Load Test',
     'Spawn 5 parallel edge_sender.py processes (one per drone ID) at 5 FPS each. '
     'Monitor NGINX rate-limit enforcement, API p95 latency, and Redis XLEN under load.',
     'High'),
    ('Prometheus Metrics Validation',
     'Scrape /metrics under 5-drone simulated load. '
     'Validate ingest_frames_received_total per drone, latency histogram, '
     'and ingest_queue_depth gauge matches Redis XLEN.',
     'Medium'),
    ('WebSocket Live Event Test',
     'Connect browser WebSocket client to wss://localhost/ws/status. '
     'Verify frame_queued events are broadcast on each successful ingest. '
     'Verify mission_start/end events after calling mission endpoints.',
     'Medium'),
    ('Redis Counter Persistence Fix',
     'Persist _ingest_stats (frames_received, frames_queued, bytes_ingested) '
     'to Redis hash per drone_id to survive API container restarts.',
     'Low'),
    ('WebSocket Auth Implementation',
     'Add JWT Bearer token validation to /ws/status on connection establishment. '
     'Reject unauthenticated WebSocket connections with 403.',
     'Low'),
    ('Phase 3 GPU Planning',
     'Evaluate NVIDIA Triton Inference Server vs. direct PyTorch serving. '
     'Document YOLOv8/YOLOv9 model input/output specs for gpu_consumer.py integration. '
     'Begin Phase 3 M4 planning document.',
     'Low'),
])

# ── Section 3: Overall Project Phase Roadmap ───────────────────────────────────
h2('Section 3: Overall Project Phase Roadmap')

body(
    'The table below reflects the combined implementation and verification status of all active '
    'project streams as of 12 June 2026.'
)

table_4col([
    ('Phase', 'Name', 'Key Deliverables', 'Status'),
    ('Phase 0',
     'Foundation & Environment Setup',
     'Repository, Docker, CI/CD, secrets management, networking, observability scaffold.',
     '✓ Complete'),
    ('Phase 1',
     'Central Ingest & Streaming Server (M3)',
     'FastAPI API (9 endpoints), Keycloak JWT auth, Redis Streams queue, NGINX TLS proxy, '
     'Docker Compose (5 services), edge sender, GPU consumer stub, Prometheus metrics. '
     'Verification: ALL 14 verify.py CHECKS PASSED.',
     '✓ Complete (Verification) — Live Test Pending'),
    ('Phase 2',
     'Scale Ingest to 20+ Drones',
     'Migrate Redis Streams → Apache Kafka 3.x. Replace REST ingest → gRPC. '
     'Horizontal FastAPI workers. Load-balanced NGINX upstream.',
     'Pending'),
    ('Phase 3',
     'GPU Analytics Cluster (M4)',
     'Triton Inference Server, YOLOv8/9 person/vehicle/vessel detection, '
     'ByteTrack tracking, crowd density, licence plate OCR, event confirmation engine.',
     'Pending'),
    ('Phase 4',
     'Evidence Object Storage (M5)',
     'MinIO WORM storage, evidence upload/retrieval API, hash verification, '
     'retention policy engine, legal hold API.',
     'Pending'),
    ('Phase 5',
     'Databases & Audit Systems (M6)',
     'PostgreSQL schema, TimescaleDB time-series, OpenSearch index, append-only audit log.',
     'Pending'),
    ('Phase 6',
     'Authentication & RBAC/ABAC (M7)',
     'Keycloak full RBAC + ABAC policies, scoped mission tokens, operator provisioning workflow.',
     'Pending'),
    ('Phase 7',
     'Dashboards (M8)',
     'Edge operator dashboard, ops monitoring dashboard, central command React dashboard with live map.',
     'Pending'),
    ('Phase 8',
     'Legal Compliance & Chain of Custody (M9)',
     'Chain-of-custody engine, court export, dual-auth deletion, compliance reporting.',
     'Pending'),
])

hr()

# ── Footer ─────────────────────────────────────────────────────────────────────
p = doc.add_paragraph(style='Normal')
p.add_run('Prepared By').bold = True

table_2col([
    ('Name', 'Role'),
    ('Adhish S Sujan', 'Software Engineer — Ingest Server (M3)'),
    ('Nived M',        'Software Engineer — GPU Consumer (M4 Stub)'),
    ('Organisation',   'Quantumweave Intelligence'),
    ('Report Period',  '06 June – 12 June 2026'),
    ('Report Week',    'Week 2 — Phase 1: Ingest Server Verification & Edge Integration'),
    ('Next Report',    'Week 3 — 19 June 2026 (Live End-to-End Testing)'),
])

# ── Save ───────────────────────────────────────────────────────────────────────
doc.save(OUTPUT)
print(f'✅  Saved: {OUTPUT}')
