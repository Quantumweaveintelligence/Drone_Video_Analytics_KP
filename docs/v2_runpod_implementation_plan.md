# DVAS RunPod v2 — Implementation plan (cloud, branch `runpod-v1`)

*Version 2, 2026-11-10 (adds R-F11–R-F13 queueing findings, the disk-backed analytics feed, the
decisions below, and the delivery stages in §0). Version 1, 2026-11-09. Owner: RunPod developer. Companion document: `v2_edge_implementation_plan.md`
(edge developer). §3 (the uplink contract) is identical in both documents; change it in both or in
neither. This plan builds on `analytics_pipeline_improvement_plan.md` (Phases 1, 2a and 2 are done;
Phase 3 items are folded into R4 below). Its §3 evidence rules and §4 accuracy guard apply here
unchanged.*

**Goal of v2:**
- **Live view:** every drone's feed plays in the dashboard at the source rate (25–30 fps), through
  the RunPod HTTP proxy.
- **Analytics:** every enabled service runs at **≥ 20 fps per drone**.
- **Overlays:** boxes, labels and geofences are drawn in the browser, not burned into JPEGs on the
  server.

This is not a full overhaul. The work is limited to:

1. continuous video: WebSocket uplink from the edge, then WS-fMP4 (MSE) to the browser through
   go2rtc;
2. removing the HTTP JPEG polling and the server-side draw → re-encode for live viewing;
3. the redundant YOLO inference and the CPU thread contention;
4. the other changes without which 20 fps is not reachable or evidence would suffer.

## 0. Decisions (2026-11-10) and delivery

| Topic | Decision |
|---|---|
| Analytics falling behind the stream | **Superseded 2026-11-11 for overload (R6):** normal load — every frame for person / face, ANPR, intrusion, anomaly; latest frame for crowd and vessel; over 1.5 s behind — shed (drop-oldest, marked gaps, cadence step-down). The recording keeps every frame. *(Was: no loss, disk-backed; the video-gateway feeds analytics from the recording and keeps a small window in Valkey — still how the feed works under normal load.)* |
| Analytics resolution | **Source resolution.** No downscale (`DVAS_ANALYTICS_MAX_WIDTH=0`). 4K stays 4K for face tiles and plates. Person at 4K reaches 20 fps only after R4. |
| Recording | **On, kept 7 days** (`DVAS_RECORD_RETENTION_DAYS=7`), except segments linked to legal-hold evidence. Stops with an alert at 90 % of the volume. |
| Delivery | **Stages, each pushed to branch `runpod-v2` and checked on the pod before the next:** Stage 1 = R0 spike script + R1 + R2; Stage 2 = R3; Stage 3 = R4 + R5. **Reordered after the Stage 1 pod check: Stage 3 (speed) before Stage 2 (overlays)** — overlays on analytics 20 s+ behind live are of little use. |

### Stage 1 on the pod (2026-11-10, RTX PRO 6000 Blackwell)

| Check | Result |
|---|---|
| Uplink (`send_ts`, 4K H.264 at 8 Mbps, 60 s) | 30 fps received, no disconnects |
| No-loss queue | 400 / 400 frames analysed; when analytics lagged, the decoder slowed to match and the backlog waited on disk |
| Disk guard | Works; it first refused video because MooseFS `du` counts allocated blocks (165 GB for ~16 GB of MinIO data). Volume wiped and set to 100 GB |
| Bitrate cap | Works: an uncapped test clip (~25 Mbps) was cut with 4413; the test clip is now capped at 8 Mbps |
| Live video in the browser | **Bug, fixed in `bf57b82`**: the backend's service token has no DVAS role, so `/ingest/video/live` answered 403 and tiles fell back to JPEG. Re-check on the pod pending |
| Analytics at 4K | **8–10 fps steady**, lag grew to 26 s per minute of flight. `dvas-ctl lag`: only `person-group` had pending frames — person + 28 face tiles per 4K frame sets the pace for every service |
| CPU decode 4K / nvJPEG 4K | 146–164 fps / 0.3–0.7 ms — not a bottleneck |

Follow-ups: make the disk guard measure apparent size (not MooseFS blocks).

### Status (2026-11-10): Stage 1 implemented on `runpod-v2`, not yet on the pod

