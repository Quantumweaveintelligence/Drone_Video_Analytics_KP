# Drone Video Analytics System (DVAS)
## Production Implementation Roadmap — Two-Member Team
### Quantumweave Intelligence × Kerala Police Department

---

> **Document Type:** Production Engineering Roadmap  
> **Team Size:** 2 Engineers  
> **Total Estimated Duration:** 22 Months  
> **Methodology:** Iterative delivery with phase gates; each phase produces a deployable, testable artifact

---

## Team Role Assignment

| Role | Label | Primary Responsibilities |
|---|---|---|
| Engineer A | **BE/Infra** | Backend services, infrastructure, databases, auth, streaming pipeline, DevOps |
| Engineer B | **ML/FE** | ML models, GPU analytics, edge processing algorithms, dashboards, frontend |

> Both engineers contribute to integration, testing, and documentation. Ownership labels indicate *primary* accountability, not exclusivity.

---

## System Module Map (Reference)

The document defines 9 production modules that must be fully built:

```
[M1] Drone Communication Interface
[M2] Edge Processing Layer (Operator Laptop)
[M3] Central Ingest & Streaming Server
[M4] GPU Analytics Cluster
[M5] Evidence Object Storage
[M6] Structured Databases & Audit Systems
[M7] Authentication & Access Control (RBAC + ABAC)
[M8] Dashboards (Edge / Ops / Central Command)
[M9] Legal Compliance & Chain of Custody Engine
```

---

## Phase 0 — Foundation & Environment Setup
### Duration: Weeks 1–3

This phase establishes the shared development environment, repository structure, hardware provisioning, and inter-engineer conventions. No feature code is written yet — only the scaffold that all future work depends on.

---

### Week 1 — Repository, Conventions & Hardware

**BE/Infra:**
- Provision on-premise server hardware (GPU cluster node, ingest server, storage server) or staging equivalents
- Install Ubuntu Server 22.04 LTS on all nodes
- Set up private GitLab or Gitea instance for source control (no cloud SaaS)
- Configure SSH key-based access across all machines
- Create monorepo structure:
  ```
  /dvas
    /edge          ← M2
    /ingest        ← M3
    /analytics     ← M4
    /storage       ← M5
    /database      ← M6
    /auth          ← M7
    /dashboards    ← M8
    /compliance    ← M9
    /infra         ← Terraform/Ansible configs
    /docs
    /tests
  ```
- Install Docker Engine + Docker Compose on all nodes
- Set up private Docker registry (Harbor or Gitea Packages)

**ML/FE:**
- Install CUDA Toolkit 12.x + cuDNN on GPU node
- Validate GPU access: `nvidia-smi`, run a basic PyTorch CUDA tensor operation
- Set up Python 3.11 virtual environments with `pyproject.toml` (poetry)
- Install Node.js 20 LTS for dashboard development
- Establish shared `.env` convention: no secrets in git, use a secrets template file
- Set up pre-commit hooks: `black`, `ruff`, `mypy` for Python; `eslint`, `prettier` for JS

**Joint:**
- Define API contract format: all internal APIs use OpenAPI 3.1 spec, stored in `/docs/api/`
- Agree on message format for inter-service communication (use Protocol Buffers or JSON with strict schema)
- Set up a shared internal wiki (Outline or Notion self-hosted)
- Define Git branching strategy: `main` → protected; `dev` → integration; feature branches per module

---

### Week 2 — Networking, Secrets & Observability Scaffold

**BE/Infra:**
- Configure internal network VLANs:
  - VLAN 10: Field (drone operator laptops)
  - VLAN 20: Ingest servers
  - VLAN 30: GPU analytics cluster
  - VLAN 40: Storage and databases
  - VLAN 50: Dashboards and user access
- Set up WireGuard VPN for field-to-HQ encrypted tunnel
- Deploy Vault (HashiCorp) for secrets management — all credentials, API keys, certs stored here
- Configure internal DNS (CoreDNS) so services refer to each other by name, not IP
- Deploy Prometheus + Grafana stack for observability (all future services will emit metrics here)
- Deploy Loki for centralized log aggregation

**ML/FE:**
- Set up Jupyter Lab environment on GPU node for model experimentation
- Download and evaluate base model candidates:
  - YOLOv8n / YOLOv9 for object detection
  - ByteTrack / StrongSORT for tracking
  - InsightFace / DeepFace for face detection
  - CSRNet / DM-Count for crowd density
  - LPRNet / OpenALPR for number plate
- Document each model's input/output format, inference speed on target GPU, licensing

**Joint:**
- Write a single-page Architecture Decision Record (ADR) for each major technology choice
- Finalize tech stack decisions (do not revisit these after Phase 0):

| Layer | Technology |
|---|---|
| Edge runtime | Python 3.11, OpenCV, PyTorch |
| Ingest streaming | Apache Kafka 3.x |
| GPU analytics | PyTorch 2.x + TorchServe or Triton Inference Server |
| Object storage | MinIO (S3-compatible) |
| Relational DB | PostgreSQL 16 |
| Time-series DB | TimescaleDB (PostgreSQL extension) |
| Search index | OpenSearch 2.x |
| Audit log | Append-only PostgreSQL table + Loki |
| Auth | Keycloak 24 (OAuth2 / OIDC) |
| Dashboard frontend | React 18 + TypeScript + Leaflet (maps) |
| Dashboard backend | FastAPI (Python) |
| Container orchestration | Docker Compose (Phase 1-2); migrate to K3s in Phase 3 |
| IaC | Ansible for provisioning, Docker Compose for services |

---

### Week 3 — CI/CD, Testing Framework & Phase Gate

**BE/Infra:**
- Set up Gitea Actions (or GitLab CI) pipelines:
  - On every push: lint → unit tests → build Docker image
  - On merge to `dev`: integration tests → push image to private registry
  - On merge to `main`: tag release → deploy to staging
- Write Ansible playbooks for:
  - Node provisioning (OS packages, CUDA, Docker)
  - Service deployment (pull images, start compose stacks)
  - Rolling updates

**ML/FE:**
- Establish model benchmarking harness: given a video clip, output detection latency, FPS, and accuracy metrics
- Record baseline numbers for each candidate model on target GPU
- Create synthetic test dataset from public drone footage (VisDrone dataset is freely available) for early model testing

**Joint:**
- Conduct Phase 0 gate review:
  - ✅ All machines provisioned and networked
  - ✅ CI/CD pipeline runs green on a "hello world" service
  - ✅ Model baselines documented
  - ✅ Tech stack finalized and ADRs written
  - ✅ Secrets management live

---

## Phase 1 — Edge Processing Layer (M2)
### Duration: Weeks 4–10

This is the first module that produces real video data. The operator laptop software is a standalone Python application that runs on a field laptop, connected to the drone's video output.

---

### Week 4–5 — Video Reception & Rolling Buffer

**ML/FE (primary):**

**Task 1.1 — Drone Video Input Abstraction**
- Write a `DroneVideoSource` class that accepts:
  - RTSP stream URL (most drones output RTSP)
  - USB/HDMI capture card input (fallback)
- Use OpenCV `VideoCapture` backend with GStreamer pipeline for low-latency decoding
- Handle reconnection automatically on stream drop (exponential backoff)
- Emit heartbeat metrics (frame rate, resolution, latency) to Prometheus

**Task 1.2 — Rolling Buffer Implementation**
- Implement a `RollingFrameBuffer` class:
  - Fixed-duration window: configurable, default 60 seconds
  - Storage: circular buffer backed by memory-mapped files on local SSD (not RAM — avoid OOM on long ops)
  - Each entry: `(frame_index, utc_timestamp_ms, frame_bytes_compressed)`
  - Compression: use JPEG at 85% quality for buffer frames (balance size vs. quality)
  - Thread-safe: producer (decoder) and consumer (change detection) run in separate threads
- Implement `extract_segment(start_ts, end_ts) → List[Frame]` for evidence clip extraction
- Write unit tests: buffer overflow behavior, timestamp accuracy, concurrent read/write

**BE/Infra:**
- Set up laptop provisioning Ansible playbook (for field deployment): install Python, OpenCV, CUDA lite
- Create systemd service unit for the edge application (auto-start on boot, restart on crash)
- Set up local SSD partition for buffer storage with proper mount and permissions

---

### Week 6–7 — Change Detection Engine

**ML/FE (primary):**

**Task 1.3 — Change Detection Pipeline**
- Implement a two-stage change detection approach:

  **Stage 1 — Frame Differencing (CPU, fast):**
  - Convert incoming frames to grayscale
  - Compute absolute pixel difference between current frame and a rolling background model
  - Background model: Gaussian Mixture Model via OpenCV `createBackgroundSubtractorMOG2`
  - Threshold the difference map → binary mask
  - Compute motion score = percentage of changed pixels weighted by region

  **Stage 2 — Region-of-Change Scoring:**
  - Identify contiguous changed regions using `cv2.findContours`
  - Filter out small blobs (noise, camera vibration) below a minimum area threshold
  - Score each region by: area, motion magnitude, persistence across N frames
  - Output: `ChangeEvent(timestamp, regions: List[BoundingBox], motion_score: float)`

- Tune thresholds on the VisDrone dataset:
  - False positive rate target: < 5% (wind, shadows should not trigger)
  - True positive rate target: > 95% for crowd movement, vehicle entry

