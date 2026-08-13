# DVAS V1 — Two-Laptop First Version: Full Plan & Directory Structure

**Reality:** one Windows laptop (edge), one Ubuntu laptop (currently just ingest), no server, no cloud.
**Goal:** a genuinely working, demoable end-to-end system — drone frame → AI detection → live dashboard
alert → evidence clip — using only these two machines.

**This works.** It's not a scaled-down hack of the proposal architecture; it's a legitimate single-node
central deployment, which is precisely what the proposal's own Phase 1 pilot (§13.1) describes: a
controlled, limited-scale deployment before district/state scaling. The only real change from the
3-machine document I wrote earlier is: **the dashboard backend and frontend now also live on the
Ubuntu laptop**, alongside ingest and GPU analytics, instead of on a separate box. Nothing about the
proposal's logical layering changes — the layers just share one physical machine for now.

---

## 1. Machine role assignment for V1

| Machine | Role | Runs |
|---|---|---|
| **Windows laptop** | Edge only | Everything already in your `edge/` tree — unchanged |
| **Ubuntu laptop** | Everything else | Ingest API, Valkey, GPU consumer + 5 AI modules, Postgres, evidence storage, dashboard backend, dashboard frontend (built + served by NGINX) — all as Docker containers on one machine |

**Assumption to confirm:** the Ubuntu laptop has an NVIDIA GPU. If it's CPU-only, the AI module plan
still works, just slower — use `yolov8n` (nano) variants instead of `yolov8m`/larger for the demo, and
expect single-digit FPS rather than real-time. Either way the *architecture* below doesn't change,
only model size and expected throughput.

---

## 2. Networking for V1 — no server, no cloud, no problem

You don't need a public IP, a domain, or TLS certificates from a real CA for a first demo. Two options,
pick based on what's simplest given where the demo happens:

