# DVAS Edge v2 — Implementation plan (edge `.exe`, branch `edge-exe-v1`)

*Version 1, 2026-11-09. Owner: edge developer. Companion document: `v2_runpod_implementation_plan.md`
(RunPod developer). §3 (the uplink contract) is identical in both documents; change it in both or
in neither.*

**Goal of v2:** every drone feed reaches the RunPod pod as a continuous video stream. The control
room sees it live at the source frame rate (25–30 fps). The analytics process it at **≥ 20 fps per
drone**. This is not a full overhaul. The work is limited to:

1. continuous WebSocket video uplink (H.264 in MPEG-TS) instead of one HTTP POST per JPEG;
2. ending the HTTP JPEG upload as the main path (it stays as a fallback);
3. edge-side CPU waste that caps the frame rate before anything leaves the laptop;
4. the correctness bugs that break frame order and timestamps today.

Out of scope for v2: GB/T 28181, WebRTC from the edge, MQTT gimbal control, a new edge UI design,
on-edge AI.

---

## 1. What the edge does today (verified on `origin/edge-exe-v1` @ `b948456`)

Path of one frame:
source → `RealVideoSource` (OpenCV decode) → `_on_frame` → (rolling buffer JPEG) → change
detection / cognitive selector, *or* bypass sampling → resize + JPEG → `OfflineBuffer` (file +
SQLite) → `EdgeSenderThread` → `POST /ingest/frame` (multipart).

| # | Finding | Where | Effect |
|---|---|---|---|
| E-F1 | **One HTTPS POST per frame, at most 5 in flight.** From Kerala the pod's POST round trip is 350–750 ms. | `edge_api_server.py:460` (`len(in_flight_ids) >= 5`), `:366` | Upload ceiling ≈ 5 / 0.5 s ≈ **7–14 fps**, whatever `bypass_target_fps` says. 20 fps cannot be reached on this path from Kerala. |
| E-F2 | **`frame_seq` and `capture_ts` are set when the frame is *sent*, not when it is captured.** A frame that fails and is retried later gets a newer sequence number and a newer capture time. | `ingest/edge_sender.py:365, :386` (and `:256, :268`) | The server orders and tracks frames by these fields. A retried frame looks newer than frames captured after it, so trackers see time jump back and forth. The server's re-ordering cannot repair it, because the numbers themselves are wrong. |
| E-F3 | **Failed sends wait 2 s, then go back into the queue** while newer frames continue to upload. | `edge_api_server.py:450` | Out-of-order arrival beyond the server's 300 ms re-order window. Combined with E-F2, the frame order is lost. |
| E-F4 | **Every live frame is written to disk and committed to SQLite before it is sent.** | `ingest/offline_buffer.py:46-60`, called from `edge_api_server.py:344, :348` | At 20–30 fps this means a file write plus an fsync-style commit per frame, on a laptop disk, on the send path. It is meant for offline buffering, but it is used for every frame. |
| E-F5 | **The rolling evidence buffer JPEG-encodes every source frame** (q85) inside the frame callback. | `src/buffer/rolling_frame_buffer.py:305` → `_compress_jpeg`, called first in `_on_frame` | A 4K JPEG encode takes ≈ 30–40 ms of CPU, on the reader thread. At 30 fps that is more than one core, and it slows the reader itself (the source fps drops). |
| E-F6 | **Bypass sampling assumes a 30 fps source:** `round(30 / target_fps)`. | `edge_api_server.py:653` | 25 fps or 60 fps sources send the wrong rate. |
| E-F7 | **Defaults: 720p, JPEG q75.** | `DEFAULT_SETTINGS`, `edge_api_server.py:104-114` | Analytics get 720p frames. Small faces and plates suffer. JPEG at 20 fps is still ≈ 13–19 Mbps at 720p and ≈ 32–48 Mbps at 1080p (estimates), against ≈ 4–6 Mbps for 1080p H.264. |
| E-F8 | In pipeline mode, the **cognitive frame selector decides which frames the server sees** (target 35 %). | `config/settings.yaml` `cognitive_frame_selector`, `edge_api_server.py:600-636` | The server cannot run trackers at 20 fps on 35 % of frames chosen by a scoring heuristic. |
| E-F9 | Source decode is OpenCV on the CPU (`use_gpu: false`). The local preview MJPEG re-encodes at 15 fps even when nobody is watching. | `src/video/real_video_source.py:626`, `edge_api_server.py:2079` | More CPU per frame. |

Items from the blueprint that are **already done** on this branch: the hard-coded `EDGE_API_KEY`
fallback has been removed (`edge_api_server.py:1152` now defaults to `""`). Nothing to do there.