**Task 1.4 — Change Detection Testing**
- Build a test harness that plays back recorded drone footage and evaluates change detection outputs against manually labeled events
- Tune `MOG2` parameters: `history`, `varThreshold`, `detectShadows`

**BE/Infra:**
- Instrument change detection module with Prometheus counters: `change_events_per_minute`, `false_positive_rate_estimate`, `processing_latency_ms`

---

### Week 8 — Cognitive Frame Selection

**ML/FE (primary):**

**Task 1.5 — Cognitive Frame Selector**

This is the intelligent filter on top of change detection. It answers: "Of all the frames that changed, which ones are worth sending for deep analysis?"

- Implement `CognitiveFrameSelector` with the following scoring pipeline per candidate frame:

  **Score 1 — Structural Change Score:** SSIM (Structural Similarity Index) difference vs. last selected frame. High SSIM difference = new information.
  
  **Score 2 — Motion Quality Score:** Optical flow magnitude (Farneback) in the changed regions. Distinguishes real motion from lighting changes.
  
  **Score 3 — Saliency Score:** Use a lightweight saliency model (BING saliency or simple edge density) to score how "interesting" a frame is.
  
  **Score 4 — Temporal Diversity Score:** Penalize frames that are too close in time to a recently selected frame (avoid burst of near-identical frames).

- Combine scores into a composite score; select frames above threshold
- Target output rate: 15–20 frames per minute per drone under normal conditions
- Under high-activity conditions, allow burst to 40 FPM

**Task 1.6 — Frame Packager**
- Package each selected frame with metadata:
  ```json
  {
    "frame_id": "uuid",
    "drone_id": "DRONE_001",
    "mission_id": "MISSION_2025_001",
    "utc_timestamp_ms": 1710000000000,
    "location": {"lat": 8.5241, "lon": 76.9366, "altitude_m": 50},
    "frame_jpeg_b64": "...",
    "change_score": 0.72,
    "cognitive_score": 0.85,
    "motion_regions": [...]
  }
  ```
- Compress payload with gzip before transmission

---

### Week 9–10 — Evidence Clip Extraction & Edge Dashboard

**ML/FE:**

**Task 1.7 — Async GPU Event Listener**
- Implement a background thread that connects to the central message bus (Kafka topic: `event.confirmed`)
- On receiving a confirmed event for this drone:
  - Extract timestamp window `[event_ts - 30s, event_ts + 30s]` from rolling buffer
  - Encode extracted frames into MP4 (H.264, libx264, CRF 22)
  - Compute SHA-256 hash of the encoded file
  - Write sidecar JSON: `{event_id, drone_id, mission_id, start_ts, end_ts, file_hash, creation_ts}`
  - Upload clip + sidecar to central evidence storage via secure API
  - Delete local copy after confirmed upload (verify hash on server response)

**Task 1.8 — Edge Operator Dashboard**
- Build a minimal web UI (served locally on `localhost:8080`):
  - Live video feed (MJPEG stream from local buffer)
  - Stream status indicator (green/amber/red)
  - Buffer fill indicator (seconds buffered)
  - Frame transmission rate (FPM sent to ingest)
  - Connection status to ingest server
  - Evidence clip upload status (pending / uploading / done)
- No analytics results, no event data — operator sees only operational health

**BE/Infra:**
- Package edge application as a Docker image (CPU-only, no GPU required on field laptop)
- Write Ansible playbook to deploy and configure edge app on a fresh laptop
- Test full offline resilience: ingest server unreachable → buffer accumulates → reconnect → resume

**Phase 1 Gate Review:**
- ✅ Rolling buffer runs for 2+ hours without memory leak
- ✅ Change detection correctly flags events on 3 test video scenarios
- ✅ Cognitive frame selector outputs ≤20 FPM on low-activity footage
- ✅ Evidence clip created and uploaded after mock "confirmed event" message
- ✅ Edge dashboard functional
- ✅ All metrics visible in Grafana

---

## Phase 2 — Central Ingest & Streaming Server (M3)
### Duration: Weeks 11–15

The ingest server is the authenticated gateway that receives frames from all field laptops and routes them to the GPU analytics cluster. It must handle 20+ concurrent drone feeds with isolation.

---

### Week 11–12 — Kafka Deployment & Stream Authentication

**BE/Infra (primary):**

**Task 2.1 — Kafka Cluster Setup**
- Deploy Apache Kafka 3.x using Docker Compose (start with 3-broker cluster for resilience)
- Configure:
  - Topic per drone: `frames.DRONE_001`, `frames.DRONE_002`, ... (created dynamically on mission start)
  - Retention: 30 minutes (frames are transient — GPU consumes and analytics are authoritative)
  - Replication factor: 2 (fault tolerance)
  - Max message size: 2MB (compressed JPEG frame + metadata)
- Deploy Kafka UI (Conduktor or Kafka-UI) for operational monitoring

**Task 2.2 — Secure Ingest API**
- Build a FastAPI service (`ingest-api`) that acts as the HTTP gateway for edge devices:
  - `POST /v1/frames` — receives a frame payload from an edge laptop
  - Validates JWT token (issued by Keycloak, validated against JWKS endpoint)
  - Extracts `drone_id` and `mission_id` from token claims
  - Validates payload schema (pydantic model)
  - Applies rate limiting: max 50 frames/minute per drone (configurable)
  - Publishes frame to Kafka topic `frames.{drone_id}`
  - Returns `202 Accepted` with frame receipt ID
- Implement back-pressure: if Kafka lag exceeds threshold, return `429 Too Many Requests`

**Task 2.3 — Stream Validation Layer**
- Validate each incoming frame:
  - Schema validation (required fields present)
  - Timestamp sanity check (not in future, not older than 5 minutes)
  - Drone ID matches token claim (prevent cross-drone injection)
  - File size within bounds
- Reject and log invalid frames to `audit.ingest.rejected` topic

---

### Week 13–14 — Stream Router & Queue Management

**BE/Infra (primary):**

**Task 2.4 — Stream Router**
- Build a `stream-router` service that:
  - Consumes from all active `frames.*` Kafka topics
  - Groups frames into batches: collect frames from up to N drones within a 500ms window
  - Publishes batches to `analytics.batch.pending` Kafka topic
  - Ensures each batch has frames from different drones interleaved (for GPU utilization)
  - Preserves drone_id and timestamp in batch metadata for result demultiplexing

**Task 2.5 — Mission Lifecycle Management**
- Build `mission-api` service:
  - `POST /v1/missions` — create a new mission (assigns drone IDs, creates Kafka topics, issues scoped tokens)
  - `PUT /v1/missions/{id}/close` — close mission (stop topic writes, trigger retention policy)
  - Mission state stored in PostgreSQL
  - On mission creation: emit `mission.started` event to audit log

**Task 2.6 — Drone Isolation**
- Ensure Kafka consumer groups are scoped per drone: a failure in processing one drone's frames does not block others
- Implement dead-letter topic `frames.{drone_id}.dlq` for frames that fail validation

---

### Week 15 — Ops Dashboard & Ingest Load Testing

**ML/FE:**

**Task 2.7 — Ingest Operations Dashboard**
- Build React dashboard for system administrators:
  - Active drone count (live)
  - Per-drone frame ingestion rate
  - Kafka consumer lag per drone topic
  - Back-pressure events (429 rates)
  - Ingest API error rate
  - No video content, no analytics data — purely operational
- Use Grafana embedded panels (iframe) for time-series charts; React for the drone status table

**BE/Infra:**
- Run load test: simulate 20 drones at 20 FPM each (= 400 frames/min total)
- Measure: Kafka lag, API latency p95, CPU usage on ingest server
- Fix any bottlenecks before Phase 3

**Phase 2 Gate Review:**
- ✅ 20 simulated drones sending frames simultaneously, zero dropped frames
- ✅ Authenticated frame rejection works (bad token → 401)
- ✅ Rate limiting works (>50 FPM → 429)
- ✅ Kafka topics isolated per drone, DLQ populated correctly
- ✅ Ops dashboard shows all live metrics
- ✅ p95 ingest API latency < 100ms under full load

---

## Phase 3 — GPU Analytics Cluster (M4)
### Duration: Weeks 16–30

This is the longest and most technically complex phase. It encompasses all AI/ML model development, multi-model pipeline construction, batch inference optimization, and the event confirmation engine.

---

### Week 16–17 — Inference Server Setup & Model Serving Infrastructure

**ML/FE (primary):**

**Task 3.1 — Triton Inference Server Deployment**
- Deploy NVIDIA Triton Inference Server 2.x on the GPU node
- Organize model repository:
  ```
  /models
    /person_detector/1/model.plan        (TensorRT optimized YOLOv9)
    /face_detector/1/model.plan
    /crowd_density/1/model.plan
    /vehicle_detector/1/model.plan
    /vessel_detector/1/model.plan
    /lpr_detector/1/model.plan           (License Plate Recognition)
    /reid_model/1/model.plan             (Re-identification)
  ```
- Configure Triton:
  - Dynamic batching: max batch size = 16, max queue delay = 100ms
  - Instance count per model: tune based on GPU memory (start with 1 instance per model)
  - Enable GPU memory sharing between models

**Task 3.2 — Model Export Pipeline**
- For each model: train/fine-tune (or use pretrained) → export to ONNX → optimize with TensorRT for target GPU
- TensorRT optimization: FP16 precision (2x speed vs FP32 with minimal accuracy loss)
- Write export scripts for each model, store in `/dvas/analytics/model_export/`
- Document each model's:
  - Input shape and normalization
  - Output format (bboxes, scores, classes)
  - Accuracy metrics on VisDrone validation set
  - Inference latency on target GPU (single image and batch of 8)

