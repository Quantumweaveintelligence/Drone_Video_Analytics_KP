# Edge multi-drone test tool — requirements (v1, 2026-11-13)

**For:** the edge developer. **Owner on the server side:** DVAS RunPod team.
**Goal:** from **one laptop** and **one operator account**, stream **20 simulated drones at once**
(20 missions, real recorded drone videos) into the DVAS pod for **15 minutes**, so the server can
be measured with real footage instead of test patterns.

The words **MUST**, **MUST NOT** and **SHOULD** are binding. A run that breaks a MUST is not a
valid test, whatever the server shows. Every requirement has an ID; refer to it in questions and
reports.

The protocol is the **uplink contract v1** (`docs/v2_runpod_implementation_plan.md` §3). This
document does not change it; it adds what a 20-drone test tool must do on top of it. A working
reference sender for one drone is `deploy/runpod/tools/send_ts.py`; `bench_capacity.py` runs N of
them. Port their behaviour; do not invent a different protocol.

---

## P — Server prerequisites (DVAS team, before the first run)

The edge developer does not do these, but the test cannot pass without them.

| ID | Requirement |
|---|---|
| P1 | The pod runs image `20261113-130ae29` or later. |
| P2 | `DVAS_MAX_LIVE_STREAMS=20` is set in the pod's environment (default is 8; the 9th stream is refused with close code `1013 too_many_streams`). |
| P3 | nginx allows ≥ 20 simultaneous video WebSockets per token. **Today it allows 4** (`limit_conn video_conn 4` in `deploy/runpod/etc/nginx.conf`); the 5th gets HTTP 429. Until this is raised, one account can stream only 4 drones. |
| P4 | Free space on `/workspace` ≥ 1.2 × (sum of all stream bitrates × run time). 20 × 8 Mbps × 15 min ≈ 18 GB → at least 22 GB free. At 90 % full, recording stops by design. |
| P5 | One operator account with the `drone-operator` role, used for all 20 missions. |
| P6 | Test 1 runs with **every analytics service off** (Detection Models page); test 2 with the services the DVAS team names. |

---

## C — Configuration

| ID | Requirement |
|---|---|
| C1 | One config file (JSON or YAML) MUST describe a run: server base URL, laptop ID, run duration, start stagger, and one entry per drone: `drone_id`, video file, start offset in the file (s), telemetry start point (lat, lon, altitude). |
| C2 | The number of drones MUST be configurable from 1 to 20 without code changes. |
| C3 | The password MUST be entered at a prompt at start (hidden input) and kept only in memory. It MUST NOT be in the config file, the command line, environment variables saved to disk, or any log. |
| C4 | The tool MUST print the resolved settings (without secrets) before connecting and ask for confirmation, unless `--yes` is given. |

---

## V — Video files

| ID | Requirement |
|---|---|
| V1 | Codec H.264 (AVC), Baseline / Main / High. HEVC MUST be converted to H.264 **before** the run (offline), never during it. |
| V2 | Resolution ≤ 3840 × 2160; frame rate ≤ 60 fps (30 fps recommended); constant frame rate. |
| V3 | Keyframe (IDR) interval ≤ 2 s; **1 s recommended**. Every keyframe MUST carry SPS/PPS in-band. |
| V4 | No B-frames (re-encode once with `-bf 0` if the source has them). |
| V5 | Bitrate MUST stay ≤ 12 000 kbps over every 5-second window (the server closes with `4413` above it). Targets: 4K ≤ 8 000 kbps, 1080p ≤ 4 000 kbps. |
| V6 | Real drone footage, ≥ 60 s long each. 20 different files SHOULD be used; if fewer exist, the same file MAY be reused with different start offsets (C1) so no two drones show the same frame at the same time. |
| V7 | Audio is ignored by the server and SHOULD be stripped. |
| V8 | The tool MUST provide `--check`: it opens every configured file and fails (non-zero exit, one line per problem) if any of V1–V5 is violated, reporting the measured codec, profile, resolution, fps, largest keyframe gap and peak 5-second bitrate. A run MUST NOT start with files that fail `--check`. |

Preparing a file once, offline (example — 4K, 1 s keyframes, no B-frames, capped bitrate):

```
ffmpeg -i input.mp4 -an -c:v libx264 -preset medium -profile:v high -g 30 -keyint_min 30 \
  -sc_threshold 0 -bf 0 -b:v 7000k -maxrate 8000k -bufsize 8000k -pix_fmt yuv420p out.mp4
```

---

## A — Authentication