Built:
- **R1** — `/ingest/video/ws` (`dvas_ingest/ingest/api/main.py`).
- **R1** — recording and SHA-256 chain manifest (`video_store.py`).
- **R1** — live relay (`video_relay.py`).
- **R1** — `video-decoder` process (`video_decoder.py`, runs in the ai venv: no new Python packages).
- **R1** — ack-after-analysis in the sampler (and `done()` at each analyzer's stream stop).
- **R1** — video-aware `is_live`.
- **R1** — the drain waits for `decoder_done`.
- **R1** — disk guard file.
- **R1** — `dvas-ctl lag` video lines.
- **R2** — go2rtc v1.9.14 (SHA-256 pinned) with 16 slots, `/live/api/ws` behind `auth_request`.
- **R2** — `routers/live.py`.
- **R2** — `useLiveVideo` on the Multi-Drone Grid and Tactical Map tiles.
- **Tools** — `tools/send_ts.py` (edge stand-in) and `tools/v2_pod_check.py` (R0 measurements on the pod).

Tests:
- ingest: 65, incl. 12 video tests — recording, flow control, restart resume, reconnect, B-frames,
  drain gating, relay slot, disk guard;
- analytics shared: 63, incl. 5 ack tests;
- backend: `test_live_video.py`;
- frontend: builds.

Differences from the plan text above, found while building:
- the edge's `seq` follows decode order with B-frames, so the decoder orders by PTS and numbers
  frames itself (`edge_seq` kept);
- go2rtc needs pre-declared stream slots, so the R2 tickets were replaced by session-cookie
  auth plus the slot's current drone;
- `df /workspace` shows the RunPod storage cluster, so recordings are refused using the backup
  loop's du-based disk guard (`DVAS_VOLUME_GB`).

Not verified (needs the pod):
- go2rtc's handling of the empty slot entries in its config;
- MSE playback through Cloudflare;
- latency from Kerala;
- decode / JPEG speed at 4K;
- the 30 s player idle timeout in practice.

`v2_pod_check.py` covers these.

Out of scope for v2:
- a single multi-head backbone (needs retraining);
- GB/T 28181;
- MQTT gimbal control;
- VLM search / pgvector;
- 3D GIS FOV footprints;
- the edge code (the edge team owns it; see the companion plan).

---

## 1. What the platform does today (verified on `runpod-v1` @ `bd66902`)

| # | Finding | Where | Effect |
|---|---|---|---|
| R-F1 | **Frames arrive as one HTTPS POST per JPEG.** The edge allows 5 in flight; a POST round trip from Kerala to the pod is 350–750 ms. | edge `edge_api_server.py:460`; `dvas_ingest/ingest/api/main.py` `ingest_frame` | Ceiling ≈ **7–14 frames/s reach the pod**. The analyzers can never see 20 fps from Kerala on this path. |
| R-F2 | **The edge stamps `frame_seq` / `capture_ts` at send time, not at capture.** Retried frames look newer than they are. | edge `ingest/edge_sender.py:365, :386` | The Phase 1 re-order window cannot repair the order. Fixed on the edge side (edge plan E1). The video uplink removes this path altogether. |
| R-F3 | **Multi-Drone Grid and Tactical Map tiles poll raw JPEGs** every 700–800 ms. The analytics pages poll raw feeds every 1.5 s, and crowd polls its annotated view. | `MultiDroneView.js:236`, `TacticalMapPage.js:27`, `CrowdAnalytics.js:222-228`, raw feeds in Traffic / Person / Vessel | About 1 fps on the command views. A full-resolution JPEG per poll goes through the proxy. |
| R-F4 | **Annotated views are MJPEG streams** (`shared/annotated_hub.py`, `useLiveFeed.js`). They are smooth, but every analyzer draws on the frame, resizes and JPEG-encodes **every processed frame, whether or not anyone is watching.** | `AnnotatedHub.put_frame`; `cv2.imencode` in person / vehicle / ANPR / vessel / anomaly `main.py` | About 10–15 ms of CPU per frame per service. Overlays are fixed pixels: they cannot be toggled, hovered or clicked, and they cannot be combined across services in one view. |
| R-F5 | **Each enabled service JPEG-decodes the same frame** (4K: 31.8 ms on the pod). The decode is in a prefetch thread since Phase 2, but the CPU is still spent N times. | `shared/frame_sampler.py` `decode_frame` / `next_decoded` | With 5–6 services at 20 fps per drone, that is several CPU cores of the 27-vCPU quota spent on decoding. |
| R-F6 | **Vehicle and ANPR run the identical detector** (`visdrone.pt`) on the identical frames, each with its own tracker. | `vehicle/main.py:57`, `anpr/main.py:60` | One full YOLO pass plus one tracker per frame is pure duplication. |
| R-F7 | **The other detectors are different models:** person `yolov8m`; intrusion `geotrax_hbb_yolov8s_1920`; vessel `yolov8s` (SeaDrone); anomaly `visdrone-yolov11x` + `yolo11n`. | `*/main.py` model paths | The blueprint's "6× the same backbone" is true only for vehicle/ANPR. The others need retraining to share a backbone, which is out of scope. |
| R-F8 | **Person at 4K needs ≈ 85 ms per frame** (bench on the L40S: decode 31.8 ms, predict 14, predict+track 39, face tiles 46.5). The GPU and CPU stages run one after another. | `tools/bench_person.py` output, 2026-11-07 | About 12 fps at 4K. This is the service furthest from 20 fps. |
| R-F9 | **Thread budgets are uniform:** `OMP_NUM_THREADS = DVAS_CPUS / 8` for every process, whatever its load. | `deploy/runpod/bin/dvas-init.sh:79-89` | Light services hold threads that heavy ones (person, ANPR OCR, anomaly) need. Oversubscription shows up when all of them run. |
| R-F10 | **No continuous recording of the source video.** Evidence is the JPEG frames the edge chose to send. | — | When analytics move to server-decoded frames (R1), the original stream must be kept, for Section 65B. |
| R-F11 | **Unread frames are trimmed when analytics lag.** Every `XADD` uses `MAXLEN ~ QUEUE_MAX_LENGTH` (2000), or a quarter of it while Valkey memory is above 85 %. Trimming ignores consumer groups, so a service more than ≈ 100 s behind at 20 fps (≈ 25 s when memory is tight) loses the oldest frames it has not read. They are never analysed; the sampler only counts them as `skipped` from `frame_seq` gaps. The GPU governor makes this likely: under GPU pressure it caps services down to 5 fps while input stays at 20. | `ingest/api/queue_manager.py` `publish_frame` (`maxlen`), `_memory_tight`; `shared/frame_sampler.py` header; `gateway/governor.py` | Silent frame loss under load. This contradicts "every frame is analysed" (evidence rule 1) exactly when the system is busiest. |
| R-F12 | **Frames are acknowledged when read, not when analysed.** `_next_ordered` XACKs entries as soon as they arrive. They then wait in the in-memory re-order buffer (up to 16 per drone) and the decode prefetch. | `shared/frame_sampler.py` `_next_ordered` | If a service restarts or crashes, those frames are no longer pending, so they are never re-read: they are lost. The "re-read own pending list" path on start never sees them. |
| R-F13 | **Valkey holds the whole backlog as JPEGs.** 2000 entries × 1–1.5 MB at 4K ≈ 2–3 GB per drone, against the 8 GB cap. | `etc/valkey.conf`, `QUEUE_MAX_LENGTH` | Two or three 4K drones with a lagging service push Valkey into the memory guard, which shortens the queue (R-F11) for every drone. |

The pod's network has no UDP and only port 80 is exposed over HTTP (Cloudflare limit: 100 s idle).
So the transport is **WebSocket over port 80** in both directions. WebRTC is offered only for LAN or
on-prem installs (R2.6).

Items from the blueprint that are **already done**:
- `DELETE /api/evidence/{id}` requires `senior_officer` and respects legal hold (`routers/alerts.py:507`).
- Frames are stored as raw bytes in Valkey; `jpeg_b64` is written only with `QUEUE_WRITE_JPEG_B64`.
- Analytics pages already stream MJPEG rather than polling (`useLiveFeed`).
- The edge `EDGE_API_KEY` fallback has been removed.

---

## 2. Target design

```
edge ──wss /ingest/video/ws (H.264 MPEG-TS + per-frame meta, §3)──►  video-gateway (new, dvas_ingest)
                                                                       │  ├─ TS bytes ─► go2rtc (RTSP publish, 127.0.0.1) ─► browsers: wss /live/api/ws (MSE, 25–30 fps)
                                                                       │  └─ TS bytes + meta ─► recorder: 10 s segments + meta.jsonl on /workspace, SHA-256 chain (evidence)
                                                                       │                          │
                                                                       │        decoder tails the recording (PyAV, CPU or NVDEC), at the pace analytics consume:
                                                                       │        sample to DVAS_ANALYTICS_FPS (20) ─► JPEG ─► XADD mission:frames:{sid}
                                                                       │        (same fields as today + telemetry from meta.jsonl), only while the
                                                                       │        stream's unconsumed window < DVAS_VIDEO_WINDOW_FRAMES (≈ 5 s)
analyzers (unchanged inputs) ── per-frame results ──► XADD mission:hud:{sid} ──► backend WS /api/hud/ws ──► <HudCanvas> over <LiveVideo>
vehicle ── detections (visdrone) ──► mission:hud:{sid} ──► ANPR reads them instead of running visdrone again
```

- **Analyzers keep their input contract.** They still read `mission:frames:{sid}`, ordered, with the
  same fields. Sampler, ordering, persist gates, evidence rules and Phase 2a stream keys are all
  untouched. The only difference is where frames come from: the video-gateway instead of
  `/ingest/frame`.
- **`/ingest/frame` stays**, for older edges and the edge's JPEG fallback.
- **The video-gateway** writes the same frame entries the JPEG path writes. Downstream nothing
  knows which transport delivered a frame, except a `source=h264` field.
- **Backlog lives on disk, not in Valkey (decision §0).**
  - Live video and recording run at the edge's pace.
  - The analytics feed runs at the pace of the slowest enabled service: the decoder pauses while
    the Valkey window is full and continues from the recording when there is room.
  - A lag of minutes costs disk (already written for evidence), never frames. Valkey holds ≈ 5 s per
    mission (≈ 150 MB at 4K), so the memory guard (R-F11, R-F13) is no longer reached by video
    missions.
  - How far behind live each service is shows on the Models page and on the drone's tile.
  - Overlays from a service that is more than 2 s behind are not drawn on the live video (R3); the
    tile says "Person 40 s behind live".

---

## 3. Uplink contract v1 (shared with the edge plan — keep identical)

**Endpoint:** `wss://<host>/ingest/video/ws?drone_id=<id>&mission_id=<id>`
- RunPod example: `wss://<pod>-80.proxy.runpod.net/ingest/video/ws`. On a LAN install, plain `ws://`.
- The `Authorization: Bearer <operator token>` header carries the same token used for `/ingest/frame` today.
- Prerequisite: `POST /ingest/mission/start` has succeeded, as today. The server applies the Phase 2a
  rule: only the operator who started the mission may stream to it, and the mission must be `live`.

**Edge → server, first message (text):**
```json
{"type":"hello","v":1,"drone_id":"DRN-012","mission_id":"MSN-…","laptop_id":"…",
 "codec":"h264","container":"mpegts","width":1920,"height":1080,"fps":29.97,
 "source":"rtsp|file|usb","next_seq":1}
```
- `next_seq` is the sequence number of the first frame this connection will carry.
- After a reconnect it continues the mission's numbering; it never restarts at 1 within a mission.

**Server → edge, reply (text):**
- On success:
  ```json
  {"type":"ready","v":1,"analytics_fps":20,"max_kbps":12000,"last_seq":0}
  ```
  `last_seq` is the highest sequence the server already has for this mission (used on resume).
- On refusal:
  ```json
  {"type":"error","code":"…","detail":"…"}
  ```
  followed by a close with the code from the table below.

**Edge → server, during the stream:**
- **Binary messages:** MPEG-TS bytes (a multiple of 188 bytes, ≤ 256 KiB per message), in order.
  - Video PTS must be monotonic within a connection.
  - Every connection starts with a keyframe preceded by SPS/PPS.
- **Text `meta`:** one per video frame, sent *before* the binary chunk that carries that frame.
  - Messages may be batched as `{"type":"meta_batch","items":[…]}`, at most 10 per batch and at most 200 ms old.
  ```json
  {"type":"meta","seq":1234,"pts_ms":41233.4,"capture_ts":1762671000.123,"key":false,
   "change_score":0.12,
   "tel":{"latitude":8.52,"longitude":76.93,"altitude_m":85.0,"abs_alt":120.1,"speed_mps":4.2,
          "battery_pct":71,"gb_yaw":142.5,"gb_pitch":-35.0,"gb_roll":0.0,"focal_len":24.0,
          "dzoom_ratio":1.0}}
  ```
  - `seq`: per-mission frame counter **assigned at capture**. It may follow packet (decode)
    order when the source has B-frames. It never repeats for different frames, and continues
    across reconnects. The server orders frames by PTS and numbers the analytics frames itself;
    `seq` is kept as `edge_seq` and used to drop frames re-sent after a reconnect.
  - `pts_ms`: the PTS of that frame in the TS stream, in milliseconds.
  - `capture_ts`: wall clock, epoch seconds, **taken at capture**.
  - `tel`: the same field names as today's `/ingest/frame` metadata; missing values are `null`.
- **Text `bye`:** `{"type":"bye","last_seq":N}` before `POST /ingest/mission/end`.
- **Pings:** WebSocket ping every 15 s (the RunPod/Cloudflare path drops connections idle for 100 s).

**Server → edge, during the stream (text, about every 2 s):**
```json
{"type":"stats","rx_fps":29.9,"rx_kbps":5200,"decoded_fps":29.9,"analytics_fps":20.0,
 "analytics_lag_frames":3,"last_seq":1234}
```
The server may also send a congestion hint, `{"type":"slow_down","max_kbps":4000}`.

**Codec rules:**
- H.264 (AVC), Baseline/Main/High, up to 3840×2160, up to 60 fps.
- Keyframe interval ≤ 2 s (1 s recommended).
- When transcoding: no B-frames, `-tune zerolatency`.
- HEVC is not accepted by the server: the edge transcodes it to H.264.

**Close codes:**

| Code | Meaning | Edge action |
|---|---|---|
| `1000` | Normal close | — |
| `4401` | Token invalid or expired | Refresh the token, reconnect |
| `4403` | Not the mission owner | Stop and show an error |
| `4409` | Mission not live | Call `mission/start`, or stop |
| `4413` | Over `max_kbps` | Transcode lower, reconnect |
| `4415` | Codec not supported | Transcode, reconnect |
| `1011` | Server error | Back off 0.5 → 1 → 2 → 4 → 8 s, then reconnect |
| `1013` | Server busy: recording disk full, too many live streams, previous connection still closing | Wait 30 s, reconnect; keep the JPEG fallback meanwhile |
| `1003` / `1008` / `1009` | Malformed message, bad hello, chunk over 256 KiB or not whole TS packets | Fix the sender (bug); do not retry in a loop |

If the upgrade is refused (HTTP 403/404: an old server, or `DVAS_VIDEO_INGEST=0`) or fails 5 times in a row, switch to the JPEG
fallback and keep retrying the WebSocket every 60 s.

---

## 4. Phases

Each phase is one image release:
1. `build_image.sh`
2. `push_ghcr.sh`
3. set the image **on the pod**
4. restart
5. add a row to `RELEASES.md`

Every behaviour sits behind an env flag, so it can be switched off on the pod without a rebuild.

### Phase R0 — Spike on the pod (2 days, no production change)

Answer these questions before any design detail is fixed. Write the answers here.

1. **go2rtc through the RunPod proxy.**
   - Run go2rtc (pinned release, SHA-256 verified) on `127.0.0.1:1984` / `:8554`.
   - Publish a test TS with `ffmpeg -re -i test.ts -c copy -f rtsp rtsp://127.0.0.1:8554/t`.
   - Proxy `/live/` through nginx and play `wss://<pod>-80.proxy.runpod.net/live/api/ws?src=t` in
     Chrome, Edge and Firefox.
   - Measure glass-to-glass latency with a burnt-in clock, from Kerala.
   - Check whether the fMP4 `baseMediaDecodeTime` keeps the source PTS. This decides the HUD sync
     method in R3.
2. **Decode in the image.**
   - Is NVDEC usable? Check `ffmpeg -hwaccels`, `ls /usr/lib/x86_64-linux-gnu/libnvcuvid*`, and
     `NVIDIA_DRIVER_CAPABILITIES` (it needs `video`).
   - Does the PyAV wheel decode H.264 with threads?
   - CPU cost per 1080p30 / 4K30 stream for CPU decode (H.264 decode costs far less than JPEG
     decode; NVDEC is optional).
3. **JPEG encode for the analytics frames:** `cv2.imencode` at 1920 and 3840 px, compared with
   nvJPEG (`torchvision.io.encode_jpeg` on CUDA).
4. **Size of `/dev/shm`** on the pod: `df -h /dev/shm`. This is only needed for the optional R5.4.
5. **WebSocket uplink through Cloudflare** with a 10 Mbps TS for 30 minutes: does it disconnect, and
   what is the throughput ceiling?

**Done when:** each answer is recorded with numbers, and the HUD sync method and decode path are
chosen.

### Phase R1 — Video ingest: video-gateway, go2rtc, recording (6–8 days)

New service `dvas_ingest/video_gateway/` (FastAPI + asyncio). It is a supervisord program
`video-gateway` on `127.0.0.1:8092`. It reuses `ingest/api/queue_manager.py` (Phase 2a registry,
`publish_frame`) and `ingest/api/auth.py`.

1. **WebSocket endpoint** `/ingest/video/ws` (§3).
   - **Auth:** the same token validation as `/ingest/frame`, and the Phase 2a ownership rule (the
     mission must be `live` and started by this `sub`).
   - **Limits:** one connection per mission (a newer one replaces the older one, with close code
     1000); `DVAS_VIDEO_MAX_KBPS` (default 12000) → 4413; `DVAS_MAX_LIVE_STREAMS` (default 8) →
     close 1013.
   - Validate `drone_id` and `mission_id` with the same pattern as ingest (D7).
2. **Relay to go2rtc.**
   - Per mission, one `ffmpeg -f mpegts -i pipe:0 -c copy -f rtsp rtsp://127.0.0.1:8554/{sid}`
     subprocess.
   - It restarts on exit. Its stderr goes to `/var/log/dvas/video-gateway.log`, rate-limited.
   - go2rtc stream name = Phase 2a `sid`.
3. **Recorder** (`DVAS_RECORD_VIDEO=1`; it is also the analytics feed, §2).
   - The TS bytes are written as-is into segments of about 10 s, rotated at the next keyframe:
     `/workspace/dvas/recordings/{sid}/seg-000001.ts`, `seg-000002.ts`, …
   - Each segment has a `seg-NNNNNN.meta.jsonl`: every `meta` message as received, plus `rx_ts`
     (server clock).
   - When a segment closes, a line goes to `manifest.jsonl`: `sha256`, the previous line's hash
     (a chain), `seq` range, `capture_ts` range, bytes.
   - Progress is kept in Valkey `mission:video:{sid}` (hash): `status` (receiving | ended),
     `segments`, `last_seq_rx`.
   - At mission end the manifest's last hash is written to the audit log as a
     `video_recording_sealed` event, through the existing backend audit API.
   - **Retention:** `DVAS_RECORD_RETENTION_DAYS=7`. Purge runs in `backup-loop.sh`. It skips:
     - any mission whose analytics have not finished (`decoder_done` not set);
     - anything linked to evidence under legal hold.
   - **Disk:** at 90 % of `DVAS_VOLUME_GB`, new missions are refused with close 1013 and an
     alert; running missions continue.
   - **Why it is critical:** analytics frames are now decoded on the server, so the original stream
     is what proves them (65B). It is also the backlog store for the no-loss policy.
4. **Decoder: the recording → analytics frames, at the analytics' pace.**
   - **Input.** One decoder thread per mission reads the segments in order through a tailing
     reader:
     - at the end of the newest segment it waits while `status=receiving`;
     - EOF comes after the last segment once `status=ended`.
     - `av.open(reader, format="mpegts")` on CPU, or NVDEC if R0 chose it.
   - **Decoding and sampling.** Every frame is decoded (P-frames depend on earlier ones). Sampling
     happens after decode: keep a frame when `floor(t × DVAS_ANALYTICS_FPS)` advances. Default 20;
     the source rate if lower.
   - **Meta join.** Meta comes from the segment's `meta.jsonl`, joined by `pts_ms` (± half a frame;
     fall back to `seq` order).
     - Fields from meta: `frame_sequence` / `frame_seq` = meta `seq`, `capture_ts`, the telemetry
       fields, `change_score`.
     - Added fields: `source="h264"`, `rx_ts_ms` (when the server received the frame).
   - **Resolution: source** (`DVAS_ANALYTICS_MAX_WIDTH=0`; a value > 0 caps the width). JPEG q90 via
     `cv2.imencode` (nvJPEG if it wins in R0). The frame is XADDed through
     `QueueManager.publish_frame`: same stream key, same fields.
   - **Flow control (no loss).**
     - Before each XADD, the decoder reads the stream's unconsumed frames: for each *active*
       consumer group (the same rule as the drain check, `QUEUE_READER_IDLE_S`), `lag + pending`.
     - At ≥ `DVAS_VIDEO_WINDOW_FRAMES` (default 100 ≈ 5 s at 20 fps) it waits (50 ms polls). The
       recording keeps growing; nothing is dropped.
     - The stream's `MAXLEN` for video missions is 4 × the window. It is a safety bound only and is
       never reached while flow control works.
     - With no active group (no service enabled), the decoder keeps live pace and the window is
       trimmed, as today: a service enabled later starts at the current window.
   - **Restart-safe.**
     - Every 20 published frames the decoder stores `decoder_seg` and `decoder_seq` in
       `mission:video:{sid}`.
     - After a restart it reopens `decoder_seg` (segments start at a keyframe) and skips frames with
       `seq ≤ decoder_seq`.
   - **End of mission.**
     - When the decoder reaches EOF it sets `decoder_done=1`.
     - The ingest drain check (`is_fully_consumed`) additionally requires `decoder_done` for a
       mission that has a video record. A mission stays `draining` until its analytics have seen
       every frame. Its stream is not purged early.
   - **Live position.** The decoder publishes `behind_live_s` (`rx_ts` of the newest received frame
     minus `rx_ts` of the last published one) for `stats`, the Models page and `dvas-ctl lag`.
