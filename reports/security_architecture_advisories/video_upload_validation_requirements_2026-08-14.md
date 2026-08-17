# Video Upload Validation Requirements & Engineering Advisory

**Date & Time:** 2026-08-14 11:30:00 AM IST (+05:30)  
**Project:** Kerala Police Drone Video Analytics System (DVAS)  
**Target Systems:** `dvas_ingest`, `dvas_edge`, `DVAS_Dashboard`  

---

## 1. Executive Purpose & The Risk of Extension-Only Checking

In video processing pipelines, relying solely on file extensions (such as `.mp4`) or client-supplied **MIME (Multipurpose Internet Mail Extensions)** headers (such as `Content-Type: video/mp4`) is a critical security vulnerability. 

### Why Extension & Header Checking Alone Fails:
1. **Client-Controlled Headers:** The `Content-Type` header sent during an **HTTP (Hypertext Transfer Protocol)** multipart upload is entirely controlled by the client. An attacker using tools like `curl` can upload an executable file, a malicious shell script, or a compressed archive while declaring `Content-Type: video/mp4`.
2. **Parser Exploits in Downstream Decoders:** Video files are deeply structured binary files composed of multiple containers, tracks, metadata atoms, and codecs. Malformed or deliberately crafted video files can exploit buffer overflows, integer overflows, or memory corruption vulnerabilities in underlying decoders and demuxers (such as **FFmpeg**, **GStreamer**, or **OpenCV**) running on **GPU (Graphics Processing Unit)** and **CPU (Central Processing Unit)** worker nodes.
3. **Decompression & Resource Exhaustion Bombs:** Specially crafted video files can claim small file sizes on disk but require massive amounts of memory upon decoding, causing **DoS (Denial of Service)** crashes across analytics workers.
4. **Server-Side Request Forgery (SSRF) via Playlists:** Formats supporting playlists or segment references (such as **HLS — HTTP Live Streaming** or **DASH — Dynamic Adaptive Streaming over HTTP**) can contain embedded URLs targeting internal services (e.g., `http://127.0.0.1:8080/` or internal cloud metadata endpoints).
5. **Path Traversal & Overwrite Attacks:** Using the original client-provided filename (such as `../../etc/cron.d/job.mp4`) can lead to unauthorized file overwrites if stored directly to disk.

---

## 2. The 7-Stage Defense-in-Depth Video Validation Pipeline