| ID | Requirement |
|---|---|
| A1 | Log in the same way the edge app does today (Keycloak, client `drone-ingest-client`), once, as the P5 account. |
| A2 | Access tokens live about **5 minutes**. The tool MUST keep a valid token for the whole run: refresh with the refresh token before 70 % of the access token's lifetime has passed, and log in again (prompting) only if the refresh fails. |
| A3 | Every HTTP call (`mission/start`, `mission/end`) and every WebSocket (re)connect MUST use the current, unexpired token in `Authorization: Bearer …`. (The server checks the token when a stream connects; a stream that is already running is not cut when the token expires, but its next reconnect and its `mission/end` need a fresh one.) |
| A4 | On close code `4401` the tool MUST refresh the token and reconnect (R2). |
| A5 | Tokens MUST NOT appear in logs, reports or crash dumps (log at most the first 8 characters). |

---

## M — Missions and identities

| ID | Requirement |
|---|---|
| M1 | Each simulated drone MUST have its own `drone_id`, unique within the run and used by no real drone. Use `DRN-T01` … `DRN-T20`. Allowed characters: `A–Z a–z 0–9 _ . : -`, ≤ 64. Two streams MUST NOT share a `drone_id`: a new mission on a drone ends that drone's previous mission. |
| M2 | Each mission MUST have its own `mission_id`, new for every run: `MSN-T<run-id>-<NN>`, where `<run-id>` is the start time `YYYYMMDD-HHMMSS`. Allowed: `A–Z a–z 0–9 _ . : -` and space, ≤ 80. A `mission_id` MUST NOT be reused across runs (the server answers `409 mission_id_taken`). |
| M3 | All 20 missions are started by the same account (P5). |
| M4 | Per drone, in this order: `POST /ingest/mission/start` → open the WebSocket → send `hello` → wait for `ready` → stream → send `bye` → `POST /ingest/mission/end`. |
| M5 | Drones MUST start one after another, 0.5–1 s apart (configurable), not all in the same instant. |
| M6 | On `409` from `mission/start` the tool MUST stop that drone and report the server's `detail`; it MUST NOT retry with a modified ID on its own. |
| M7 | At the end of the run — normal end, Ctrl-C, or a fatal error — the tool MUST send `bye` and call `mission/end` for **every** drone it started, within 15 s, with a valid token (A2). |
| M8 | The tool MUST provide `--cleanup <run-id>`, which ends every mission of that run that is still open (after a crash or power loss). |

---

## S — Streaming (uplink contract v1, §3)

| ID | Requirement |
|---|---|
| S1 | Endpoint `wss://<pod>-80.proxy.runpod.net/ingest/video/ws?drone_id=<id>&mission_id=<id>`, one WebSocket per drone. At most one open connection per mission at any time. |
| S2 | First message: `hello` with `v:1`, `drone_id`, `mission_id`, `laptop_id`, `codec:"h264"`, `container:"mpegts"`, `width`, `height`, `fps`, `source:"file"`, `next_seq`. Nothing else is sent before `ready` arrives. |
| S3 | Video is sent as binary messages of MPEG-TS: each message a whole number of 188-byte packets, ≤ 256 KiB. The tool MUST remux (container change only), MUST NOT decode or re-encode at run time. |
| S4 | Every connection MUST start with a keyframe preceded by SPS/PPS. |
| S5 | One `meta` per video frame, sent **before** the binary chunk that carries that frame; or `meta_batch` with ≤ 10 items, none older than 200 ms. Fields: `seq`, `pts_ms`, `capture_ts`, `key`, `change_score`, `tel`. |
| S6 | `seq` starts at 1 per mission, increases by 1 per frame, never repeats, and continues across reconnects and file loops. |
| S7 | PTS (`pts_ms` and the TS timestamps) MUST increase monotonically within a connection. When a file loops, the next pass continues after the last PTS of the previous one (offset = last PTS + one frame duration); it MUST NOT restart at 0. |
| S8 | **Real-time pacing:** frame *n* is sent no earlier than `t_start + (pts_n − pts_first)` (wall clock) and never more than 0.5 s ahead of it. Sending faster than real time, or bursting after a pause, is not allowed. |
| S9 | `capture_ts` = the laptop's wall clock (epoch seconds, UTC, ms precision) when the frame is due to be sent (S8). The laptop clock MUST be NTP-synchronised and within 1 s of true time before the run (the tool SHOULD check and refuse to start if it cannot confirm this). |
| S10 | `tel` per frame, with the field names of today's `/ingest/frame` metadata. Each drone gets its own plausible track: start at its configured point (C1), move a few metres per second, battery falls slowly; values that are not simulated are `null`. No two drones at the same position. |
| S11 | Send a WebSocket ping every 15 s on every connection. |
| S12 | Files loop until the run time (C1) is reached, then the drone stops at the next frame boundary and follows M7. |

---

## R — Server messages, errors, reconnects