4a. **Queue integrity fixes** (all transports; R-F11, R-F12).
   - **Acknowledge after analysis.** The sampler XACKs a frame when the service asks for the next
     round. By then the service has finished it, because loops are sequential per drone; the
     prefetch path never acknowledges.
     - Frames in the re-order buffer and the prefetch stay pending, so a restart re-reads them (at
       most one duplicate frame per drone after a crash: at-least-once).
     - Test: kill a service mid-stream and restart it → no `seq` missing in its results.
   - **Lag measured on the server clock.** `lag_ms` uses `rx_ts_ms` when present (video). For the
     JPEG path it uses `ts` as today, so a backlog read from disk is not reported as "live".
   - **Legacy JPEG path, overflow made visible.**
     - When a group's unread count passes 80 % of `MAXLEN`, ingest logs a warning and publishes a
       `queue_overflow_risk` event. The Models page shows it.
     - Frames trimmed unread are counted per mission as **Dropped (queue full)**, separate from
       `skipped`. This path cannot be made lossless without a disk store; the video path is the
       fix.
   - **Governor.** With the video path, a lower cap only adds delay. The status shows the resulting
     `behind_live_s` per service, so the operator sees the cost of the cap.
5. **`stats` to the edge** every 2 s (§3). The values come from the gateway's counters and the
   sampler snapshot (analytics fps, lag). `analytics_lag_frames` is the unconsumed window plus the
   frames not yet decoded from disk.