To ensure that only legitimate, structurally valid, and safe drone surveillance footage enters the DVAS pipeline, uploaded video files must pass through a strict **7-Stage Validation Pipeline** before being stored or handed to GPU analytics:

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             7-STAGE VIDEO VALIDATION PIPELINE                                    │
├────────────────────────┬─────────────────────────────────────────────────────────────────────────┤
│ Stage 1: Transport Gate│ • Enforce Authentication (JWT) before buffering data.                   │
│                        │ • Hard HTTP request body size limits enforced at NGINX.                 │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
│ Stage 2: Magic Bytes   │ • Inspect leading binary header bytes for ISO Base Media signatures.     │
│                        │ • Reject non-matching binary structures immediately.                    │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
│ Stage 3: Codec / Demux │ • Sandboxed container parsing (probe without network access).           │
│                        │ • Allowlist strictly H.264 / AVC and H.265 / HEVC video codecs.         │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
│ Stage 4: Dimension Sanity│ • Bounded resolution (e.g., 720p to 4K), frame rate (10–60 FPS), and   │
│                        │   maximum duration (e.g., 10 minutes per clip).                         │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
│ Stage 5: Sanitization  │ • Strip unnecessary EXIF, user comments, and non-essential atoms.       │
│                        │ • Validate drone flight metadata schema strictly.                       │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
│ Stage 6: Normalization │ • Re-mux or transcode video to an internal standardized profile.        │
│                        │ • Neutralizes hidden payloads and malformed container structures.       │
├────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
│ Stage 7: Evidence Chain│ • Compute SHA-256 cryptographic hash immediately upon receipt.          │
│                        │ • Store under random UUIDv4 filename with WORM (Write Once Read Many).  │
└────────────────────────┴─────────────────────────────────────────────────────────────────────────┘
```

---

### Stage 1: Pre-Upload & Transport Layer Gate (NGINX Proxy Layer)
* **Pre-Buffering Authentication:** Require a valid **JWT (JSON Web Tokens)** in the `Authorization` header before NGINX or FastAPI allocates memory or disk to buffer the upload. Unauthenticated uploads must be rejected at the proxy layer with `401 Unauthorized`.
* **Strict Payload Body Limits:** Enforce maximum request size at the NGINX level:
  * Single Frame Ingest (`/ingest/frame`): Maximum **10 Megabytes (MB)**.
  * Evidence Video Clip (`/ingest/evidence`): Maximum **500 Megabytes (MB)**.
* **Streaming Consumption:** Ingest handlers should stream files directly to temporary storage rather than loading the entire multi-hundred-megabyte payload into system memory at once.

> ⚠️ **Critical Dependency on Finding #1 (`SKIP_AUTH=true` Fix):**  
> Transport and upload authentication is **only as strong as the underlying token validation logic**. If `SKIP_AUTH: "true"` remains active in `dvas_ingest/docker-compose.yml`, any dummy Bearer token will pass inspection, rendering Stage 1 completely bypassed on day one. **Disabling `SKIP_AUTH` is an absolute prerequisite** before deploying this video validation pipeline.

---

### Stage 2: Binary File Signature Verification (Magic Bytes Check)
* **Inspect the File Header:** Before invoking complex video decoders, read the first **32 bytes** of the file buffer to verify the binary file signature.
* **ISO Base Media File Format (ISO/IEC 14496-12) Verification:** For MP4 video files, verify the presence of the `ftyp` (File Type Box) atom and validate that the major/compatible brands match standard MP4 specifications:
  * Standard Brand Identifiers: `isom` (ISO Base Media), `mp41`, `mp42`, `avc1`, or `dash`.
* **Immediate Rejection:** If the extension is `.mp4` but the leading bytes indicate a ZIP archive, an executable, a script, or an image format, reject the upload immediately without passing it downstream.

---

### Stage 3: Container Demuxing & Codec Allowlisting
* **Isolated / Sandboxed Metadata Probing:** Use a sandboxed media probe tool (such as **FFprobe** or **PyAV**) with network protocols strictly disabled (`-protocol_whitelist "file,crypto"`). This prevents malicious videos from triggering **SSRF (Server-Side Request Forgery)** through external network playlist URLs.
* **Strict Codec Allowlist:**
  * **Video Codecs Permitted:** Only **H.264 / AVC (Advanced Video Coding)** or **H.265 / HEVC (High Efficiency Video Coding)**.
  * **Audio Codecs Permitted:** **AAC (Advanced Audio Coding)**, or strictly **No Audio** (drone operational footage rarely requires audio).
  * **Rejected Streams:** Reject any video containing executable data streams, subtitle tracks with script execution tags, or unapproved legacy codecs.

---

### Stage 4: Dimensional, Temporal & Resource Sanity Bounds
To prevent resource exhaustion attacks against CPU and GPU analytics workers, enforce explicit boundaries on video dimensions and playback metrics:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                             VIDEO METRIC BOUNDARIES                                    │
├───────────────────────────┬────────────────────────────────────────────────────────────┤
│ Metric Parameter          │ Allowed Operational Range for DVAS                         │
├───────────────────────────┼────────────────────────────────────────────────────────────┤
│ Resolution (Width x Height│ • Minimum: 1280 x 720 (720p HD)                            │
│                           │ • Maximum: 3840 x 2160 (4K UHD)                            │
├───────────────────────────┼────────────────────────────────────────────────────────────┤
│ Frame Rate (FPS)          │ • Minimum: 10 FPS (Frames Per Second)                      │
│                           │ • Maximum: 60 FPS (Frames Per Second)                      │
├───────────────────────────┼────────────────────────────────────────────────────────────┤
│ Maximum Duration          │ • Evidence Clips: 10 minutes maximum per upload.           │
│                           │ • Long recordings must be split into chunks.               │
├───────────────────────────┼────────────────────────────────────────────────────────────┤
│ Color Space & Pixel Format│ • Standard YUV420p (8-bit planar).                         │
│                           │ • Reject non-standard color spaces designed to crash GPUs. │
└───────────────────────────┴────────────────────────────────────────────────────────────┘
```