**BE/Infra:**
- Set up Triton's Prometheus metrics endpoint → scrape into Grafana
- Configure GPU memory limits and monitoring

---

### Week 18–20 — Analytics Pipeline: Person, Face, Crowd

**ML/FE (primary):**

**Task 3.3 — Batch Consumer Service**
- Build `analytics-worker` Python service:
  - Consumes batches from `analytics.batch.pending` Kafka topic
  - Parses batch: extract frames, restore drone_id and timestamp mapping
  - Sends frames to Triton via gRPC (tritonclient library)
  - Collects inference results
  - Maps results back to (drone_id, frame_id, timestamp)
  - Publishes per-frame analytics results to `analytics.results.raw` Kafka topic

**Task 3.4 — Person Detection & Tracking**
- Implement `PersonTracker` class:
  - Run person detector on each frame (YOLOv9, COCO person class)
  - Apply ByteTrack tracker: maintains persistent track IDs across frames for the same drone
  - Each track: `{track_id, drone_id, bboxes_history, first_seen_ts, last_seen_ts}`
  - Track state machine: `tentative → confirmed → lost`
  - Only `confirmed` tracks (seen in ≥3 consecutive frames) are published as events
  - Output: `PersonDetectionEvent(track_id, drone_id, timestamp, bbox, confidence)`

**Task 3.5 — Re-Identification**
- Implement re-ID using a MobileNetV3-based appearance embedding model (lightweight for edge batching)
- For each confirmed person track, extract appearance embedding (128-dim vector)
- Store embeddings in a session-level FAISS index per mission
- Re-ID matching: cosine similarity > 0.7 threshold = same person
- Use case: associate a person who disappears from frame and reappears later
- Output: `ReIDEvent(primary_track_id, matched_track_id, similarity_score, drone_id)`

**Task 3.6 — Face Detection**
- Run face detector (RetinaFace or InsightFace) only on person track bounding boxes
- Condition for face detection: person bbox area > 1000 px² (too small = not useful)
- Extract 5-point landmarks, align face to 112×112 canonical form
- Compute face embedding using ArcFace (512-dim)
- De-duplicate: cluster face embeddings within a session (DBSCAN); assign cluster ID
- Important: face data is stored as embeddings + cluster IDs, not raw face images by default
- Output: `FaceDetectionEvent(track_id, cluster_id, drone_id, timestamp, visibility_score)`

**Task 3.7 — Crowd Density Estimation**
- Run CSRNet (or DM-Count) density map estimator on full frames
- CSRNet outputs a density map (same HxW as input); sum of density map = estimated crowd count
- Segment the density map into predefined zones (loaded from zone config JSON per mission/location)
- Compute per-zone count and density (persons/m²)
- Emit alerts when density exceeds configured thresholds:
  - `CROWDED`: > 2 persons/m²
  - `CRITICAL`: > 4 persons/m²
- Output: `CrowdDensityEvent(drone_id, timestamp, total_count, zones: List[ZoneDensity])`

---

### Week 21–22 — Analytics Pipeline: Vehicles, Vessels, Plates

**ML/FE (primary):**

**Task 3.8 — Vehicle Detection & Tracking**
- Run YOLOv9 with vehicle classes: car, bus, truck, motorcycle, bicycle, auto-rickshaw
- Apply ByteTrack for vehicle tracking (same infrastructure as person tracking)
- Extract vehicle attributes for each confirmed track:
  - **Type:** from detector class label
  - **Colour:** dominant colour extraction (K-means on vehicle ROI in HSV space)
  - **Speed:** estimate from pixel displacement across frames, calibrated using drone altitude + camera FOV
    - Speed formula: `v = (pixel_displacement / frame_delta_s) × (GSD_m/px)` where GSD = ground sampling distance
  - **State:** stationary (displacement < 2px over 30 frames) vs. moving
- Output: `VehicleDetectionEvent(track_id, type, colour, speed_kmph, state, bbox, drone_id, timestamp)`

**Task 3.9 — Traffic State Classification**
- Aggregate vehicle detections across a road zone:
  - Compute vehicle density in zone: vehicles/km²
  - Compute average speed of all moving vehicles in zone
  - Classify: `FREE_FLOW`, `SLOW`, `CONGESTED`, `STOPPED`
- Emit `TrafficStateEvent(zone_id, state, vehicle_count, avg_speed, drone_id, timestamp)`

**Task 3.10 — Vessel Detection**
- Fine-tune YOLOv9 on a coastal/waterway dataset (SeaDronesSee dataset is publicly available)
- Vessel classes: boat, ferry, jet-ski, kayak, vessel-unknown
- Apply tracking (same pipeline)
- Output: `VesselDetectionEvent(track_id, type, bbox, heading_deg, drone_id, timestamp)`

**Task 3.11 — Number Plate Detection**
- Two-stage pipeline:
  - Stage 1: Detect license plate region using a plate localization model (YOLO trained on CCPD/IDD datasets)
  - Stage 2: OCR on detected plate region (Tesseract v5 with LSTM, or PaddleOCR)
- Apply only when: vehicle track is confirmed, vehicle area > 2000 px², motion blur estimate < threshold
- Output: `LicensePlateEvent(track_id, plate_text, confidence, drone_id, timestamp)` — flag low-confidence reads
- Important: document that accuracy is context-dependent (altitude, angle, motion)

---

### Week 23–24 — Analytics Pipeline: Safety & Security

**ML/FE (primary):**

**Task 3.12 — Intrusion / Perimeter Breach Detection**
- Zone configuration: police operations team defines restricted zones as GeoJSON polygons per mission
- At runtime: project drone frame into ground plane using homography (requires drone altitude + camera pitch)
- Compute person/vehicle track positions in real-world coordinates
- Detect when a confirmed track enters a restricted zone polygon
- Implement dwell-time threshold: breach only confirmed if entity stays in zone for > 5 seconds (reduces false positives from passers-by)
- Output: `IntrusionEvent(track_id, entity_type, zone_id, entry_ts, dwell_s, drone_id)`

**Task 3.13 — Abandoned Object Detection**
- Stationary object detection:
  - Detect all objects in frame (person, bag, vehicle, etc.)
  - Track stationary objects: no movement > N pixels over M seconds
  - Check: was a person near this object recently (within 60s)? If yes and person left → candidate abandoned object
  - Dwell threshold: configurable, default 120 seconds
- Output: `AbandonedObjectEvent(object_id, bbox, drone_id, dwell_s, timestamp)`
- Document limitation: performance degrades with camera shake and crowded scenes

**Task 3.14 — Accident / Incident Detection**
- Heuristic + learned approach:
  - **Sudden stop:** vehicle speed drops from > 20 km/h to near-zero within 2 frames
  - **Scatter pattern:** multiple persons rapidly dispersing from a central point
  - **Cluster formation:** multiple persons converging rapidly on a point (potential fight/incident)
  - **Vehicle overlap:** two vehicle tracks whose bounding boxes intersect significantly
- Each heuristic outputs a candidate event with a confidence score
- Only emit event if 2+ heuristics fire simultaneously within a 5-second window (reduces false positives)
- Output: `IncidentEvent(type, confidence, location, tracks_involved, drone_id, timestamp)`

---

### Week 25–27 — Multi-Frame Fusion & Event Confirmation Engine

**ML/FE (primary):**

**Task 3.15 — Multi-Frame Fusion Layer**
- Implement `FusionEngine` class:
  - Maintains a sliding time window of raw analytics events per drone (last 30 seconds)
  - Applies temporal consistency checks:
    - Person track must appear in ≥ 3 frames within 10 seconds to be confirmed
    - Crowd density estimate must be consistent across ≥ 5 consecutive frames (±20% variance allowed)
    - Intrusion must persist for > dwell threshold
  - Cross-frame association: link events from multiple drones watching the same area using GPS coordinates
  - Confidence aggregation: combine per-frame confidences using weighted average (recent frames weighted higher)

**Task 3.16 — Event Confirmation Engine**
- Implement `EventConfirmationEngine`:
  - Input: fused analytics data from FusionEngine
  - Apply per-event-type confirmation thresholds (configurable YAML):
    ```yaml
    person_detection:
      min_confidence: 0.70
      min_consecutive_frames: 3
    crowd_critical:
      min_density: 4.0
      min_consecutive_estimates: 5
    intrusion:
      min_dwell_seconds: 5
      min_confidence: 0.75
    incident:
      min_heuristics_fired: 2
      min_confidence: 0.65
    abandoned_object:
      min_dwell_seconds: 120
    ```
  - False-positive suppression: if the same location fires > 10 events in 60 seconds, apply rate limiting and flag for review
  - Emit `ConfirmedEvent` to:
    1. Kafka topic `event.confirmed` (consumed by edge laptop for clip extraction)
    2. Kafka topic `event.dashboard` (consumed by central dashboard)
    3. PostgreSQL `confirmed_events` table (via a DB writer service)

**Task 3.17 — Result Demultiplexer**
- Build `result-demux` service:
  - Consumes from `analytics.results.raw`
  - Routes results back to correct drone's tracking state (using drone_id from metadata)
  - Ensures no cross-drone contamination of tracker state

---

### Week 28–30 — GPU Performance Optimization & Resilience

**ML/FE:**