6. **nginx.**
   - `location /ingest/video/ws`: proxy to `video-gateway`, `proxy_http_version 1.1`,
     Upgrade/Connection headers, `proxy_read_timeout 3600s`, `proxy_buffering off`,
     `client_body_buffer_size` small. No per-request rate limit (it is one long connection); use a
     `limit_conn` per token of 2.
   - `location /live/`: go2rtc, described in R2.
7. **Image changes.**
   - go2rtc binary (pinned version + SHA-256), `/etc/dvas/go2rtc.yaml`.
   - go2rtc settings: API on `127.0.0.1:1984` only; the RTSP listener on `127.0.0.1:8554` only; no
     `exec`/`echo` sources defined; WebRTC disabled on RunPod.
   - Add `av` to `requirements/ai.txt`, or to the ingest venv.
   - supervisord programs `go2rtc` and `video-gateway`.
   - `dvas-ctl lag` shows the video streams.
8. **Tests:**
   - `dvas_ingest/tests/test_video_gateway.py`, using a synthetic TS made with ffmpeg testsrc with
     known PTS and meta:
     - every sampled frame is XADDed in order, with the right `seq` and telemetry;
     - flow control: with a consumer that stops reading, the decoder pauses at the window, nothing
       is trimmed, and after the consumer resumes every `seq` arrives;
     - restart of the decoder mid-mission resumes without a gap or a duplicate `seq`;
     - the drain waits for `decoder_done`;
     - auth refusals give the right close codes;
     - reconnect resumes `seq`;
     - the recorder hash chain verifies;
     - a replacing connection closes the old one.
   - The existing ingest tests still pass.

**Done when:**
- with the edge's E2 build (or `tools/send_ts.py`) streaming a 4K DJI file, the analytics stream
  receives 20 fps of source-resolution frames with correct telemetry, for 30 minutes;
- with a service deliberately capped to 5 fps for 5 minutes, it then catches up and has analysed
  every `seq` (Lost = 0, Dropped = 0), while the live video never stalled;
- recordings play in VLC and their hashes verify;
- `/ingest/frame` keeps working for an old edge;
- `DVAS_VIDEO_INGEST=0` closes the endpoint (404).

### Phase R2 — Live video in the dashboard (3–4 days)

1. **Stream slots.** go2rtc accepts pushes only into streams declared in its config, and its
   API can add stream sources. So the API stays locked (`allow_paths: [/api/ws]`, localhost
   only), and `etc/go2rtc.yaml` declares 16 slots, `live01` … `live16`.
   - Ingest assigns a slot per streaming mission: `live:slot:<name>` = sid (SET NX), also stored
     as `live_slot` in `mission:video:{sid}`.
   - It frees the slot when the connection ends, followed by a 90 s cool-down.
2. **Backend** — new `routers/live.py`. No tickets: the dashboard's WebSockets already
   authenticate with the httpOnly session cookie (`?token=` on the edge app), and nginx's
   `auth_request` forwards it.
   - `GET /api/live/drone/{drone_id}`: same access check as `/frame/{drone_id}/latest`. Returns
     `{src: <slot>, ws_path: /live/api/ws?src=<slot>, width, height, fps, analytics_fps,
     behind_live_s}`, or 404 when the drone sends no video. It reads ingest's internal
     `/ingest/video/live/{drone}`.
   - `GET /api/live/authorize` (nginx `auth_request`): the user, plus the drone whose mission the
     slot carries *at this moment* (ingest `/ingest/video/slot/{slot}`), plus the operator /
     jurisdiction rule → 204, else 401 / 403.
3. **nginx.**
   - Only `location = /live/api/ws` is proxied to go2rtc; every other `/live/` path returns 404.
   - The player socket closes after **30 s without video**. With the 90 s slot cool-down, no
     viewer of a finished mission is still attached when the slot carries another drone.
   - `/ingest/video/live|slot` are internal (404 from outside).
4. **Frontend player** — `hooks/useLiveVideo.js`. A minimal MSE client for go2rtc's protocol:
   - sends `{"type":"mse","value":<codecs>}`, appends fMP4 into a SourceBuffer;
   - jumps to the live edge when more than 1.5 s behind; keeps 10 s of buffer;
   - reconnects when no data arrives for 8 s;
   - falls back to the JPEG poller while unavailable, and re-checks every 15 s;
   - uses `ManagedMediaSource` on iOS 17.1+.
5. **Replace the pollers** (the JPEG poll runs only while no video plays):
   - `MultiDroneView.js` `useDroneFeed`;
   - `TacticalMapPage.js` tiles.
   - The raw-feed panes of the analytics pages move to Stage 2 (R3), where those pages switch to
     video + browser overlays.
   Offline drones keep the Phase 1 A5 behaviour (placeholder, no polling).
6. **Grid bandwidth** (not in Stage 1; after the R0 numbers).
   - Nine tiles × 5 Mbps is too much for many control-room links.
   - Option, enabled with `DVAS_LIVE_SUBSTREAM=1`: the gateway publishes `{sid}_sub` (640 px,
     800 kbps, `h264_nvenc` if R0 confirmed NVENC, else `libx264 ultrafast`).
   - Grid tiles use `_sub`; a maximised tile uses the main stream.
7. **WebRTC (LAN / on-prem only):** enable go2rtc WebRTC with a configured TCP port. Not used on
   RunPod.

**Done when:**
- Multi-Drone Grid and Tactical Map show the live feed at the source fps;
- glass-to-glass p95 ≤ 1.5 s from Kerala (R0 method);
- with the flag off, the old pollers return;
- a user outside the drone's access, or a free slot, is refused (403) and learns nothing about the
  stream.

### R2 status on the pod (2026-11-11, final): **smooth live video verified** (image `20261111-8584b2c`)

- Multi-Drone Grid plays the H.264 sub-stream smoothly from Kerala (operator check);
  `nginx_live.log`: player sockets `101`, `listed=1` (RunPod's proxy passes Host
  `100.65.x.x:<port>`, so the public-origin list is what admits them). A `403` after a mission
  ends is expected: the slot is free and `/api/live/authorize` refuses it.
- Sub-stream encoder on the pod: `h264_nvenc` (with `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`).
- Same run: 4 × 4K, 2400 / 2400 frames per drone, 20 fps, ≤ 0.6 s behind live.
- Still to record: badge numbers (sub vs. selected 4K tile) and glass-to-glass latency.

#### History of the R2 debugging (kept for reference)