---

## 2. Target design (edge side)

```
source (RTSP / MP4 / USB)
   │  PyAV demux — packets only, no decode
   ├──► VideoUplink ── H.264 MPEG-TS + per-frame meta ──► wss://<pod>/ingest/video/ws   (§3)
   │        (copy if the source is already H.264 and within the bitrate cap; otherwise transcode once)
   ├──► PacketRing (last 60 s of compressed packets) ──► evidence clips by remux (no re-encode)
   └──► LocalDecoder (reduced rate, e.g. 10 fps at 960 px) ──► change detection / preview / existing UI
```

- **One reader owns the source.** Many RTSP cameras and DJI remotes allow only one or two sessions,
  so the source must not be opened twice.
- **Pass-through by default.** Drone RTSP feeds and DJI MP4 files are normally H.264 already. Copying
  them costs almost no CPU on the laptop. Transcoding is used only for HEVC, for a bitrate over the
  cap, or for unusual profiles.
- The **server** chooses the analytics frames (20 fps out of the source rate). The edge sends every
  frame of the video.
- Telemetry (SRT / live) travels as small JSON messages tied to each frame's PTS, so the server can
  attach the exact telemetry to each analysed frame.
- **Fallback:** the HTTP JPEG path stays (fixed per §4 Phase E1). It is used for older servers, or
  when the WebSocket upgrade fails repeatedly.

---

## 3. Uplink contract v1 (shared with the RunPod plan — keep identical)

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

Each phase is one installer release. Every behaviour sits behind a setting in `edge_settings.json`
(the `transmission` block), so a field laptop can switch back without a reinstall.

### Phase E0 — Spike: PyAV on the Windows build (1 day)

1. Add `av` (PyAV) to `packaging/requirements-edge-win.txt`. Check that PyInstaller bundles its
   FFmpeg DLLs.
2. On a field laptop:
   - open the drone RTSP URL and a DJI MP4 with PyAV and demux packets (no decode);
   - mux them into MPEG-TS in memory and write them to a `.ts` file;
   - play the file in VLC and check that the PTS are monotonic.
3. List the H.264 encoders available on the laptop, through both the PyAV wheel and the bundled
   `ffmpeg.exe -encoders`:
   - `h264_nvenc` (NVIDIA)
   - `h264_qsv` (Intel)
   - `h264_amf` (AMD)
   - `libx264` / `libopenh264`
4. **Decision recorded in this file:**
   - Where PyAV has a usable encoder, transcoding runs in-process.
   - Otherwise transcoding runs through `ffmpeg.exe` as a subprocess. In that case the edge reads the
     MPEG-TS from stdout and reads PTS from the PES headers of the video PID (a small parser).
   - Copy mode always uses PyAV.
5. Measure CPU % for: copy of 4K30 RTSP; transcode of 4K HEVC → 1080p H.264, hardware and libx264
   `veryfast`.

**Done when:** a 10-minute DJI file and a 10-minute RTSP feed produce valid TS. The encoder
decision and the CPU numbers are written here.

### Phase E1 — Fix the JPEG path (fallback stays correct) (2 days)

This ships first. It helps the current release at once, and the fallback must be correct anyway.

1. **E-F2 Capture-time identity.**
   - Assign `seq` (per mission, starting at 1) and `capture_ts = time.time()` in `_on_frame`, at
     capture. Carry both through `put_frame` / `put_raw_frame` in the telemetry dict.
   - `send_frame_bytes` uses the values it is given. Remove `self.frame_seq += 1` and
     `time.time()` from `edge_sender.py:256, 268, 365, 386`.
   - A retried frame keeps its numbers.
2. **E-F3 Ordered sending.**
   - Send in `seq` order.
   - In-flight window = `ceil(target_fps × measured_rtt × 1.5)`, bounded to 2–24. Measure RTT as an
     exponential moving average of the POST latency.
   - Retry a failed frame *immediately* (0.1 s, then 0.3 s) before newer frames go out. Only after 3
     failures does it go to the offline store, and it is still sent with its original `seq`.
3. **E-F4 Memory first, disk only when needed.**
   - Live frames sit in a bounded in-memory deque (e.g. 5 s × fps).
   - Frames are written to `OfflineBuffer` only when the deque overflows or the server is
     unreachable.
   - SQLite commits are batched (one commit per 0.5 s).
4. **E-F6** Bypass sampling uses `source.source_fps`, not 30. Use a time-based sampler (send when
   `t ≥ next_t`, then `next_t += 1/target_fps`).