**Task 3.18 — Batch Size Optimization**
- Profile GPU utilization under 20-drone simulated load
- Tune Triton dynamic batching parameters: `max_batch_size`, `preferred_batch_size`, `max_queue_delay_microseconds`
- Target: GPU utilization > 70% at 20 drones; p95 latency < 3 seconds per batch

**Task 3.19 — Model Pipeline Parallelism**
- Run independent models concurrently using Triton's concurrent model execution
- Schedule: person_detector and vehicle_detector run in parallel (different GPU streams)
- Only run face_detector after person_detector confirms a person is present in frame

**BE/Infra:**

**Task 3.20 — Analytics Worker Resilience**
- Implement dead-letter handling: if analytics worker crashes mid-batch, re-queue the batch
- Analytics worker is stateless — all tracker state is stored in Redis (session-scoped)
- Deploy Redis for tracker state (session-scoped, TTL = mission duration + 1 hour)
- Implement graceful shutdown: on SIGTERM, finish current batch, flush state, exit

**Phase 3 Gate Review:**
- ✅ All 8 analytics models running through Triton
- ✅ 20-drone simulated load with GPU util > 60%
- ✅ Event confirmation engine produces zero false positives on 3 controlled test scenarios
- ✅ Confirmed events arrive at Kafka `event.confirmed` topic within 5 seconds of incident
- ✅ Tracker state survives analytics worker restart

---

## Phase 4 — Evidence Object Storage (M5)
### Duration: Weeks 31–34

---

### Week 31–32 — MinIO Deployment & Immutability Controls

**BE/Infra (primary):**

**Task 4.1 — MinIO Cluster Deployment**
- Deploy MinIO in distributed mode: 4 drives (can be on same machine for Phase 1, different servers in Phase 3)
- Enable **MinIO Object Locking** (WORM — Write Once Read Many):
  - Bucket: `dvas-evidence`
  - Default lock mode: `GOVERNANCE` (admins can override with proper authorization)
  - Default retention period: 90 days (configurable per event type)
- Enable versioning (retain all versions, never silently overwrite)
- Enable server-side encryption (SSE-S3) with keys stored in Vault
- Configure MinIO audit logging → forward to Loki

**Task 4.2 — Evidence Upload API**
- Build `evidence-api` FastAPI service:
  - `POST /v1/evidence/clips` — accept MP4 clip + sidecar JSON from edge laptop
  - Validate JWT (edge laptop service account token)
  - Verify clip hash (SHA-256 from sidecar must match computed hash of received file)
  - Generate a globally unique `evidence_id` (UUID v7 — time-sortable)
  - Store clip in MinIO: key = `{year}/{month}/{day}/{mission_id}/{evidence_id}.mp4`
  - Store sidecar: key = `{year}/{month}/{day}/{mission_id}/{evidence_id}.json`
  - Set MinIO object lock on both objects
  - Write evidence record to PostgreSQL `evidence_items` table
  - Return `{evidence_id, storage_location, lock_expiry}`
  - All operations logged to audit system

**Task 4.3 — Evidence Retrieval API**
- `GET /v1/evidence/{evidence_id}/stream` — stream video to dashboard (authorized users only)
  - Generate time-limited MinIO presigned URL (15-minute expiry)
  - Log access to audit system before issuing URL
  - Never expose direct MinIO credentials to dashboard frontend
- `GET /v1/evidence/{evidence_id}/metadata` — return sidecar JSON
- `GET /v1/evidence/{evidence_id}/integrity` — re-compute and verify hash, return `{valid: true/false}`

---

### Week 33–34 — Retention Policy Engine & Legal Hold

**BE/Infra (primary):**

**Task 4.4 — Retention Policy Engine**
- Build `retention-engine` background service (runs as a daily cron):
  - Queries PostgreSQL for evidence items past their retention window
  - Checks for active legal holds before marking for deletion
  - For items past retention with no hold: call MinIO to remove GOVERNANCE lock, then delete
  - Log every deletion with: who authorized, what was deleted, when, and why
  - Emit a `RetentionEvent` to audit log for each deletion
- Retention period lookup from `retention_policies` table:
  ```
  event_type       | default_days | max_days | legal_hold_allowed
  incident         | 180          | 3650     | true
  crowd_monitoring | 30           | 180      | true
  routine_traffic  | 14           | 90       | true
  audit_logs       | 1825         | NULL     | false (never delete)
  ```

**Task 4.5 — Legal Hold API**
- `POST /v1/legalhold` — apply legal hold to one or more evidence items (authorized role: SUPERVISOR+)
  - Updates MinIO object lock to `COMPLIANCE` mode (cannot be overridden even by admin)
  - Sets `legal_hold = true` in PostgreSQL
  - Logs hold application with requesting officer's ID and stated reason
- `DELETE /v1/legalhold/{hold_id}` — release legal hold (requires dual authorization: two SUPERVISOR accounts)
  - Log release with both authorizing officers' IDs

**Phase 4 Gate Review:**
- ✅ Evidence clip uploaded, hash verified, WORM lock applied
- ✅ Attempt to delete locked object via MinIO admin → rejected
- ✅ Evidence stream URL expires after 15 minutes
- ✅ Retention engine correctly deletes 30-day old items but skips items under legal hold
- ✅ Legal hold requires dual authorization

---

## Phase 5 — Databases & Audit Systems (M6)
### Duration: Weeks 35–38

---

### Week 35–36 — PostgreSQL Schema & Time-Series DB

**BE/Infra (primary):**

**Task 5.1 — PostgreSQL Schema Design**

```sql
-- Core operational tables
CREATE TABLE missions (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,   -- active, closed, archived
    created_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    location_geojson JSONB,
    operator_ids UUID[],
    config JSONB
);

CREATE TABLE drones (
    id TEXT PRIMARY KEY,    -- DRONE_001 etc.
    mission_id UUID REFERENCES missions(id),
    operator_id UUID,
    hardware_serial TEXT,
    status TEXT,
    last_seen TIMESTAMPTZ
);

CREATE TABLE confirmed_events (
    id UUID PRIMARY KEY,
    drone_id TEXT REFERENCES drones(id),
    mission_id UUID REFERENCES missions(id),
    event_type TEXT NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    confidence FLOAT,
    location_lat FLOAT,
    location_lon FLOAT,
    metadata JSONB,
    evidence_id UUID,       -- FK to evidence_items
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE entity_tracks (
    id UUID PRIMARY KEY,
    event_id UUID REFERENCES confirmed_events(id),
    track_id TEXT NOT NULL,
    entity_type TEXT,       -- person, vehicle, vessel
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    trajectory JSONB,       -- array of {ts, bbox, location}
    attributes JSONB        -- type, colour, speed, etc.
);

CREATE TABLE evidence_items (
    id UUID PRIMARY KEY,
    event_id UUID REFERENCES confirmed_events(id),
    drone_id TEXT,
    mission_id UUID,
    clip_path TEXT,
    clip_hash TEXT,
    file_size_bytes BIGINT,
    duration_seconds FLOAT,
    start_ts TIMESTAMPTZ,
    end_ts TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    retention_expires_at TIMESTAMPTZ,
    legal_hold BOOLEAN DEFAULT FALSE,
    legal_hold_id UUID
);

CREATE TABLE retention_policies (
    event_type TEXT PRIMARY KEY,
    default_days INTEGER,
    max_days INTEGER,
    legal_hold_allowed BOOLEAN
);
```

- Create indexes on `event_ts`, `drone_id`, `mission_id`, `event_type` for query performance
- Enable row-level security (RLS) — enforce access control at DB level (defense in depth)

**Task 5.2 — TimescaleDB Setup**
- Enable TimescaleDB extension on PostgreSQL
- Create hypertable for time-series data:
  ```sql
  CREATE TABLE crowd_density_ts (
      time TIMESTAMPTZ NOT NULL,
      drone_id TEXT,
      mission_id UUID,
      zone_id TEXT,
      person_count INTEGER,
      density_per_sqm FLOAT
  );
  SELECT create_hypertable('crowd_density_ts', 'time');
  
  CREATE TABLE traffic_state_ts (
      time TIMESTAMPTZ NOT NULL,
      drone_id TEXT,
      zone_id TEXT,
      state TEXT,
      vehicle_count INTEGER,
      avg_speed_kmph FLOAT
  );
  SELECT create_hypertable('traffic_state_ts', 'time');
  ```
- Configure compression policy: compress chunks older than 7 days (80%+ space saving)

---

### Week 37 — OpenSearch & Audit Log System

**BE/Infra (primary):**

**Task 5.3 — OpenSearch Deployment**
- Deploy OpenSearch 2.x (single node for Phase 1, add replicas in Phase 3)
- Create index for event search:
  ```json
  {
    "mappings": {
      "properties": {
        "event_id": {"type": "keyword"},
        "event_type": {"type": "keyword"},
        "drone_id": {"type": "keyword"},
        "mission_id": {"type": "keyword"},
        "event_ts": {"type": "date"},
        "confidence": {"type": "float"},
        "location": {"type": "geo_point"},
        "metadata": {"type": "object", "enabled": false}
      }
    }
  }
  ```
- Build `search-api` FastAPI service:
  - `GET /v1/events/search` — query by event_type, drone_id, time_range, location_radius, confidence_min
  - Authenticated and RBAC-enforced (users only see events from their authorized missions)
  - Every search query logged to audit system