**Root cause found (2026-11-11, later):** the tiles never played H.264. go2rtc refuses a
WebSocket whose `Origin` differs from its `Host` (HTTP 403), and behind nginx the `Host` is
`127.0.0.1` while the browser's `Origin` is the pod URL — so every browser player socket was
refused (Firefox: `NS_ERROR_WEBSOCKET_CONNECTION_REFUSED` on `/live/api/ws`) and the tile kept
showing the 0.7 s JPEG fallback: the "choppy" picture. Local tests missed it because the test
clients sent no `Origin`. **Fix:** nginx checks the origin itself (`$live_origin_ok`: same host,
or no `Origin` for the edge app's native client) and sends no `Origin` to go2rtc. Verified in the
image through the real `nginx.conf`: same-site 101 with one video message per frame; another
site 403. The analysis below (bandwidth, decode, player) still applies once video plays.

Also found in the same pod run: `send_ts --loop` restarted the PTS on each pass and the decoder
stopped analysing after the first pass (600 of 2400 frames). Fixed in `7d9b0d5` (send_ts keeps
PTS increasing; the decoder survives a PTS restart and matches each pass to its own meta).

- The tile plays the H.264 stream (the 403 fix works); the operator still sees it as choppy.
- **Server path verified smooth** (production image, locally): relay ffmpeg → go2rtc RTSP →
  go2rtc MSE plays 1080p at **30.0 fps, 0 dropped** in go2rtc's own player, and the same
  through a copy of `useLiveVideo`'s logic (0 jumps, 0 stalls).
- So the remaining causes are between the pod and the browser — to be measured, not guessed:
  1. **Bandwidth over the RunPod proxy (EU → Kerala):** each 4K tile is ~8 Mbps; 4 tiles ≈ 32 Mbps.
  2. **Browser decode:** several 4K H.264 streams at once exceed many laptop GPUs' decoders.
  3. **Player behaviour on a jittery link:** `useLiveVideo` seeks to the live edge when > 1.5 s
     behind (a visible skip), and a reconnect swaps the tile to the 0.7 s JPEG poll until video
     plays again.
- **Next (R2 hardening, before R3):**
  1. **Player diagnostics** on the tile (debug toggle): rendered fps, dropped frames, behind-live,
     received Mbps, reconnects — so the cause is read off the screen.
  2. **Grid sub-stream** (R2.6, `DVAS_LIVE_SUBSTREAM=1`): grid tiles play a 720p ~1.5 Mbps
     stream; the selected / maximised tile plays the source. Transcoded with `h264_nvenc` if the
     pod exposes NVENC (`NVIDIA_DRIVER_CAPABILITIES` needs `video`), else `libx264 ultrafast`.
  3. **Smoother catch-up:** `playbackRate` 1.05–1.25 to drift back to the live edge instead of
     seeking; seek only when > 5 s behind. Keep the last video frame on screen during a short
     reconnect instead of switching to JPEG.
  4. **Glass-to-glass latency** with a burnt-in clock (R0 item 1, still open).
- **Built (2026-11-11), items 1–3:**
  - `useLiveVideo`: catch-up by `playbackRate` (≤ 1.25×, target 0.5 s behind the newest buffered
    frame), seek only when > 5 s behind; a reconnect keeps the last frame for up to 10 s
    (`reconnecting` counts as playing) before the tile falls back to JPEG; `stats` for the
    diagnostics badge (`components/LiveVideoStats.js`, switch: `localStorage dvas.liveDebug = "1"`
    or `?livedebug=1`): rendered fps, dropped, behind, kbps, reconnects, seeks, rate.
  - Grid sub-stream: `video_relay.LiveRelay` runs a second ffmpeg into go2rtc slot `liveNN_sub`
    (16 twins declared in `go2rtc.yaml`) only while `live:sub_want:<slot>` exists. The grid's
    `GET /api/live/drone/{id}?sub=1` sets it (45 s) and the player repeats the call every 20 s.
    Multi-Drone Grid tiles use it except the selected tile; Tactical Map tiles always.
    `/api/live/authorize` accepts `liveNN_sub` for the drone of `liveNN`. go2rtc's own `ffmpeg:`
    source was not usable: it needs ffmpeg ≥ 5, the image has 4.4.
  - Measured in the production image: encoder auto-detect picks `h264_nvenc` where NVENC is
    exposed; 4K → 720p sub-stream plays at **29.9 fps, 0 dropped, ≈ 1.4 Mbps** (source 8 Mbps).
    `libx264` fallback: ≈ 0.8 CPU core per watched drone (2× real time).
  - Tests: ingest 66 (new: the sub-stream starts only when asked, both outputs start at a
    keyframe, `_sub` slot ownership); backend 110 (new: sub lookup and authorize).
  - On the pod: check whether NVENC is exposed — `grep "sub-stream encoder" /var/log/dvas/ingest.log`
    after a grid tile opens. If it says `libx264`, add `video` to `NVIDIA_DRIVER_CAPABILITIES`
    in the template (`compute,utility,video`).

### Phase R3 — Overlays in the browser; stop re-encoding for live view (5–6 days)

1. **Per-frame results bus** — `shared/hud.py`.
   - `publish(service, drone_id, frame_fields, w, h, objects, overlays)` →
     `XADD mission:hud:{sid} MAXLEN ~600`. The packet:
     ```json
     {"service":"vehicle","seq":1234,"pts_ms":41233.4,"capture_ts":1762671000.12,"w":1920,"h":1080,
      "objects":[{"id":"17","cls":"car","bbox":[0.41,0.51,0.45,0.63],"conf":0.91,
                  "label":"KL-01-AB-1234","color":"#10B981","layer":"anpr","alert":false}],
      "overlays":{"geofence":[[0.21,0.29],[0.67,0.27],[0.73,0.82],[0.16,0.85]],"count":42}}
     ```
   - Coordinates are normalised to the analysed frame. At most 300 objects.
   - Each analyzer calls it once per frame, where it calls `hub.put_frame` today:
     - person (person boxes, face boxes, watchlist labels);
     - vehicle;
     - ANPR (plates);
     - intrusion (geofence polygon in pixels, PIP state per track);
     - vessel;
     - anomaly (alert boxes, vectors);
     - crowd (count, and a small density PNG at ≤ 2 Hz as `overlays.heatmap`).
   - With `DVAS_HUD=1` (default on in v2).
2. **Backend WebSocket** — `WS /api/hud/ws?drone_id=&services=`.
   - Same ticket/jurisdiction check as R2.
   - XREAD on the drone's active `mission:hud:{sid}`.
   - Per-service rate ≤ 20 Hz to each client; a 1 s send timeout; slow clients are dropped (the
     lesson from D4).
   - Heartbeat every 20 s.
3. **Frontend.**
   - `hooks/useHudStream.js`.
   - `components/HudCanvas.js` — the blueprint's `TacticalHudCanvas` is the starting point, with
     these fixes:
     - map coordinates through the video's **letterboxed** content box (`object-fit: contain`), not
       the element box;
     - redraw on `requestVideoFrameCallback`;
     - DPR-aware;
     - layer toggles per service;
     - click → target details (track id, Re-ID / plate history link).
   - **Sync** uses the method chosen in R0:
     - (a) if fMP4 keeps the source PTS: draw the packet whose `pts_ms` matches the frame's
       `mediaTime`;
     - (b) otherwise: hold the video behind the live edge by the measured analytics latency (MSE
       lets the player sit behind the live edge), and match by `capture_ts`;
     - (c) last resort: the latest packet, with boxes moved along each track's velocity.
     - Target skew ≤ 150 ms p95, shown in a debug overlay.
   - Analytics pages and the Multi-Drone view use `LiveVideo` + `HudCanvas` when video is
     available, otherwise MJPEG.
   - A **"frame-exact view"** toggle keeps the MJPEG annotated stream for review and evidence
     checks.
4. **Lazy annotated rendering** (`DVAS_LIVE_LAZY_ANNOTATE=1`).
   - `AnnotatedHub` gets `put_lazy(drone_id, render_fn)`: draw + resize + encode run only while at
     least one MJPEG subscriber is connected, or `/annotated/latest` was requested within the last
     5 s.
   - **Audit each call site first.** Where the annotated JPEG also goes into an alert or evidence
     record, it is still rendered on that frame. Evidence rule: alert-moment images are never
     skipped.
   - Expected saving: about 10–15 ms of CPU per frame per service.
5. **Tests:**
   - HUD packets from a recorded clip match the MJPEG overlay boxes (same frame, IoU ≥ 0.99);
   - lazy mode renders on alert frames;
   - the backend WebSocket drops a stalled client without delaying the others.

**Done when:**
- the Multi-Drone view shows the live video with person + vehicle + ANPR + geofence layers
  together, toggleable, at the source fps;
- the CPU per analyzer drops by the measured render cost when nobody watches MJPEG;
- the §3 evidence rules tests still pass.

### Phase R4 — Remove redundant inference and CPU contention; reach 20 fps (6–8 days)

Every step is measured on the pod before and after, with the stage split (A4) and `bench_person.py`.

**Status (2026-11-10):** started with person, the measured bottleneck.
- Done (`PERSON_FACE_GPU_TILES=1`, `PERSON_FACE_PARALLEL=1`, both on by default):
  - face tiles are cut on the GPU from one upload of the frame, not as 28 CPU crops;
  - face tiles run on their own CUDA stream while person detection + tracking runs, so a round
    costs max(person, face), not the sum.
- Verified locally: identical faces (coordinates, confidences) in FP16 and FP32, and identical
  tracker ids, boxes and face links, sequential vs parallel.
- **Measured on the pod (image `20261110-f9a30a8`, RTX PRO 6000):**
  - face tiles: 34 ms (CPU cut) → 29 ms (GPU cut); person round: 60 ms (in turn) → 50.6 ms (parallel);
  - e2e, one 4K drone: **analytics 20 fps sustained, 0.1–0.7 s behind live, 400 / 400 frames,
    wall time 22 s for a 20 s clip** (was 8–10 fps and 26 s behind).
- Next gain seen in the profile: Ultralytics copies the 28 GPU tiles back to the host for
  `orig_img` (≈19 ms device→host per round on the face stream).
  **Done (`PERSON_FACE_DIRECT=1`):** the GPU tiles go through the predictor's own network and
  NMS with the same settings, boxes clipped as its `scale_boxes()` does. Verified in the
  production image (Ultralytics 8.3.253): identical faces to `predict()` and to the CPU-cut path.
- Found while testing: `yolov8n-face.pt` needs `omegaconf`, missing from the image — Ultralytics
  pip-installed it at model load. Installed in its own Dockerfile layer (so the venv layer stays
  cached); move into `requirements/ai.txt` at the next full venv rebuild.
- `bench_person.py` now uses a synthetic frame with real people/faces, so its FP16 face check
  compares detections (it compared 0 with 0).

1. **Vehicle → ANPR shared detections** (`DVAS_SHARED_DETS=1`).
   - Vehicle's HUD packet already carries the visdrone boxes and its track ids. It adds the raw
     detection fields ANPR needs: `xyxy` in pixels, `conf`, `cls`.
   - Vehicle publishes at the lower of the two services' confidence thresholds; each service applies
     its own threshold.
   - **ANPR**, for frame `seq`, reads vehicle's packet from `mission:hud:{sid}`. It waits at most
     `DVAS_DETS_WAIT_MS` (150 ms) and runs the plate stage on those boxes, using vehicle's track ids.
   - If vehicle is not running (heartbeat key missing) or the packet times out, ANPR runs
     `visdrone.pt` itself, exactly as today. No frame goes without detection.
   - **Accuracy guard:** same weights, same frame, same image size → identical detections. Verify
     the image-size and NMS settings match, and test that on a clip the plate reads are equal before
     and after.
   - **Saves** one YOLO pass and one tracker (with GMC) per frame whenever both services run.
2. **GPU/CPU stage pipelining** (`DVAS_STAGE_PIPELINE=1`), in the ordered services.
   - Frame N+1's model call (GPU stage) overlaps frame N's tracker update, post-processing and
     persist (CPU stage).
   - The tracker still consumes frames strictly in order (a 1-deep queue between the stages), so
     ordering, Late/Lost and the evidence rules are unchanged.
   - Throughput becomes max(stage) instead of the sum. Person at 4K: ≈ 85 ms → ≈ 60 ms (estimate).
3. **TensorRT FP16** (Phase 3 B7; `DVAS_TRT=1`).
   - Engines are built on the pod, cached in `/workspace/dvas/engines/<sm>-<trt>/`, keyed by GPU,
     driver, TensorRT and model hash.
   - On a build failure, fall back to `.pt`.
   - Order: person and face first (the face tile batch is the largest single cost), then
     visdrone, then the anomaly primary model.
   - The face model ships only if face recall is unchanged (accuracy guard).
4. **Per-service thread budgets** (replaces the uniform `DVAS_CPUS/8`).
   - `dvas-init.sh` computes `DVAS_THREADS_<SVC>` from `DVAS_CPUS` with weights:
     - person 3
     - ANPR 2 (OCR)
     - anomaly 2
     - vehicle 1.5
     - intrusion 1
     - vessel 1
     - crowd 0.5
     - video-gateway 1 per active stream
   - The sum is ≤ `DVAS_CPUS`. supervisord passes it per program.
   - Each service applies it to `torch.set_num_threads`, `cv2.setNumThreads`, onnxruntime
     `intra_op_num_threads` and Paddle `cpu_threads`.
   - Any value can be overridden per service through env.
5. **Multi-frame catch-up** (Phase 3 B6): when a drone has a backlog, process up to K consecutive
   frames per round as one GPU batch; the tracker is still updated in frame order.
6. **ANPR OCR on the GPU** (Phase 3 B4). This needs the plate test set; it ships only if correct
   plate reads ≥ today's.
7. **Optional, measured:** CUDA MPS (`nvidia-cuda-mps-control -d` in the container) to let the
   kernels of the 7 processes overlap. Keep it only if total fps rises and nothing becomes unstable
   in a 2-hour run.
8. **Mission presets** in the dashboard: Traffic = vehicle + ANPR; Crowd event = crowd + person +
   anomaly; Perimeter = intrusion + person; Coast = vessel. Only enabled services read the stream.
   This is the cheapest way to stay at 20 fps with several drones.

**Done when**, on the L40S:
- with 2 drones at 1080p and vehicle + ANPR + person + intrusion enabled, every service holds ≥ 20
  fps for 30 minutes, lag ≤ 1 s, and the stage split shows the GPU as the largest share;
- with 1 drone at 4K (source resolution, the default), person holds ≥ 20 fps. If it does not, the
  gap is recorded here; frames are still not lost (they wait on disk), they are only analysed later.

### Phase R5 — Other critical items (3–4 days)

1. **Capacity and admission.**
   - `tools/bench_capacity.py` replays a recorded TS into N simulated drones through the
     video-gateway, with a chosen service set, and reports per-service fps and lag. Record the
     table (drones × service set → fps) here.
   - **Status (2026-11-10): `tools/bench_capacity.py` added.** N `send_ts` senders at once
     (30 fps H.264, 8 Mbps at 4K / 4 Mbps at 1080p), straight to ingest (nginx allows 4 uplinks
     per token); samples every 5 s each drone's analytics fps and lag behind live, the service
     with the largest backlog, and GPU use. Ingest caps uplinks at `DVAS_MAX_LIVE_STREAMS` (8).

     | Drones × resolution | Analytics fps per drone | Max behind live | Slowest service |
     |---|---|---|---|
     | 1 × 4K (e2e, 2026-11-10) | 20 | 0.7 s | — |
     | 2 × 4K (2026-11-11, image `7125f0c`) | 19.8–20.2 | 0.7 s | none (backlog 0) |
     | 4 × 4K (2026-11-11) | 20.0–20.2 | 0.6 s | none (backlog 0) |
     | 4 × 1080p (2026-11-11) | 20.0–20.2 | 0.6 s | none (backlog 0) |
     | 8 × 1080p | _to measure_ | | |

     All 600 / 600 analytics frames per drone. Person bench on the same image: person round
     36.7 ms (parallel), face tiles 12.4 ms with `PERSON_FACE_DIRECT` (45 ms via `predict()`),
     FP16 face check 11 / 11. Synthetic clips (`testsrc2`) have few detections; real footage
     with crowds costs more per frame — repeat with a recorded drone clip before sizing for 20 drones.
   - Gateway governor: when an enabled service stays below `DVAS_ANALYTICS_FPS` for 30 s, the
     dashboard shows **Degraded: <service> 14 fps** on the drone's tile and the Models page.
   - Do not silently drop frames.
2. **End-to-end latency metrics.**
   - `capture_ts` → `rx_ts` (uplink), → analysed (sampler), → HUD sent (backend).
   - p50/p95 on the Models page and in Prometheus.
3. **Disk guard covers recordings.**
   - `backup-loop.sh` writes `/run/dvas/disk_guard.json`; this is the blueprint's F2.
   - Recording stops with an alert at 90 % of `DVAS_VOLUME_GB`, never deleting evidence.
   - The Ingest dashboard shows a banner.
4. **Optional — shared-memory frame bus** (`DVAS_FRAME_BUS=shm`).
   - Do this only if, after R1–R4, JPEG decode is still a top-2 stage.
   - The video-gateway writes decoded frames into a per-mission ring in `/dev/shm` (size from R0).
     The XADD then carries the slot and generation instead of a JPEG.
   - Analyzers map the slot read-only. Alert / evidence JPEGs are encoded from it on demand.
   - A frame whose slot was reused before every group read it is published again as JPEG. The
     evidence rule (every frame analysed) holds.
   - Memory: 1080p BGR is 6.2 MB per frame, so a 10 s ring at 20 fps is ≈ 1.2 GB per drone. This is
     why it is optional.

---

### Phase R6 — Live-first analytics: feed modes, overload shedding (from the "30 fps reference architecture" review, 2026-11-11)

**Source:** a description of how tier-1 platforms keep a 30 fps wall (lossy latest-frame AI
queue, detection at 5–10 Hz with tracking in between, browser-side interpolation, zero-copy
NVDEC → GPU memory, JSON overlays). The same ideas are in the blueprint (§2.3, §5.2–5.3) and in
Frigate (detects at ~5 fps on the latest frame, records separately).

**Where DVAS v2 stands against it (measured on the pod, 2026-11-11):**

| Principle | DVAS v2 today | Verdict |
|---|---|---|
| Never block video delivery for AI | Done (R1/R2): go2rtc serves the edge's H.264 straight to browsers; analytics read a separate feed from the recording. Live view does not wait for any analyzer. | **Already done** |
| Lightweight JSON overlays, not re-encoded JPEGs | Planned (R3 HUD bus + `HudCanvas`); MJPEG annotated views still exist. | **Adopt — R3** (unchanged) |
| Browser interpolation of boxes (rAF, 60 fps) | R3 sync method (c) was a "last resort". | **Adopt as the default render loop in R3**: draw on `requestVideoFrameCallback`, place each box by its packet's `pts_ms` / `capture_ts`, and between packets move it along the track's velocity (lerp). Packets older than 1.5 s are not drawn (R6.4). |
| AI queue capped at 1, drop stale frames | No loss, disk-backed backlog; every frame analysed. | **Adopt where it fits (decided):** latest-frame for crowd and vessel always (R6.1); drop-oldest for every service only under overload (R6.3). Critical services keep every frame under normal load. |
| Detector at 5–10 Hz, tracker bridges the rest | Every analytics frame (20 fps) gets full detection. | **Only as the overload step-down** (R6.3 level 3); no fixed lower rate (decided). |
| Zero-copy NVDEC → GPU memory | CPU H.264 decode → JPEG → Valkey → each service CPU-decodes the JPEG (27 ms at 4K) → GPU. | **Partly** — R6.6 (GPU JPEG decode now; shared GPU frames only if still needed). |

**Why the critical services do not drop frames all the time:** at today's measured load (4 × 4K,
20 fps, backlog 0, R5 table) nothing lags, and dropping by default would cost: ANPR (fewer looks
at each plate), the face watchlist (fewer chances per face), Late / Lost accounting, and the
intrusion / anomaly rules, which count **consecutive frames** (`intrusion/state_machine.py`
`confirm_inside_frames`) — hence R6.5 (thresholds in seconds) before any frame is ever skipped.