| ID | Requirement |
|---|---|
| R1 | Read and log every server message: `ready`, `stats` (about every 2 s: `rx_fps`, `rx_kbps`, `analytics_fps`, `last_seq`, …), `slow_down`, `error`. |
| R2 | Act on close codes exactly as the contract's table says: `4401` refresh token + reconnect; `4403` stop that drone; `4409` call `mission/start` once, then reconnect, else stop; `4413` stop that drone and report (the file breaks V5 — the run is invalid); `4415` stop and report; `1011` back off 0.5 → 1 → 2 → 4 → 8 s and reconnect; `1013` wait 30 s and reconnect; `1003`/`1008`/`1009` stop and report as a tool bug, never retry in a loop. |
| R3 | A reconnect resumes the same mission: `hello.next_seq` = the server's `last_seq` + 1 (from `ready`), and streaming restarts at the next keyframe at or after that frame. |
| R4 | `slow_down` is logged with its `max_kbps`; for this test the tool does not change bitrate (V5 already caps it). |
| R5 | A drone that needed more than 3 reconnects in a run, or was refused, is marked failed in the report (L3) and the run continues with the others. |

---

## N — Laptop and network

| ID | Requirement |
|---|---|
| N1 | Wired Ethernet; no Wi-Fi, no VPN, no other large uploads during the run. |
| N2 | Measured **upload** bandwidth MUST be ≥ 1.3 × the sum of all stream bitrates: 20 × 8 Mbps (4K) needs ≥ 210 Mbps up; 20 × 4 Mbps (1080p) needs ≥ 105 Mbps. Measure before each test day and record the value in the report. If the uplink is smaller, use 1080p files or fewer drones — do not exceed it. |
| N3 | The tool's own CPU use SHOULD stay below 50 % of the laptop for 20 drones (it only remuxes and sends). Record CPU and memory use every 5 s (L1). |
| N4 | Laptop on mains power, sleep and screen lock disabled for the run. |

---

## L — Logging and report

| ID | Requirement |
|---|---|
| L1 | Every 5 s, one CSV row per drone: time (UTC), drone_id, state (connecting / streaming / reconnecting / stopped / failed), frames sent, seconds of video sent, send fps, send kbps, last server `rx_fps`, last server `last_seq`, reconnect count, last close code, laptop CPU %, laptop memory. |
| L2 | A text log of every connect, `ready`, error message, close (with code and reason), reconnect, token refresh (without the token), `mission/start` and `mission/end` response (status + body). |
| L3 | At the end, a summary table per drone: frames sent, median send fps, median server `rx_fps`, reconnects, close codes seen, `mission/end` status, pass / fail by T2–T3. Plus the run settings, the N2 upload measurement and the laptop model. |
| L4 | Files are named by run ID (M2) and kept; the CSV, log and summary are sent to the DVAS team after every run. |

---

## X — Not allowed

| ID | |
|---|---|
| X1 | Decoding or re-encoding video while streaming (S3). |
| X2 | Sending faster than real time, or several drones' frames in one message. |
| X3 | Sharing a `drone_id` or `mission_id` between streams, or reusing a `mission_id` from an earlier run. |
| X4 | Leaving missions open after the run (M7, M8). |
| X5 | Passwords or tokens on disk, on the command line or in logs (C3, A5). |
| X6 | Retrying in a loop on `1003`, `1008`, `1009`, `4403`, `4413`, `4415`. |

---

## T — Acceptance: the test is successful when

Measured over a **15-minute** run with 20 drones, after P1–P6.

| ID | Criterion | Checked by |
|---|---|---|
| T1 | All 20 drones receive `ready` within 60 s of the first `mission/start`. | tool (L2) |
| T2 | Per drone, frames sent = run time × source fps (± 1 %), and the median server `rx_fps` ≥ 98 % of the source fps. | tool (L3) |
| T3 | No close with `1003`, `1008`, `1009`, `4403`, `4413`, `4415`; at most 1 reconnect per drone. | tool (L3) |
| T4 | All 20 `mission/end` calls return 2xx. | tool (L3) |
| T5 | Server side: ingest receives ≥ 98 % of the source fps per drone, live relay restarts 0, ingest event-loop stall < 0.5 s, recordings complete (frames recorded = frames sent). | DVAS team |
| T6 | The Multi-Drone View shows 20 live tiles for the whole run, none dropping out. | DVAS team |

---

## D — Deliverables

| ID | |
|---|---|
| D1 | The tool (executable or script) and its source, in the edge repository. |
| D2 | A README: install, config file format with a 3-drone example, `--check`, a run, `--cleanup`, where the reports go. |
| D3 | A sample config for 20 drones (`DRN-T01`…`DRN-T20`). |
| D4 | The `--check` output and the L3 summary of one 1-drone and one 4-drone dry run against the pod, before the 20-drone test. |