**Task 5.4 — Tamper-Evident Audit Log**
- Design: append-only table in PostgreSQL + Loki forwarding
  ```sql
  CREATE TABLE audit_log (
      id BIGSERIAL PRIMARY KEY,       -- monotonically increasing
      ts TIMESTAMPTZ DEFAULT NOW() NOT NULL,
      actor_id TEXT NOT NULL,         -- user ID or system service ID
      actor_role TEXT NOT NULL,
      action TEXT NOT NULL,           -- LOGIN, VIEW_EVIDENCE, EXPORT, DELETE, LEGAL_HOLD, etc.
      resource_type TEXT,
      resource_id TEXT,
      outcome TEXT NOT NULL,          -- SUCCESS, DENIED, ERROR
      ip_address INET,
      request_id TEXT,
      details JSONB,
      prev_hash TEXT,                 -- hash of previous row (chaining)
      row_hash TEXT GENERATED ALWAYS AS (
          md5(actor_id || action || ts::TEXT || COALESCE(resource_id,'') || COALESCE(prev_hash,''))
      ) STORED
  );
  ```
- The `row_hash` chains each row to the previous (tamper-evident: modifying any row breaks all subsequent hashes)
- Grant INSERT-only to application service accounts; no UPDATE or DELETE ever
- Audit log entries are also forwarded to Loki (dual storage for resilience)

---

### Week 38 — Database Access Layer & Migration System

**BE/Infra:**

**Task 5.5 — Database Service Layer**
- Build `db-service` Python package (internal library used by other services):
  - All SQL in one place — no raw SQL in business logic services
  - Use `asyncpg` for async PostgreSQL access
  - Use `SQLAlchemy 2.0` Core (not ORM) for query building
  - Connection pooling: `asyncpg` pool with min=5, max=20 per service
  - All writes wrapped in transactions with explicit rollback on error

**Task 5.6 — Database Migration System**
- Use `Alembic` for schema migrations
- All migrations in `/dvas/database/migrations/`
- Migration policy: no destructive migrations (no DROP COLUMN, no DROP TABLE) without a data-preservation migration first
- Run migrations automatically in CI/CD pipeline before deploying any service that uses the DB

**Phase 5 Gate Review:**
- ✅ All tables created and seeded with test data
- ✅ OpenSearch returns correct results for event_type and location queries
- ✅ Audit log chaining: modify one row → all subsequent hashes break (verified by test)
- ✅ TimescaleDB compression reduces 7-day-old data by > 70%
- ✅ RLS policies block unauthorized queries at the DB level

---

## Phase 6 — Authentication & Access Control (M7)
### Duration: Weeks 39–43

---

### Week 39–40 — Keycloak Deployment & Role Hierarchy

**BE/Infra (primary):**

**Task 6.1 — Keycloak Configuration**
- Deploy Keycloak 24 on a dedicated server
- Configure `dvas` realm:
  - Disable self-registration (admin-provisioned accounts only)
  - Force password policy: min 12 chars, uppercase, number, special char
  - Require MFA for all roles (TOTP via authenticator app)
  - Session timeout: 8 hours (align with duty shift)
  - Idle timeout: 30 minutes

- Create roles:
  ```
  DRONE_OPERATOR      - edge dashboard only
  FIELD_SUPERVISOR    - edge + limited ingest ops view
  CONTROL_ROOM_OFFICER - central dashboard: active missions only
  SUPERVISOR          - central dashboard: all missions in jurisdiction
  SENIOR_OFFICER      - state-level aggregated dashboard
  SYSTEM_ADMIN        - infra management, no evidence access
  COMPLIANCE_OFFICER  - audit log access, evidence integrity checks
  ```

- Configure role hierarchy: higher roles inherit lower role permissions (except SYSTEM_ADMIN which is isolated)

**Task 6.2 — Token Configuration**
- JWT access token TTL: 15 minutes
- Refresh token TTL: 8 hours (one duty shift)
- Token claims:
  ```json
  {
    "sub": "officer_uuid",
    "preferred_username": "officer_badge_id",
    "roles": ["CONTROL_ROOM_OFFICER"],
    "district": "THIRUVANANTHAPURAM",
    "unit": "ARMED_RESERVE",
    "active_mission_ids": ["uuid1", "uuid2"],
    "iat": 1710000000,
    "exp": 1710000900
  }
  ```

---

### Week 41 — RBAC Enforcement Layer

**BE/Infra (primary):**

**Task 6.3 — Central RBAC Middleware**
- Build `auth-middleware` Python package (shared across all backend services):
  - FastAPI dependency injection: `Depends(require_role(["CONTROL_ROOM_OFFICER", "SUPERVISOR"]))`
  - Validates JWT signature (JWKS from Keycloak)
  - Checks token expiry
  - Extracts role claims
  - Returns `AuthContext` object containing all token claims
  - Logs every authenticated request to audit log

**Task 6.4 — RBAC Permission Matrix**

| Resource | DRONE_OPERATOR | CONTROL_ROOM | SUPERVISOR | SENIOR | SYSTEM_ADMIN |
|---|---|---|---|---|---|
| Edge Dashboard | ✅ | ❌ | ❌ | ❌ | ❌ |
| Live Alerts | ❌ | ✅ own district | ✅ own district | ✅ all | ❌ |
| View Evidence | ❌ | ✅ active mission | ✅ all missions | ✅ read-only | ❌ |
| Export Evidence | ❌ | ❌ | ✅ with approval | ✅ | ❌ |
| Apply Legal Hold | ❌ | ❌ | ✅ | ✅ | ❌ |
| User Management | ❌ | ❌ | ❌ | ❌ | ✅ |
| Audit Logs | ❌ | ❌ | ✅ own unit | ✅ all | ❌ |
| Mission Create | ❌ | ❌ | ✅ | ✅ | ❌ |

---

### Week 42 — ABAC Policy Engine

**BE/Infra (primary):**

**Task 6.5 — Open Policy Agent (OPA) Integration**
- Deploy OPA (Open Policy Agent) as a sidecar policy engine
- Write ABAC policies in Rego:

```rego
# Policy: can officer view this evidence item?
allow if {
    # Role check (from RBAC)
    "CONTROL_ROOM_OFFICER" in input.user.roles
    
    # Must be an active mission (not closed more than 24h ago)
    input.resource.mission_status == "active"
    
    # District match
    input.user.district == input.resource.district
    
    # Time window: only during assigned duty hours
    time.now_ns() >= input.user.duty_start_ns
    time.now_ns() <= input.user.duty_end_ns
    
    # Case association: if mission is restricted, user must be explicitly assigned
    not input.resource.restricted
}

allow if {
    "CONTROL_ROOM_OFFICER" in input.user.roles
    input.resource.restricted
    input.user.sub in input.resource.authorized_officers
}
```

- Integrate OPA into `auth-middleware`: after RBAC check passes, call OPA with full context
- OPA decision (allow/deny) is logged to audit system with all evaluated attributes

---

### Week 43 — Token Revocation & Security Hardening

**BE/Infra:**

**Task 6.6 — Token Revocation System**
- Build `revocation-service`:
  - Maintains a Redis set of revoked token JTI (JWT ID) values
  - All backend services check this set on every request (fast O(1) Redis lookup)
  - Revocation triggers:
    - Officer logs out (immediate)
    - Admin revokes session (e.g., device lost → emergency revoke)
    - Role change (all tokens for user revoked, re-authentication required)
    - Mission closes (tokens scoped to that mission revoked)
  - Revocation events logged to audit system with reason

**Task 6.7 — mTLS Between Internal Services**
- Configure mutual TLS for all service-to-service communication (not just HTTPS):
  - Issue service certificates from internal Vault CA
  - Each service presents its certificate when calling another service
  - Services reject connections from unknown certificates
- This ensures that even if an attacker gains network access, they cannot impersonate a service

**Phase 6 Gate Review:**
- ✅ DRONE_OPERATOR cannot access evidence API (403)
- ✅ CONTROL_ROOM_OFFICER cannot view evidence from closed mission > 24h (ABAC deny)
- ✅ Revoked token rejected within 100ms of revocation
- ✅ mTLS working between analytics-worker and evidence-api
- ✅ Keycloak MFA enforced for all roles

---

## Phase 7 — Dashboards (M8)
### Duration: Weeks 44–52

---

### Week 44–46 — Central Command Dashboard (Core)

**ML/FE (primary):**

**Task 7.1 — Dashboard Architecture**
- Frontend: React 18 + TypeScript + Vite
- UI library: shadcn/ui components (Tailwind CSS)
- Map: Leaflet.js with OpenStreetMap tiles (self-hosted tile server for data sovereignty)
- Charts: Recharts for time-series data
- Real-time updates: WebSocket connection to `dashboard-ws` backend service
- State management: Zustand (lightweight, no Redux boilerplate)

**Task 7.2 — Dashboard Backend (WebSocket Gateway)**
- Build `dashboard-ws` FastAPI service with WebSocket support:
  - `GET /ws/dashboard?token=JWT` — establish WebSocket connection
  - Authenticate token on connect; reject invalid tokens before connection is established
  - Subscribe to Kafka topic `event.dashboard` for this user's authorized missions
  - Push events to connected dashboard in real-time as JSON messages
  - Disconnect when token expires (force re-authentication)
  - Connection metadata logged to audit system