5. **E-F7** Default `max_resolution` becomes `1080p`, `jpeg_quality` 80. The setting stays.
6. Tests: `tests/test_edge_sender_order.py`.
   - Failure injection on frames 3 and 7 → the server receives seq 1..10 with the original
     `capture_ts`.
   - Each seq is sent exactly once on success.

**Done when:**
- a 10-minute run against the pod shows **Late = 0** and **Lost = 0** on the Models page;
- `capture_ts` is monotonic in the stream;
- the upload fps equals the target, up to the E-F1 ceiling.

### Phase E2 — Continuous video uplink (main deliverable) (5–7 days)

New module `ingest/video_uplink.py`, with tests in `tests/test_video_uplink.py`.

1. **`VideoUplink` (one per mission), started by `PipelineManager.start_mission`** when
   `transmission.transport == "h264_ws"` (the new default).
   - It opens the source with PyAV: `rtsp_transport=tcp` for RTSP, and a real-time pacing loop on
     PTS × `playback_speed` for files.
   - **Copy mode:** used when the codec is H.264 and the bitrate ≤ `max_kbps`. Packets are remuxed
     into MPEG-TS; source PTS are kept, rebased so the first packet is 0.
   - **Transcode mode:** for HEVC, other codecs, or a bitrate over the cap. Decode, then encode with
     the encoder chosen in E0.
     - Settings: `-g fps`, `-bf 0`, CBR/VBV at `max_kbps`.
     - Scale down to `max_resolution` only when transcoding is needed anyway.
   - One background asyncio loop holds the WebSocket (`websockets` is already in
     `requirements.txt`). Messages follow §3.
2. **Per-frame meta.**
   - Each frame is identified by its PTS: video packet PTS → `seq` (mission counter) and
     `capture_ts`.
   - For live sources, `capture_ts` is the wall clock at packet arrival. For files it is
     `mission_start_wall + pts`.
   - Telemetry comes from the existing SRT parser (`_load_telemetry`, keyed by frame index =
     round(pts × fps)) or from live telemetry. Field names are as in §3.
3. **Back-pressure and reconnect.**
   - Measure the send buffer: queued bytes ÷ the current bitrate = seconds behind.
   - Above 2 s behind: drop whole GOPs up to the next keyframe. This keeps the view live. Dropped
     frames are counted and shown, and they remain in the PacketRing (E3), so evidence clips still
     have them.
   - On disconnect: reconnect per §3, restart at the next keyframe, and continue `seq`.
   - Server `slow_down` → lower the encoder bitrate (transcode mode), or warn the operator (copy
     mode).
4. **Local frames for the existing UI.**
   - A `LocalDecoder` thread receives the same packets.
   - It decodes at `local_fps` (default 10) and scales to `local_width` (default 960). It uses PyAV
     hwaccel (`d3d11va` / `cuda`) when available, otherwise the CPU.
   - It emits the existing `VideoFrame` objects to `_on_frame`. Change detection, the cognitive
     selector, the preview and the UI keep working unchanged, at lower cost.
5. **Mission flow.**
   - Unchanged: `mission/start` (HTTP) → WebSocket `hello` … `bye` → `mission/end` (HTTP).
   - "Server send OFF" closes the WebSocket with 1000. The source keeps running for the local view.
6. **The cognitive selector stops gating uploads in this mode.** Its score is still sent per frame
   (`change_score` in meta), so the server can prioritise or show it. Pipeline bypass has no
   meaning in video mode; the UI hides it.
7. **Status.**
   - `/edge-api/status` adds `transport`, `uplink_kbps`, `uplink_fps`, `dropped_frames`,
     `reconnects`, and the server's `stats` (rx, analytics fps, lag).
   - The Mission Control page shows them. The page is in `DVAS_Dashboard/frontend/src/pages/edge/`,
     owned by the edge team.
8. **Fallback.** The transport is auto-selected:
   - `h264_ws`;
   - on 404 or 5 failures → `jpeg_http` (Phase E1 path, `RealVideoSource` as today);
   - the WebSocket is retried every 60 s.

**Done when:** against the pod, for 30 minutes, with one DJI 4K file and one RTSP source:
- the server reports `rx_fps` = source fps;
- the live view in the dashboard runs at 25–30 fps;
- `analytics_fps` ≥ 20;
- edge CPU ≤ 25 % in copy mode;
- reconnect after pulling the network cable for 20 s works without a manual action, and `seq` has no
  duplicates.

### Phase E3 — Remove per-frame JPEG work from the edge (2–3 days)