---

### Stage 5: Metadata Stripping & Sanitization
* **Strip Unverified Metadata:** Drone video containers often carry **EXIF (Exchangeable Image File Format)** metadata, camera user comments, and custom vendor tags. Strip all arbitrary user-defined text fields to neutralize injection attacks targeting metadata extraction libraries.
* **Schema Validation for Flight Telemetry:** If telemetry data (such as GPS coordinates, altitude, drone speed, and gimbal angles) is provided in an accompanying **JSON (JavaScript Object Notation)** sidecar, validate it strictly against a strongly typed schema. Reject strings containing control characters, SQL injection keywords, or out-of-range latitude/longitude coordinates.

---

### Stage 6: Normalization & Re-Muxing (Neutralization)
* **Re-Muxing / Transcoding into Clean Container:** Before saving the video as permanent evidence or passing it to the **AI (Artificial Intelligence)** analytics models, process the file through a fast re-muxing pass.
* **Why this is advised:** Re-muxing copies raw video packets into a newly generated container structure. This discards malformed atom structures, hidden binary payloads, and trailing garbage bytes without degrading the original video quality.

---

### Stage 7: Storage Hardening & Evidence Chain of Custody
* **Cryptographic Hash Computation (SHA-256):** Compute a **SHA-256 (Secure Hash Algorithm 256-bit)** checksum immediately upon ingest. Store this hash in the database alongside the evidence record to ensure mathematical proof of chain of custody and tamper resistance for legal proceedings.
* **Safe Filename Generation (UUIDv4):** Never use the original filename provided by the client (such as `DJI_001.MP4`). Generate a cryptographically random **UUIDv4 (Universally Unique Identifier Version 4)** filename (e.g., `550e8400-e29b-41d4-a716-446655440000.mp4`).
* **Storage Immutability (WORM):** Store finalized video evidence in object storage (**MinIO** or **S3 — Simple Storage Service**) configured with **WORM (Write Once, Read Many)** / **Object Lock** policies to prevent accidental or malicious modification and deletion.

---

## 3. 📋 Video Ingest Engineering Checklist

When implementing and reviewing video upload handlers:

- [ ] **Prerequisite Gated:** Confirms `SKIP_AUTH: "false"` is enforced so JWT token authentication is actively verified.
- [ ] **Transport Auth Check:** Ingest endpoint validates JWT authentication before buffering the file.
- [ ] **Transport Size Limit:** Reverse proxy enforces body size limit (e.g., 10 MB for frames, 500 MB for clips).
- [ ] **Magic Bytes Validation:** Inspects initial bytes for `ftyp` / ISO Base Media signatures before invoking decoders.
- [ ] **Disabled Network in Probes:** Media probing tools disable all network and external playlist protocols to prevent SSRF.
- [ ] **Codec Allowlist:** Validates that video stream is strictly H.264 or H.265.
- [ ] **Dimension Bounds:** Enforces resolution (720p to 4K), frame rate (10 to 60 FPS), and duration constraints.
- [ ] **Metadata Sanitization:** Strips unverified comments/metadata; validates GPS telemetry against strict data types.
- [ ] **Randomized Storage Names:** Stores files using random UUIDv4 names, completely discarding original client filenames.
- [ ] **Cryptographic Hash Computed:** Calculates SHA-256 checksum upon ingest for evidence integrity and chain of custody.
- [ ] **WORM Storage Locking:** Uploads permanent evidence clips to object storage with immutability locks enabled.