**Task 7.3 — Live Alerts Panel**
- Real-time event feed showing confirmed events from all active drones
- Each alert shows: event type (icon), drone ID, timestamp, location (text + map pin), confidence score
- Severity color coding: info (blue), warning (orange), critical (red)
- Sound alert for CRITICAL events (browser notification API)
- Clicking an alert opens the evidence clip player

**Task 7.4 — Multi-Drone Situational Map**
- Leaflet map with:
  - Live drone position markers (updated every 5 seconds from GPS metadata)
  - Drone field-of-view polygon (estimated from altitude + camera FOV angle)
  - Event markers (clustered) with color by event type
  - Zone overlays (restricted zones, monitoring zones) as GeoJSON polygons
  - Click a drone → see its latest analytics summary in a side panel
  - Click an event marker → see event details + "View Evidence" button

---

### Week 47–49 — Evidence Playback, Analytics & Reports

**ML/FE (primary):**

**Task 7.5 — Evidence Player**
- Video player component (Video.js) that streams from evidence-api presigned URLs
- Synchronized annotation overlay:
  - Show bounding boxes at correct timestamp (from entity_tracks metadata)
  - Show event timeline below the player scrubber
  - Highlight frame when a specific detection occurred
- Player controls: play/pause, speed (0.5x, 1x, 2x), frame-by-frame step
- Export clip button (generates export request → SUPERVISOR approval required → logged)
- Integrity badge: shows hash verification status from `evidence-api/integrity` endpoint

**Task 7.6 — Analytics Summary Panels**
- Crowd density chart: TimescaleDB time-series data, zone breakdown, heatmap overlay on map
- Traffic state timeline: zone-based traffic state over time
- Event type breakdown: pie/bar chart of confirmed event types in current mission
- Entity tracking history: click a track ID → see its full trajectory on the map

**Task 7.7 — Reporting Module**
- Build report generator (backend PDF generation using `WeasyPrint`):
  - Operational summary report: mission details, event counts by type, drones deployed, key incidents
  - Evidence chain of custody report: for a specific evidence item, full access log
  - Audit trail report: all actions by a specific user in a time window
- Reports generated on-demand, logged to audit system, stored temporarily (24h auto-delete)

---

### Week 50–51 — Role-Based Views & Admin Dashboard

**BE/Infra + ML/FE:**

**Task 7.8 — Role-Based View Filtering**
- Implement client-side view filtering based on JWT role claims
- CONTROL_ROOM_OFFICER: sees only events from active missions in their district
- SUPERVISOR: sees all missions in their district, plus historical search
- SENIOR_OFFICER: sees state-wide aggregated view (no individual evidence playback without explicit case association)
- SYSTEM_ADMIN: sees only infrastructure panels (no mission data, no evidence)

**Task 7.9 — Administrative Dashboard**
- React application for SYSTEM_ADMIN role:
  - User management (create, disable, role assignment) — calls Keycloak Admin API
  - Mission management (create, configure, close)
  - Zone configuration (draw restricted zones on map → save GeoJSON)
  - Retention policy management (view/edit, requires dual authorization for changes)
  - System health overview (aggregated from Grafana via API)

**Task 7.10 — Audit Dashboard (COMPLIANCE_OFFICER)**
- View all audit log entries with filters: actor, action, time range, resource
- Export audit logs (PDF or CSV) for court submissions
- Flag anomalies: highlight actions that deviate from normal patterns (e.g., evidence accessed at 3 AM, bulk downloads)

---

### Week 52 — Dashboard Integration Testing & UX Hardening

**ML/FE:**

**Task 7.11 — End-to-End UX Testing**
- Test all user flows for each role (use Playwright for automated browser testing):
  - CONTROL_ROOM_OFFICER: receive alert → view event → play evidence → file report
  - SUPERVISOR: search past events → review audit log → apply legal hold
  - DRONE_OPERATOR: monitor stream health → see upload status
- Accessibility: ensure WCAG 2.1 AA compliance (important for government deployment)
- Test on low-bandwidth connection (field operators may have limited connectivity)

**Phase 7 Gate Review:**
- ✅ Live events appear on map within 5 seconds of GPU confirmation
- ✅ Evidence plays correctly with bounding box overlay synced to video timestamp
- ✅ DRONE_OPERATOR sees no event data or evidence (confirmed by role testing)
- ✅ Audit log shows every evidence view with correct officer identity
- ✅ Export generates PDF with correct chain-of-custody information

---

## Phase 8 — Legal Compliance & Chain of Custody Engine (M9)
### Duration: Weeks 53–58

---

### Week 53–54 — Section 65B Certificate Engine

**BE/Infra (primary):**

**Task 8.1 — Certificate Generation Service**
- Build `compliance-api` service:
  - `POST /v1/certificates/65b` — generate a Section 65B certificate for a specific evidence item
  - Required inputs: evidence_id, certifying_officer_id, incident_description
  - Pulls from PostgreSQL: evidence metadata, chain of custody log, system integrity records
  - Generates certificate PDF using the Annexure B template (filled with actual data)
  - PDF includes: officer details, system description, evidence hash, access log summary, integrity status
  - Certificate itself is stored in evidence storage with its own hash
  - Log certificate generation to audit system

**Task 8.2 — Chain of Custody Report Generator**
- For a given evidence_id, generate a complete chain of custody document:
  1. Evidence creation event: drone ID, mission ID, GPS location, creation timestamp
  2. System processing log: confirmation timestamp, GPU confidence scores, clip extraction timestamp
  3. Upload confirmation: hash verification, MinIO storage path, lock confirmation
  4. Access history: every officer who viewed/downloaded the evidence (from audit log)
  5. Integrity status: re-computed hash matches stored hash (yes/no)
- Format as a signed PDF with embedded QR code linking to online verification endpoint

**Task 8.3 — Online Integrity Verification Endpoint**
- `GET /v1/verify/{evidence_id}` — publicly accessible (on internal network for court use) endpoint:
  - Returns: evidence_id, hash, creation_ts, system_verified (boolean), last_verified_ts
  - Does NOT return the video itself — only integrity metadata
  - Allows a court to verify evidence authenticity without accessing the full system

---

### Week 55–56 — Privacy Controls & DPDPA Alignment

**BE/Infra:**

**Task 8.4 — Data Minimisation Enforcement**
- Implement automated checks:
  - Scan `confirmed_events` table: if event_type is `ROUTINE_CROWD_MONITORING` and no linked investigation, auto-schedule deletion at 14 days
  - Face embedding data: if not linked to a confirmed incident within 72 hours, auto-purge from FAISS index
  - Analytics metadata for non-events: purge after 24 hours (only confirmed events are retained)

**Task 8.5 — Purpose Limitation Controls**
- Every data access request must include a `purpose_code` from a predefined list:
  ```
  LIVE_MONITORING    - during active mission
  INCIDENT_REVIEW    - reviewing a specific confirmed event
  INVESTIGATION      - linked to a registered case number
  AUDIT              - compliance/supervisory review
  COURT_SUBMISSION   - preparing evidence for legal proceedings
  ```
- If `purpose_code = COURT_SUBMISSION`, automatically generate Section 65B certificate
- Purpose code is logged with every access event

**Task 8.6 — Breach Notification Protocol**
- Build `breach-monitor` service:
  - Monitors audit log for anomaly patterns:
    - Bulk evidence access (>10 clips in 1 hour by same user)
    - Access outside duty hours
    - Multiple failed authentication attempts
    - Unusual export volumes
  - On anomaly detection: create an alert in admin dashboard + send email to designated DPO (Data Protection Officer)
  - Maintain a `security_incidents` table for tracking

---

### Week 57–58 — Integration Compliance Testing & Documentation

**Both engineers:**

**Task 8.7 — End-to-End Legal Compliance Test**
- Simulate a complete operational scenario:
  1. Mission created, drones deployed
  2. Incident detected and confirmed
  3. Evidence clip created with full chain of custody
  4. Supervisor applies legal hold
  5. Section 65B certificate generated
  6. Evidence chain of custody report generated
  7. Audit log reviewed for completeness
  8. Verify all steps are tamper-evident
- Document every step with screenshots for the compliance handbook

**Task 8.8 — System Documentation**
- Produce final technical documentation:
  - System architecture document (update from proposal to as-built)
  - Operations manual (step-by-step for each dashboard role)
  - SOP document (per Annexure A requirements)
  - Disaster recovery playbook
  - Incident response playbook (what to do if a breach is detected)

**Phase 8 Gate Review:**
- ✅ Section 65B certificate generated correctly for a test evidence item
- ✅ Chain of custody PDF is complete and matches audit log
- ✅ Breach monitor correctly flags bulk access anomaly
- ✅ All documentation complete and reviewed

---

## Phase 9 — System Integration, Load Testing & Pilot Preparation
### Duration: Weeks 59–66

---

### Week 59–61 — Full System Integration Testing

**Both engineers:**

**Task 9.1 — End-to-End Integration Test Suite**
- Simulate 5-drone operation for 2 hours continuously:
  - Use pre-recorded drone footage played back through the edge application
  - Measure: event detection latency (capture → confirmed event), evidence clip creation time, dashboard update time
  - Target end-to-end latency: < 10 seconds from incident occurrence to alert appearing on dashboard

**Task 9.2 — 20-Drone Load Test**
- Scale to 20 simulated drones
- Measure under 20-drone load:
  - GPU utilization (target: 65–80%)
  - Kafka consumer lag (target: < 30 seconds)
  - Evidence storage write throughput
  - Dashboard WebSocket latency
  - Database query p95 latency (target: < 200ms)