- **Option A (recommended, reuses what you've already validated):** Keep the WireGuard tunnel you
  already have working between the two laptops. The Windows laptop reaches the Ubuntu laptop at its
  WireGuard IP (e.g. `10.8.0.1`) exactly as it does today for the frame streaming you've already
  proven works. The dashboard is then reachable at `https://10.8.0.1:<port>` from **any device on that
  same VPN** — including the Windows laptop's own browser, or a phone/tablet you add to the VPN for
  the demo.
- **Option B (simplest possible, if WireGuard isn't available at the demo location):** Put both
  laptops on the same Wi-Fi/hotspot and use plain LAN IPs. Same architecture, just skip the VPN hop.
  Not what you'd use operationally, but fine for a quick proof-of-concept demo in a room.

Either way: **no public hosting is needed.** "Hosting" here just means "a process is running and
listening on a port on the Ubuntu laptop" — which Docker Compose already gives you.

---

## 3. What changes vs. the current state

Today: Windows edge laptop → WireGuard → NGINX → Keycloak → Ingest API → Valkey → `gpu_consumer.py`
(stub, no real models, no dashboard reading its output).

For V1, three things get built, in this order:

1. **Real AI modules wired into `gpu_consumer.py`** — this is the plan already written in
   `DVAS_AI_Dashboard_Integration_Plan.md` (`ModelRegistry`, `TrackerStore`, `FusionEngine`). No change
   to that plan — it already assumed everything runs on "the central server," which is now just this
   one Ubuntu laptop specifically.
2. **A dashboard backend container**, added to the same `docker-compose.yml` already running ingest —
   not a new machine, just a new service in the existing stack, reading `drone:events:confirmed` off
   the same Valkey instance and pushing to a WebSocket.
3. **A dashboard frontend**, built once (`npm run build`) and served as static files by the NGINX
   container that's already sitting in front of everything.

---

## 4. Build order for V1 (concrete steps)

**Phase A — Get one real detector talking to Redis (no dashboard yet)**
1. Add `analytics/` folder to the Ubuntu repo with `model_registry.py`, `tracker_store.py`, one module
   (`person_analyzer.py`, adapted from `person_face_crowd.py`'s `detect_on_tiles()`).
2. Replace `run_gpu_inference`'s stub body in `gpu_consumer.py` with a real call to the person module.
3. Verify with `redis-cli XREAD COUNT 5 STREAMS drone:events:confirmed 0` while a drone (or a looped
   test `.MP4` through `edge_sender.py`) is streaming — you should see real detection JSON, not empty
   `detections: []`.

**Phase B — Stand up the dashboard, still on the same laptop**
4. Add a `dashboard-backend/server.py` service to `docker-compose.yml` — `XREAD`s
   `drone:events:confirmed`, exposes a WebSocket.
5. Add a Postgres `confirmed_events` table + a small `db_writer.py` service doing the same `XREAD` →
   `INSERT`, so events aren't just live-streamed but also queryable/persisted.
6. `npm run build` the frontend, mount the build output into the existing NGINX container, add one
   `location /` block to serve it. Open a browser (from either laptop) to the Ubuntu laptop's VPN/LAN
   IP and confirm the page loads and the WebSocket connects.

**Phase C — Fill out the remaining AI modules**
7. Add `TrackerStore` so person tracks persist across batches (needed before vehicle/face modules make
   sense — they depend on stable track IDs).
8. Add `vehicle_analyzer.py`, then `face_analyzer.py` (gated on person tracks), then `plate_analyzer.py`
   (gated on vehicle tracks), then `crowd_analyzer.py` last (needs zone config — see open items).
9. Add the minimal `FusionEngine` so the dashboard shows *confirmed* events, not raw per-frame noise.

**Phase D — Close the loop**
10. Confirm `edge/scripts/extract_evidence_clip.py` is subscribed to `drone:events:confirmed` (scoped
    to its own drone ID) and actually extracts+uploads a clip on a confirmed event — this is what makes
    the demo end-to-end rather than just "detections appear in a log."

That's your first version: drone footage in, live confirmed alerts + evidence clip out, on two laptops,
no server.

---

## 5. Full directory structure for V1 (two machines only)

```
dvas/                                          ← single git repo, two deployment targets

├── edge/                                      ═══ DEPLOYS TO: Windows laptop ═══
│   ├── main.py
│   ├── analytics_worker.py
│   ├── config/
│   │   ├── config.py
│   │   ├── settings.yaml                      # ingest.url → Ubuntu laptop's WireGuard/LAN IP
│   │   └── wireguard/wg0-client.conf
│   ├── src/
│   │   ├── video/
│   │   │   ├── drone_video_source.py
│   │   │   ├── drone_simulation_source.py      # <-- use this for demos w/o a live drone
│   │   │   ├── frame_interpolator.py
│   │   │   └── stream_viewer.py
│   │   ├── buffer/rolling_frame_buffer.py
│   │   ├── change_detection/
│   │   │   ├── change_detection_engine.py
│   │   │   └── change_event.py
│   │   ├── cognitive_frame_selector/
│   │   │   ├── cognitive_frame_selector.py
│   │   │   └── selected_frame.py
│   │   ├── metrics/prometheus_metrics.py
│   │   └── utils/{gpu_utils.py, logger.py}
│   ├── ingest/edge_sender.py                   # posts frames to Ubuntu laptop
│   ├── scripts/
│   │   ├── extract_evidence_clip.py            # listens for confirmed events, own drone_id only
│   │   └── verify_pipeline.py
│   └── tests/
│
└── central/                                    ═══ DEPLOYS TO: Ubuntu laptop (ONLY machine besides edge) ═══
    ├── docker-compose.yml                      # one file, one machine, every service below
    ├── .env
    │
    ├── gateway/                                # already working today
    │   ├── nginx/default.conf                  # also serves dashboard-frontend build (Phase B, step 6)
    │   ├── keycloak/
    │   └── api/
    │       ├── main.py
    │       ├── auth.py
    │       ├── models.py
    │       └── queue_manager.py
    │
    ├── analytics/                              # NEW — this is the AI integration work
    │   ├── gpu_consumer.py                     # existing file, stub replaced per Phase A/C
    │   ├── model_registry.py                   # NEW
    │   ├── tracker_store.py                    # NEW
    │   ├── fusion_engine.py                    # NEW
    │   ├── modules/
    │   │   ├── base.py
    │   │   ├── person_analyzer.py              # from person_face_crowd.py
    │   │   ├── face_analyzer.py                # from person_face_crowd.py
    │   │   ├── vehicle_analyzer.py              # your trained weights + optimized_detection.py's attribute logic
    │   │   ├── plate_analyzer.py                # your trained weights
    │   │   └── crowd_analyzer.py                # from crowd_live_analysis.py
    │   ├── weights/                             # your saved .pt/.pth files, volume-mounted
    │   └── config/
    │       ├── confirmation_thresholds.yaml
    │       └── zone_config/                     # only needed once crowd zone analysis is in scope
    │
    ├── evidence/                                # NEW for V1 — even single-node needs somewhere to write clips
    │   └── docker-compose.evidence.yml          # SeaweedFS or plain MinIO single-node
    │
    ├── db/                                      # NEW for V1
    │   ├── schema/confirmed_events.sql
    │   ├── schema/entities_tracks.sql
    │   ├── schema/audit_log.sql
    │   └── db_writer.py
    │
    ├── dashboard-backend/                       # NEW — Phase B
    │   ├── server.py                            # XREAD drone:events:confirmed → WebSocket
    │   ├── database.py
    │   └── routes/
    │
    ├── dashboard-frontend/                      # NEW — Phase B, built once, served as static files
    │   ├── package.json
    │   └── src/
    │       ├── pages/CommandDashboard.js         # the one screen you actually need for a V1 demo
    │       ├── lib/{api.js, useDvasSocket.js}
    │       └── components/LiveMap.js
    │
    ├── housekeeping/                            # not real-time, run later/manually
    │   ├── gpu_deduplicate.py
    │   └── person_dedupe.py
    │
    └── monitoring/                              # you likely already have this from ingest work
        ├── prometheus.yml
        ├── grafana/
        └── rules/dvas_alerts.yml
```

Notice this is genuinely **two folders, two machines** — nothing here assumes a third box exists.
When you eventually do get real server/cloud hardware, `central/` is what migrates there wholesale;
nothing in `edge/` changes at all, since it only ever talks to "the ingest server's address," whatever
that address happens to be.

---

## 6. What's deliberately out of scope for V1

To keep this buildable on two laptops without stalling on infrastructure that doesn't matter for a
first demo:

- **Headscale / step-ca** (from `plan__2_.md`'s later phases) — plain WireGuard + Keycloak's existing
  JWT is enough for a demo; the more elaborate VPN control-plane and internal CA are for multi-drone,
  multi-operator scale, not a two-laptop pilot.
- **Zone-based crowd analytics** (§5.4) — needs per-mission zone config you likely don't have yet;
  V1 can report whole-frame crowd counts and add zoning once there's a real deployment site to define
  zones for.
- **Multi-GPU / Triton batching across nodes** — irrelevant with one GPU on one laptop; the async
  batch accumulator already in `gpu_consumer.py` is enough.

---

## 7. Open items before/while building

- Confirm GPU presence + VRAM on the Ubuntu laptop — determines model sizes for the demo.
- Confirm rough disk headroom on the Ubuntu laptop for evidence storage + Postgres + Docker images —
  single-laptop deployments run out of disk faster than you'd expect once video clips start
  accumulating.
- Decide Option A vs B networking (WireGuard vs. plain LAN) based on where the actual demo will happen.