#### Decisions (2026-11-11, operator answers) — supersede the "no loss" row of §0 for overload

| Topic | Decision |
|---|---|
| Services that run on the **latest frame only** | **crowd** (density + heatmap) and **vessel**. They never queue and never hold the feed back. |
| Services that analyse **every frame** under normal load | **person / face, ANPR, intrusion, anomaly** (and vehicle, which feeds ANPR). |
| **No analytics service selected** | No analytics queue at all: the decoder does not decode or publish frames; only live view and recording run. |
| **Overload** (a service more than **1.5 s** behind live) | **Shed, don't queue:** drop to the newest frame (drop-oldest), mark the gap, step the detector cadence down with tracker prediction in between, skip secondary models unless a priority event is in progress, and show the state. Live video is never affected. |
| Fixed lower detection rate per service | **None for now** — only the dynamic step-down under overload. |
| Intrusion / anomaly thresholds | **Converted from frame counts to seconds**, so alarm timing does not change with the analytics rate. |

**What this keeps and what it changes:**
- **Kept:** live video at the source rate (independent of analytics); the recording on disk with
  every frame and its SHA-256 chain — the Section 65B source evidence is complete whatever
  analytics shed; telemetry for every frame (ingest updates the drone's live position from each
  `meta`, independent of analytics); evidence rules for frames that **are** analysed (alerts,
  plate reads, face crops, alert-moment frames are never throttled once a detection exists).