- Profile and fix all bottlenecks

**Task 9.3 — Failure Scenario Testing**
- Test each failure mode and verify graceful recovery:
  - Ingest server goes down → edge buffers → ingest recovers → frames resume
  - GPU worker crashes mid-batch → batch re-queued → event detected (within 60 seconds of restart)
  - Evidence storage temporarily unreachable → edge holds clip locally → retries → uploads
  - Keycloak goes down → existing valid tokens continue to work for their remaining TTL → re-authentication fails gracefully

---

### Week 62–63 — Security Penetration Testing

**Both engineers (with optional external pen tester):**

**Task 9.4 — Security Testing Checklist**
- Authentication bypass attempts: direct API calls without token, expired token, revoked token
- Privilege escalation: DRONE_OPERATOR token used to access evidence API
- SQL injection: all API endpoints that accept parameters
- Path traversal: evidence file path manipulation
- Replay attacks: reuse of an old valid token
- IDOR (Insecure Direct Object Reference): access evidence item by ID without mission authorization
- Kafka message injection: attempt to publish to analytics.batch.pending from unauthorized client
- MinIO direct access: attempt to access evidence bucket bypassing evidence-api
- All findings documented and fixed before pilot

---

### Week 64–66 — Pilot Preparation & Deployment

**BE/Infra:**

**Task 9.5 — Pilot Infrastructure Deployment**
- Deploy all services on physical hardware (not development machines)
- Run Ansible playbooks to provision all nodes from scratch (validate reproducibility)
- Configure monitoring alerts: Grafana alerts for critical metrics (GPU temp, disk space, Kafka lag)
- Set up daily automated backups: PostgreSQL dumps + MinIO replication
- Perform disaster recovery drill: restore from backup, verify system operates correctly

**ML/FE:**

**Task 9.6 — Model Fine-tuning on Kerala Data**
- Collect a small labeled dataset of Kerala-specific scenarios (coastal, temple festivals, highways)
- Fine-tune person, vehicle, and crowd models on this data
- Validate accuracy improvement over base models
- Update Triton model repository with fine-tuned models

**Task 9.7 — Operator Training Material**
- Create video walkthroughs for each dashboard role
- Create printed quick-reference card for edge operators
- Conduct 1-day training session with pilot officers

---

## Phase 10 — Monitoring, Hardening & Handover
### Duration: Weeks 67–88 (Ongoing, parallel to pilot operations)

---

**Tasks (ongoing during pilot and beyond):**

**Task 10.1 — Operational Monitoring (BE/Infra)**
- Weekly review of Grafana dashboards: anomaly detection model performance, evidence storage growth, access patterns
- Monthly audit log review: check for policy violations, unusual access patterns
- Quarterly retention policy run verification

**Task 10.2 — Model Performance Monitoring (ML/FE)**
- Track false positive rate from officer feedback (implement "flag this alert as incorrect" button in dashboard)
- Re-train models quarterly using feedback data
- Monitor for model drift (accuracy degrading over time)

**Task 10.3 — Scalability Expansion (BE/Infra)**
- Phase 2 district-level: add ingest nodes, scale Kafka brokers, add GPU nodes
- Phase 3 state-level: migrate from Docker Compose to K3s (lightweight Kubernetes) for multi-node orchestration
- Configure K3s cluster with node affinity: GPU nodes run analytics workloads, storage nodes run MinIO

**Task 10.4 — Advanced Capabilities (ML/FE, Phase 4 of document)**
- Evaluate and implement only after pilot validation:
  - Multi-drone fusion: correlate events across two drones watching the same area
  - Altitude-adaptive models: retrain with images from multiple altitudes
  - Crowd behaviour prediction: LSTM sequence model on crowd density time-series
  - Cross-camera re-identification: associate a person tracked by Drone A with a person seen by Drone B

---

## Master Timeline Summary

| Phase | Weeks | Key Deliverable |
|---|---|---|
| Phase 0: Foundation | 1–3 | Environment, CI/CD, tech stack |
| Phase 1: Edge Layer | 4–10 | Working edge application with buffer + change detection |
| Phase 2: Ingest Server | 11–15 | Kafka pipeline handling 20 concurrent drones |
| Phase 3: GPU Analytics | 16–30 | All 8 analytics capabilities + event confirmation |
| Phase 4: Evidence Storage | 31–34 | WORM-locked MinIO with retention and legal hold |
| Phase 5: Databases | 35–38 | PostgreSQL + TimescaleDB + OpenSearch + tamper-evident audit |
| Phase 6: Auth & Access Control | 39–43 | Keycloak + RBAC + ABAC + token revocation |
| Phase 7: Dashboards | 44–52 | All three dashboards with role-based views |
| Phase 8: Legal Compliance | 53–58 | Section 65B certificates, chain of custody, DPDPA |
| Phase 9: Integration & Pilot | 59–66 | Full system load-tested and pilot-ready |
| Phase 10: Operations | 67–88 | Ongoing monitoring, model tuning, district scale-up |

**Total: 22 months to pilot-ready; ~30 months to full state-level deployment**

---

## Third-Party & Open Source Licenses to Verify

Before production deployment, both engineers must verify that every component's license permits law-enforcement use:

| Component | License | Action |
|---|---|---|
| YOLOv9 (Ultralytics) | AGPL-3.0 | **Requires commercial license for closed deployment** — budget for this |
| ByteTrack | MIT | OK |
| InsightFace / ArcFace | MIT | OK |
| CSRNet / DM-Count | MIT / Apache 2.0 | OK |
| Apache Kafka | Apache 2.0 | OK |
| MinIO Community | AGPL-3.0 | **Requires commercial license for internal deployment** — or use AIStor |
| Keycloak | Apache 2.0 | OK |
| OPA | Apache 2.0 | OK |
| OpenSearch | Apache 2.0 | OK |
| TimescaleDB Community | Timescale License | Free for single-node; verify terms for multi-node |
| Triton Inference Server | BSD 3-Clause | OK |

---

---

# Probable Hiccups, Gaps & Risks in the Current Document

This section identifies technical, legal, and operational gaps in the proposal document that the engineering team must resolve before or during implementation.

---

## Category 1 — Technical Ambiguities & Missing Specifications

### H1 — Drone Communication Protocol Not Specified
**Problem:** The document says drones "transmit video to the assigned operator laptop" but never specifies the protocol. Most commercial drones (DJI, Parrot) use proprietary RTSP streams, custom SDKs, or require specific apps. Some drones require their own controller as a bridge device.

**Impact:** The edge application may not be able to receive video from certain drones without a drone SDK integration.

**Resolution needed:** A concrete list of approved drone models must be produced before Phase 1 begins. For each model, document: video output protocol (RTSP URL format, USB, HDMI), required firmware, SDK license terms.

---

### H2 — GPS Geolocation Accuracy Not Addressed
**Problem:** Multiple analytics capabilities (intrusion detection, zone analysis, speed estimation, location metadata in evidence) rely on accurate geolocation. However, drone GPS typically has 2–5 meter accuracy. At an altitude of 50m with a 90° FOV camera, a 3-meter GPS error translates to significant ground position error.

**Impact:** Intrusion alerts may fire for objects that are near a restricted zone but outside it. Speed estimates will be inaccurate. Location metadata in evidence clips may be legally challenged.

**Resolution needed:** Implement geolocation uncertainty bounds in all location-dependent outputs. Add camera calibration procedure (using ground control points). Document location accuracy limitations in the system's operational handbook and evidence certificates.

---

### H3 — Homography Calibration (Aerial-to-Ground Projection) Not Detailed
**Problem:** Zone-based analysis, intrusion detection, speed estimation, and crowd density in m² all require projecting video pixels to real-world ground coordinates. This requires a calibration step: drone altitude, camera pitch/yaw/roll, and lens focal length must be known and used to compute a homography matrix.

**Impact:** Without calibration, zone analysis is pixel-based (not geographic), speed estimates are wrong, and density estimates are in px/px² not persons/m².

**Resolution needed:** Build a calibration module that: reads drone telemetry (altitude, gimbal angles, GPS), reads camera metadata (focal length, sensor size), computes the homography matrix per frame, and applies it to all spatial analytics. Test on actual drone footage with known ground-truth measurements.

---

### H4 — Field-to-HQ Bandwidth Not Quantified
**Problem:** The document mentions "selected frames" are transmitted and bandwidth is "manageable," but no actual numbers are provided. At 20 FPM per drone and 20 drones, with 150KB per JPEG frame: 20 × 20 × 150KB = 60 MB/min = 1 MB/s upstream. For 4G field connections, this may be acceptable. For crowded areas with poor signal, it could cause failures.

**Impact:** Frame delivery may fail in poor network conditions. The system must degrade gracefully.

**Resolution needed:** Conduct field bandwidth measurement at planned deployment sites. Implement adaptive quality reduction (reduce JPEG quality or frame rate) under low bandwidth. Add a local store-and-forward mode: edge buffers frames to SSD, uploads when connection improves.

---

### H5 — Number Plate Detection Accuracy Overstated
**Problem:** The document says number plate detection is "supported" and "demonstrated on suitable datasets," but in real drone footage from typical operational altitudes (30–80m), number plates are rarely legible. At 50m altitude with a standard drone camera, a Kerala license plate (340mm × 200mm) covers only ~12–30 pixels in height depending on resolution — below the minimum required for reliable OCR.