1. **E-F5 PacketRing replaces the JPEG rolling buffer.**
   - It keeps the last `buffer.duration_s` (60 s) of *compressed* packets, with their PTS, `seq`,
     `capture_ts` and telemetry.
   - Memory: 60 s × 6 Mbps ≈ 45 MB at 1080p, ≈ 150 MB at 4K / 20 Mbps.
   - `extract_evidence_clip` remuxes from the ring to MP4 (`-c copy`, starting at the keyframe before
     the requested start). There is no decode and no re-encode, so the evidence keeps the source
     quality. Hash the clip as today.
   - The old `RollingFrameBuffer` stays only for the `jpeg_http` transport.
2. **E-F9 Local preview.** MJPEG at ≤ 10 fps and 960 px, encoded only while at least one client
   is connected to `/stream/live`.
3. **Change detection** runs on the LocalDecoder output (10 fps, 960 px). Re-tune
   `min_contour_area` for 960 px; it is currently tuned for 4K (2000 px² → about 125 px²). Add a
   test that compares event counts on the reference clip before and after.

**Done when:**
- edge CPU at 4K30 copy mode with change detection on is ≤ 35 % (record the before/after numbers
  here);
- an evidence clip around an event is byte-for-byte a remux of the source (`ffprobe` shows the same
  codec and resolution).

### Phase E4 — Packaging and settings (1 day)

- **Bundling.** `ffmpeg.exe` / `ffprobe.exe` become mandatory in `build_edge.ps1` (today: "if
  available"). Pin their version, verify their SHA-256, and include PyAV.
- **`edge.env.template` / settings:**
  - `transmission.transport` (`h264_ws` | `jpeg_http`)
  - `video.max_kbps` (default 8000)
  - `video.max_resolution` (`original` | `1080p` | `720p`; used only when transcoding)
  - `video.encoder` (`auto` | `nvenc` | `qsv` | `amf` | `x264`)
  - `video.local_fps`
  - `video.local_width`
- **Bandwidth test.** "Test connection" (`edge_api_server.py:1555`) adds a 10-second upload test
  over the WebSocket. It recommends `max_kbps` and warns when the uplink is below what the source
  needs.

### Phase E5 — Field validation (1–2 days)

- **Network impairment.** Test with clumsy (Windows): 200 ms RTT, 1 % loss, a 20 s outage, and a
  3 Mbps cap. Expect a live view with GOP drops but no stall over 3 s; recovery without operator
  action; no duplicate `seq`.
- **Two laptops, two drones at once.** Streams stay separate per mission (Phase 2a keys).
- **A 2-hour run.** Watch memory (the ring stays bounded) and file handles.

---

## 5. Settings and flags summary

| Setting | Default | Phase |
|---|---|---|
| `transmission.transport` | `h264_ws` (auto-falls back to `jpeg_http`) | E2 |
| `transmission.max_resolution` / `jpeg_quality` | `1080p` / 80 (JPEG path only) | E1 |
| `transmission.max_inflight` | auto (2–24 from RTT) | E1 |
| `video.max_kbps` | 8000 | E2 |
| `video.encoder` | `auto` | E2 |
| `video.local_fps` / `video.local_width` | 10 / 960 | E2–E3 |
| `buffer.mode` | `packets` (`jpeg` with the JPEG transport) | E3 |

## 6. Coordination with the RunPod developer

| Edge step | Needs from RunPod | RunPod phase |
|---|---|---|
| E1 | Nothing new (same `/ingest/frame`). Optional: the server's Late/Lost counters to verify. | — |
| E2 | `/ingest/video/ws` live on a test pod, with the `stats` messages. | R1 |
| E2 testing | A pod image with `DVAS_VIDEO_INGEST=1`, and the dashboard live player. | R1, R2 |
| E3 | None. | — |

- Agree on the §3 contract **before** either side codes against it.
- Any change to it bumps `"v"`. The server accepts v1 for the whole v2 release.

## 7. Risks

| Risk | Mitigation |
|---|---|
| PyAV wheel lacks a needed encoder on Windows | E0 decides; `ffmpeg.exe` subprocess path. |
| Laptop uplink < source bitrate (4K at 20 Mbps over 4G) | Transcode to 1080p at `max_kbps`. The bandwidth test warns before take-off. Analytics still get 20 fps at 1080p. |
| RTSP source allows one session only | A single reader feeds the uplink, the ring and the local decoder (§2). |
| Corporate proxy blocks WebSockets | Auto-fallback to the JPEG path, shown in the UI. |
| Drone stream carries B-frames | Copy mode keeps them (allowed). The server tolerates them; latency rises by a frame or two. |