- **Changed:** under overload some frames are not analysed by the critical services. An alert
  whose only frames fall in a shed range is missed in real time. Every shed range is recorded
  (service, mission, `seq` range, reason) in the audit log as `analytics_shed`, so coverage gaps
  are explicit and defensible; a later **backfill** pass from the recording can analyse them
  (R6.7, optional).
- At today's measured load (4 × 4K, 20 fps, backlog 0) no shedding happens; this is the overload
  behaviour for 20 drones and for spikes.

#### R6 status (2026-11-11): built and tested locally; not yet on the pod

| Item | Built | Tests |
|---|---|---|
| R6.5 thresholds in seconds | intrusion confirmations = (N−1)/20 s of capture time (`INTRUSION_CONFIRM_S`), gap > 1 s restarts a streak; anomaly gets the real frame spacing (it was told 30 fps while frames come at 20 fps: duration rules ran at 2/3 speed — **now fire at their configured seconds**), lost tracks 1.5 s, alert clearing 0.45 s; both fed `capture_ts`, not processing time | same decisions as the frame counts at 20 fps (200 random patterns, ±10 ms jitter); same timing at 10 / 5 fps |
| R6.1 feed modes | vessel on the newest frame (crowd already); `DVAS_FEED_MODE_<SVC>`; the decoder never waits for a newest-mode service | decoder test with a busy crowd group |
| R6.2 no feed without services | decoder skips decoding while no service publishes stats; resumes at the next keyframe at live (`DVAS_DECODE_ONLY_WITH_SERVICES`) | nothing published, mission still drains |
| R6.3 shedding | sampler levels 1–3 with recovery; `_gap` on jumps, tracker reset over 1 s; ranges → `dvas:analytics_shed` → backend writes `analytics_shed` audit rows (consumer group, ack after write). Person: Re-ID clustering waits while shed; a watchlist hit marks priority. **Not shed** (evidence rules): face super-resolution, face crops, ArcFace / ResNet embeddings, person crops | live drone untouched; level 1 → drop-oldest with logged range; cadence + recovery; audit row per range, retried on failure |
| R6.4 states | Models page: `degraded` / `shedding` + frames shed; tiles: `DEGRADED` / `SHEDDING` badge (`GET /api/analytics/drone-states`) | backend view tests; frontend builds |
| R6.6 GPU JPEG decode, R6.7 backfill | not started | — |

Temporal votes (e.g. fall: 5 of 8 frames) stay counts of observations: at a lower rate they wait
for the same evidence over a longer time (conservative).

Found on the way (not R6): the backend's `/ws/status` listener (A5, drone offline on stream end)
uses the role-less `dvas-admin-service` token, which ingest rejects — it has likely never
connected on the pod; drones go offline through the slower telemetry-pump fallback.

#### R6.1 Feed modes per service (`DVAS_FEED_MODE_<SVC>` = `every` | `latest`)
- Defaults: `latest` for crowd and vessel; `every` for person, vehicle, ANPR, intrusion, anomaly.
- `latest`: the sampler reads the newest entry per drone (`XREVRANGE … COUNT 1`) each round, with
  no consumer group, so it never adds to the decoder's flow control window. Frames in between are
  not analysed by design (counted as `sampled_out`, not as lost).
- `every`: today's consumer-group path (ordered, ack after analysis) plus R6.3 shedding.