**Impact:** LPR will produce high false positive rates and low recall in most operational scenarios. If this is relied upon for enforcement, errors will be legally and reputationally damaging.

**Resolution needed:** Clearly document altitude and angle constraints for LPR. Recommend that LPR is only used when drones fly at < 20m altitude specifically for plate capture. Add confidence thresholds and human-review flags. Remove LPR from "confirmed event" triggers — treat as advisory annotation only.

---

### H6 — Crowd Density Estimation at Drone Altitudes Not Validated
**Problem:** Deep learning crowd counting models (CSRNet, DM-Count) are trained on surveillance camera footage at typical heights of 5–20m with near-overhead angles. Drone footage at 50–100m with varying angle produces very different visual characteristics (smaller heads, more occlusion from overhead angle, color variation with altitude).

**Impact:** Crowd count estimates will be systematically biased. The error margin is unknown without testing on actual drone footage from the target environment.

**Resolution needed:** Collect ground-truth crowd count data at Kerala Police events. Fine-tune the density estimation model on this drone-specific data before using counts for operational decisions. Communicate uncertainty bounds in the dashboard (e.g., "Estimated 500 ± 150 persons").

---

### H7 — The "Asynchronous GPU Confirmation → Edge Clip Extraction" Flow Has a Race Condition
**Problem:** The rolling buffer on the edge laptop is 60 seconds. The GPU processes frames and confirms events. But if the GPU takes more than 60 seconds to process (e.g., during heavy load with 20 drones), the relevant frames will have been overwritten in the rolling buffer by the time the confirmation arrives at the edge laptop.

**Impact:** Evidence clips will be empty or contain wrong footage — a critical failure for evidential purposes.

**Resolution needed:** Extend the rolling buffer to at least 3× the maximum expected GPU processing latency under peak load. Based on load testing in Phase 3, determine the actual 99th-percentile latency and set buffer accordingly (likely 3–5 minutes minimum). Alternatively, implement a "candidate frame preservation" mode: when the GPU flags a candidate event (before full confirmation), the edge laptop immediately marks those frames as "do not overwrite" in the buffer.

---

### H8 — Face Data Handling Needs Legal Clarification
**Problem:** The document says face detection is performed and face embeddings are extracted. The system generates "face cluster IDs" but doesn't clearly state under what legal authorization this is permitted. Facial recognition data is biometric data under emerging Indian data protection law (Digital Personal Data Protection Act 2023).

**Impact:** Collecting and processing facial biometric data without proper legal basis may violate the DPDPA 2023 and constitutional privacy protections.

**Resolution needed:** Obtain specific legal opinion on whether face embedding (not face image, but mathematical representation) constitutes "biometric data" under DPDPA. If yes, implement: consent or legitimate government use documentation, strict access controls on face data, auto-purge of face embeddings when not linked to a confirmed incident within 72 hours. Consider making face analytics an opt-in module that requires explicit superintendent-level authorization per mission.

---

## Category 2 — Architecture & Design Gaps

### H9 — No Disaster Recovery Plan Specified
**Problem:** The document describes what data is stored and how, but never specifies backup frequency, recovery point objective (RPO), or recovery time objective (RTO). If the GPU cluster, MinIO, or PostgreSQL fails, there is no documented procedure.

**Resolution needed:** Define: PostgreSQL: daily full backup + WAL streaming (RPO: 1 hour). MinIO: cross-site replication to a secondary on-premise location. Redis (tracker state): ephemeral, acceptable to lose (re-start analytics for affected drones). Target RTO: 2 hours for non-evidence systems, 4 hours for evidence system.

---

### H10 — No Offline Mode for Field Operations
**Problem:** The entire analytics pipeline depends on the HQ GPU cluster. If the WireGuard VPN tunnel to HQ drops, the edge laptop has no analytics capability. Operators would only have a live video feed with no alerts.

**Resolution needed:** Design a degraded-mode operation: if HQ connection is unavailable for > 5 minutes, switch edge application to "record-only mode": buffer continuously to SSD (extend from 60 seconds to full available SSD space), alert operator that analytics are unavailable, upload buffered footage when connection restores, run GPU analytics retrospectively on uploaded footage. This is especially important for operations in remote areas (coastal, forest).

---

### H11 — Token Revocation Latency vs. Short-Lived Token TTL
**Problem:** The document proposes 15-minute access tokens but claims "access can be terminated instantly." In reality, with a 15-minute token TTL and a Redis-based revocation list, the revocation is near-instant only if all backend services check the revocation list on every request. If any service caches tokens locally (common performance optimization), revocation effectiveness drops.

**Resolution needed:** Mandate that all services check the Redis revocation list on every authenticated request with no local caching of revocation decisions. Measure the additional latency of this Redis check and ensure it is acceptable (should be < 5ms on local Redis). Alternatively, shorten token TTL to 5 minutes (increases Keycloak load but reduces window of unauthorized access for a revoked token).

---

### H12 — No Specification for How Zone Configurations Are Managed
**Problem:** Intrusion detection and crowd zone analysis depend on predefined zone polygons. The document mentions zones can be configured per operation, but there is no specification for: who can create/edit zones, how they are version-controlled, whether zones can be modified during an active mission, and how zone configuration affects historical analytics queries.

**Resolution needed:** Build a zone management module in the admin dashboard. Only SUPERVISOR+ roles can create or modify zones. Zone configurations are versioned (immutable after a mission is created with that zone set). Zone modification during an active mission requires explicit confirmation and creates a new zone version. Historical queries always use the zone configuration active at the time of the event.

---

## Category 3 — Legal & Regulatory Risks

### H13 — Drone Operations Approval Framework Missing
**Problem:** Section 14.5 says operations comply with "civil aviation and unmanned aircraft regulations" but provides no specifics. In India, the Drone Rules 2021 (Ministry of Civil Aviation) require: operator registration, drone registration, Digital Sky Platform permission for each flight in controlled airspace, and NPNT (No Permission No Takeoff) compliance for most drones.

**Impact:** Drone operations without proper permissions could result in flight being unauthorized and all evidence from those flights being inadmissible.

**Resolution needed:** Document the specific permit types obtained. Integrate mission creation with Digital Sky Platform API (or manual process): mission cannot be started until airspace clearance is confirmed. Store clearance document reference in the `missions` table.

---

### H14 — The ABAC Policy Examples Are Incomplete for All Realistic Scenarios
**Problem:** The ABAC policies described in the document cover the obvious cases (role + district + duty hours). But real-world edge cases are not addressed: What happens during an emergency where an officer needs data outside normal duty hours? What is the approval process? Who can override ABAC for emergencies?

**Resolution needed:** Design and implement an emergency access override workflow: officer requests emergency access → automated notification to duty supervisor → supervisor approves in dashboard → ABAC policy temporarily overridden for specified officer + resource + time window → all emergency access flagged prominently in audit log.

---

### H15 — Section 65B Certificate Requires a "Person Responsible for the Computer"
**Problem:** Section 65B(4) of the Indian Evidence Act requires the certificate to be signed by a person "in charge of the management of the relevant activities." In a multi-component automated system like this one, determining who legally qualifies as the "person in charge" for each evidence item is complex and must be pre-defined.

**Resolution needed:** Before pilot deployment, obtain legal opinion on who must sign Section 65B certificates for this system. Likely candidates: the CCTNS/IT officer responsible for the system, the mission commander, or the drone operator. Create a designated role `CERTIFYING_OFFICER` in the system with a defined appointment process. Ensure the Annexure B template is reviewed and approved by the department's legal counsel.

---

### H16 — No Mention of Informed Notice to Public
**Problem:** The Supreme Court's right to privacy judgment (K.S. Puttaswamy, 2017) and emerging surveillance law suggest that the public should be informed when aerial surveillance is being conducted. The document does not address any public notice requirement.

**Resolution needed:** Consult the department's legal team and the State Human Rights Commission on whether public notice is required. If required, build a mechanism to publish surveillance notices (drone deployment in area, dates/times) to a public portal before operations begin.

---

## Summary Risk Matrix

| Hiccup | Severity | Blocks Pilot? | Resolution Phase |
|---|---|---|---|
| H1 — Drone protocol | HIGH | YES | Before Phase 1 |
| H2 — GPS accuracy | MEDIUM | NO | Phase 3 |
| H3 — Homography calibration | HIGH | PARTIAL | Phase 3 |
| H4 — Bandwidth not quantified | MEDIUM | NO | Phase 1 |
| H5 — LPR accuracy | MEDIUM | NO | Phase 3 |
| H6 — Crowd counting accuracy | MEDIUM | NO | Phase 3 |
| H7 — Race condition in clip creation | CRITICAL | YES | Phase 1/3 |
| H8 — Face data legal basis | HIGH | YES | Before Phase 1 |
| H9 — No DR plan | HIGH | NO | Phase 9 |
| H10 — No offline mode | MEDIUM | NO | Phase 10 |
| H11 — Token revocation caching | MEDIUM | NO | Phase 6 |
| H12 — Zone management gaps | LOW | NO | Phase 7 |
| H13 — Drone permit framework | CRITICAL | YES | Before Phase 1 |
| H14 — Emergency ABAC override | MEDIUM | NO | Phase 6 |
| H15 — 65B certificate legal clarity | HIGH | YES | Before Phase 8 |
| H16 — Public notice requirements | MEDIUM | NO | Before Pilot |

---

*Document prepared for internal engineering use. Version 1.0. Classification: Confidential — Technical Staff Only.*