#### R6.2 No services selected → no analytics feed
- The decoder decodes and publishes only while at least one service is enabled for the drone
  (an active consumer group, or a `latest` reader's heartbeat). Otherwise it only follows the
  recording position, so CPU is not spent on frames nobody reads.
- When a service is enabled mid-mission it starts at the live position (no backlog from before).

#### R6.3 Overload shedding for `every` services (`DVAS_SHED_AFTER_S=1.5`)
Measured on the server clock (`rx_ts_ms` of the frame being read vs. now), with hysteresis:
enter a level when behind > threshold, leave it after ≤ 0.5 s behind for 10 s.
1. **Level 1 — shed secondary stages** (> 1.5 s): skip person whole-body Re-ID embeddings, face
   Re-ID clustering, face crop enhancement, anomaly secondary-model passes. **Kept even then:**
   detection, tracking, ArcFace watchlist matching on detected faces, plate OCR, alert
   generation — the primary police functions. A priority event in progress (open alert, watchlist
   hit) keeps the secondary stages on for that drone.
2. **Level 2 — drop-oldest** (still > 1.5 s after level 1 for 3 s, or > 3 s): the sampler XACKs
   every pending entry older than the newest one and continues from the newest (no growing PEL).
   The skipped `seq` range is recorded (`analytics_shed` audit event, `frames_shed` counter) and
   the next frame carries `gap=true`: trackers do not read the jump as a velocity spike — tracks
   whose age exceeds the gap are retired and Re-ID re-links identities.
3. **Level 3 — detector cadence step-down**: detect every 2nd, then every 4th frame; between
   detections the tracker's Kalman prediction moves the boxes (published as `predicted`).
   Alerts, crops and evidence come only from frames with real detections.
- The decoder's flow-control window applies only while no group is shedding; with shedding the
  feed runs at live pace and the stream's small `MAXLEN` acts as the leaky bucket.
- Valkey memory stays bounded (≈ 5 s per mission) in every case.

#### R6.4 Dashboard: smooth degradation
- Tile and Models page show the service state: `LIVE`, `DEGRADED` (level 1), `SHEDDING` (level
  2/3, with frames shed), `DRAINING`.
- The HUD (R3) draws no packet older than 1.5 s against the live video; between packets boxes
  move by track velocity (rAF interpolation) — no stale boxes over current video.

#### R6.5 Thresholds in seconds
- Intrusion `confirm_inside_frames` / `confirm_outside_frames` → `INTRUSION_CONFIRM_S` (default
  = today's frames ÷ 20 fps, so behaviour at 20 fps is unchanged); dwell / loiter timers already
  in seconds stay. Anomaly frame-count rules (running, fall, loitering hysteresis) likewise, using
  `capture_ts` deltas. Tests: the same alerts at 20 fps as before; correct timing at 10 / 5 fps
  and across a `gap=true`.

#### R6.6 Cheaper frame hand-off to the GPU
1. **GPU JPEG decode** in the sampler (`torchvision.io.decode_jpeg(device="cuda")`): 0.3 ms
   instead of 27 ms CPU per 4K frame per service; identical-output test against `cv2.imdecode`.
2. **NVDEC** in `video-decoder` when the pod exposes the `video` driver capability.
3. Shared GPU frames between processes only if 1–2 leave decode as a top cost (R5.4 on the GPU).

#### R6.7 Optional — backfill of shed ranges (`DVAS_BACKFILL=0`)
- When the system is idle, analyse the recorded `analytics_shed` ranges from the recording with a
  separate tracker; results marked `late`. Off by default; enable if missed coverage matters.

**Tests (R6):** a service held at 5 fps enters level 1 → 2 → 3 within seconds, its PEL stays
small, every shed range is in the audit log, and it returns to `LIVE` after the load ends; a
`latest` service never delays the feed; with no service enabled the decoder publishes nothing;
intrusion / anomaly alerts fire at the same times at 20 fps as before; live video fps unchanged.

**Done when:** with load beyond capacity (e.g. 8 × 4K, all services), every `every` service stays
within ~1.5–3 s of live, shed ranges are marked, live video stays at the source rate, and the
recording is complete.

## 5. Flags summary

| Flag | Default (v2) | Phase |
|---|---|---|
| `DVAS_VIDEO_INGEST` | 1 | R1 |
| `DVAS_ANALYTICS_FPS` | 20 | R1 |
| `DVAS_ANALYTICS_MAX_WIDTH` | 0 = source resolution | R1 |
| `DVAS_VIDEO_WINDOW_FRAMES` | 100 (≈ 5 s at 20 fps) | R1 |
| `DVAS_VIDEO_MAX_KBPS` / `DVAS_MAX_LIVE_STREAMS` | 12000 / 8 | R1 |
| `DVAS_RECORD_VIDEO` / `DVAS_RECORD_RETENTION_DAYS` | 1 / 7 | R1 |
| `DVAS_LIVE_SUBSTREAM` / `DVAS_LIVE_SUB_ENCODER` | 1 / auto (nvenc → libx264) | R2 |
| `DVAS_HUD` / `DVAS_LIVE_LAZY_ANNOTATE` | 1 / 1 | R3 |
| `DVAS_SHARED_DETS` / `DVAS_DETS_WAIT_MS` | 1 / 150 | R4 |
| `DVAS_STAGE_PIPELINE` | 1 | R4 |
| `DVAS_TRT` | 0 → 1 per model after the guard | R4 |
| `DVAS_THREADS_<SVC>` | computed | R4 |
| `DVAS_FRAME_BUS` | `jpeg` | R5 |
| `DVAS_FEED_MODE_<SVC>` | `latest` for crowd, vessel; `every` for the others | R6 |
| `DVAS_SHED_AFTER_S` | 1.5 | R6 |
| `INTRUSION_CONFIRM_S` (replaces frame counts) | today's frames ÷ 20 | R6 |
| `DVAS_GPU_JPEG_DECODE` | 0 → 1 after the identical-output test | R6 |
| `DVAS_BACKFILL` | 0 | R6 |

## 6. v2 acceptance (all on the pod, from Kerala)

| Check | Target |
|---|---|
| Live video per drone (grid and single view) | source fps (25–30), glass-to-glass p95 ≤ 1.5 s |
| Analytics per enabled service per drone | ≥ 20 fps for 30 min, `behind_live_s` ≤ 1 (2 drones, R4 service set, 1080p sources; 4K per R4) |
| Overload (R6) | a service capped to 5 fps sheds within ~1.5–3 s of live, every shed range is in the audit log, it returns to LIVE after the load ends; the recording is complete |
| HUD skew | ≤ 150 ms p95 (or the documented R0 fallback) |
| Frame order / identity | Late = 0, Lost = 0, `seq` without duplicates across a reconnect |
| Evidence | §3 rules tests pass; every alert has its crop + source frame; recording hash chain verifies |
| Fallback | Old edge on `/ingest/frame` works; every flag off → v1 behaviour |
| Real edge (`edge-exe-v2`) | The §7 real-edge requirement passes: 30 fps received, live tile plays, analytics ≥ 20 fps, for a 4K and a 1080p camera |

## 7. Coordination with the edge developer

| RunPod step | Needs from edge | Edge phase |
|---|---|---|
| R0–R1 testing | A TS + meta sender. Until E2 is ready, use `tools/send_ts.py` (ffmpeg + a websocket client, synthetic meta). | E2 |
| R1 release | E1 (correct `seq` / `capture_ts`) for the JPEG fallback | E1 |
| R2–R5 | — | — |

- Agree on the §3 contract **before** either side codes against it.
- Any change to it bumps `"v"`. The server accepts v1 for the whole v2 release.

### Requirement: stream test with the real edge application (added 2026-11-10)

The edge developer's uplink is on branch `edge-exe-v2` (`dvas_edge/ingest/video_uplink.py`,
commit `44e504f` and later). A read-only review against §3 found it follows the contract (hello,
Bearer auth, `meta` before its TS bytes, `bye`, close-code handling). It also found four risks
that must be cleared before v2 is accepted with a real drone.

| # | Risk | Effect if not handled | Owner and action | Test |
|---|---|---|---|---|
| E-1 | The edge sends each frame's TS bytes as **one** WebSocket message; a 4K H.264 keyframe is often > 256 KiB | Server closes with **1009 `bad_chunk`**; the edge reconnects, resends from the same keyframe, and loops — no live video | **Edge:** split each frame's bytes into messages of ≤ 256 KiB (multiples of 188), as §3 says. **RunPod:** make the limit configurable (`DVAS_VIDEO_MAX_MSG_KB`, default 256) so a field test is not blocked meanwhile | No `bad_chunk` in `/var/log/dvas/ingest.log` during a 4K stream |
| E-2 | The edge forwards the camera stream as is (copy mode); a real 4K camera can exceed 15 Mbps (1.25 × `DVAS_VIDEO_MAX_KBPS`) | Server closes with **4413 `over_bitrate`** after 10 s | **RunPod:** set `DVAS_VIDEO_MAX_KBPS` from the cameras' measured bitrate (for the first test 40000). **Edge:** the transcoding ladder (later release) | No `over_bitrate` in the ingest log; `rx_kbps` in `stats` matches the camera |
| E-3 | This edge release forwards only H.264; HEVC sources stay on the JPEG fallback | No live video for HEVC cameras (analytics still works via JPEG) | **Edge:** HEVC → H.264 transcoding (ladder). **Ops:** set the drone camera / RTMP output to H.264 for now | Edge log has no `video_uplink_codec_unsupported`; `codec=h264` in `mission:video:{sid}` |
| E-4 | The 2026-11-10 database wipe removed drone registrations and assignments | `mission/start` or the uplink is refused (403 / 4403) | **Ops:** register the edge's drone ID and assign it to the operator account the edge logs in with | `mission/start` 201; `ready` received |

**Test procedure** (pod on image `20261110-7125f0c` or later):
1. While the edge streams, in the pod:
   `watch -n 2 'for k in $(valkey-cli --scan --pattern "mission:video:*"); do echo "$k"; valkey-cli HMGET "$k" status width height fps codec last_seq_rx live_slot | paste -sd" "; done; tail -3 /var/log/dvas/ingest.log'`
2. Pass when:
   - `status=receiving`, `fps` 30 (or 29.97), `codec=h264`, `live_slot` set;
   - `last_seq_rx` rises by ≈ 30 per second;
   - the ingest log has no `bad_chunk` and no `over_bitrate`;
   - the drone's Multi-Drone Grid tile plays smooth video (DevTools → WS `/live/api/ws`: 101, binary frames);
   - `dvas-ctl lag` shows analytics ≈ 20 fps and ≤ 1 s behind live.
3. Repeat with a 4K and a 1080p camera, and once across a network drop (the edge resends from the
   last keyframe; no duplicate `seq`, Lost = 0).

## 8. Risks

| Risk | Mitigation |
|---|---|
| Cloudflare / RunPod proxy throttles long WebSockets or caps throughput | R0 measures it. The uplink adapts with `slow_down`. The JPEG path remains as a fallback. |
| go2rtc timestamps don't allow exact HUD sync | R0 decides; methods (b) and (c) in R3. The frame-exact MJPEG view stays. |
| NVDEC/NVENC not exposed in the container | CPU H.264 decode is cheap. The sub-stream uses libx264 `ultrafast`, or stays off. |
| go2rtc API exposure (it can add stream sources) | localhost only; nginx exposes only `/live/api/ws` behind `auth_request`. |
| Person at 4K stays under 20 fps | No frame loss (disk-backed); results run behind live and the tile says so. TRT and pipelining continue; mission presets reduce load. |
| Recording disk fills (≈ 7–9 GB per 4K drone-hour) | 7-day retention, refusal of new missions at 90 % with an alert, never deleting un-analysed or legal-hold recordings. |
