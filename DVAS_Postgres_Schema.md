# DVAS Postgres Schema — Evidence, Metadata, Analytics, Audit

**Principle driving every table below:** Postgres never stores video bytes. Per §9.1 of the proposal,
video clips live in object storage (SeaweedFS/MinIO); Postgres stores **references, checksums, and
everything queryable** — the metadata layer that makes the object storage searchable, auditable, and
legally defensible. If a table below looks like it's "storing evidence," it's storing a pointer to
evidence plus the facts a court or supervisor would need about it — never the pixels themselves.

Second principle: **legally/operationally significant fields get real columns, not JSONB.** Timestamps,
drone IDs, checksums, confidence scores, and anything that shows up in an audit trail or a §65B
certificate are strict typed columns. JSONB is used only for genuinely variable, non-critical
attributes (e.g. top-3 colour guesses) where forcing a rigid schema would be actively unhelpful.

---

## 1. Entity relationship overview

```
drones ──┬─< mission_drone_assignments >──┬─ missions ──< zones
         │                                │
         │                                └─< mission_analytics_config >── analytics_presets
         │
         ├─< tracks ──┬─< vehicle_attributes
         │             ├─< plate_reads
         │             └─< detections (raw, optional/short-retention)
         │
         ├─< confirmed_events ──┬─< evidence_clips ──┬─< evidence_certificates
         │        │              │                    └─< legal_holds
         │        │              └─< evidence_frames
         │        └── (links to: tracks, zones, missions)
         │
         └─< crowd_density_readings >── zones

users ──< audit_log
users ──< mission_analytics_config (set_by)
users ──< evidence_certificates (certifying_officer)

retention_policies  (standalone config table, referenced by policy logic, not FK'd everywhere)
```

---

## 2. Extensions

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid()
-- Optional, add only if actually needed:
-- CREATE EXTENSION IF NOT EXISTS postgis;   -- real geography columns for zones/drone position
-- CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector, if storing face embeddings for re-ID/de-dup
```

If PostGIS isn't installed on the pilot laptop, use plain `latitude`/`longitude` `DOUBLE PRECISION`
columns instead of `GEOGRAPHY` below — noted inline where relevant. Don't install PostGIS just for a
two-laptop pilot unless zone polygon math is actually needed yet.

---

## 3. Operational context — drones, missions, zones

```sql
CREATE TABLE drones (
    drone_id              TEXT PRIMARY KEY,             -- 'DRONE_KL_001', matches Redis stream naming
    label                 TEXT,
    model                 TEXT,
    registration_number   TEXT,
    status                TEXT NOT NULL DEFAULT 'inactive'
                              CHECK (status IN ('active','inactive','maintenance')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE missions (
    mission_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  TEXT NOT NULL,
    mission_type          TEXT,                          -- 'highway_patrol' | 'public_event' | 'coastal_watch' | ...
    location_description  TEXT,
    jurisdiction           TEXT,                          -- feeds ABAC (§8.3)
    status                TEXT NOT NULL DEFAULT 'planned'
                              CHECK (status IN ('planned','active','completed','cancelled')),
    started_at            TIMESTAMPTZ,
    ended_at              TIMESTAMPTZ,
    created_by            UUID REFERENCES users(user_id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mission_drone_assignments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id    UUID NOT NULL REFERENCES missions(mission_id),
    drone_id      TEXT NOT NULL REFERENCES drones(drone_id),
    operator_id   UUID REFERENCES users(user_id),
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, drone_id)
);

CREATE TABLE zones (
    zone_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id  UUID NOT NULL REFERENCES missions(mission_id),
    name        TEXT NOT NULL,
    zone_type   TEXT,                       -- 'restricted' | 'entry_point' | 'open_ground' | 'road_segment'
    -- geography(POLYGON,4326) if PostGIS is installed; otherwise store GeoJSON:
    boundary_geojson JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 4. Model selection / mission analytics config

*(this is where the "who picks which models run" decision from the earlier discussion actually lives —
whether that's a supervisor or an operator, the table doesn't care; `set_by_role` records which.)*

```sql
CREATE TABLE analytics_presets (
    preset_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT UNIQUE NOT NULL,       -- 'Highway Patrol', 'Public Event', 'Coastal Watch'
    description  TEXT,
    modules      TEXT[] NOT NULL,            -- {'person','vehicle','plate'}
    created_by   UUID REFERENCES users(user_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mission_analytics_config (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id      UUID NOT NULL REFERENCES missions(mission_id),
    drone_id        TEXT NOT NULL REFERENCES drones(drone_id),
    preset_id       UUID REFERENCES analytics_presets(preset_id),
    active_modules  TEXT[] NOT NULL,          -- resolved snapshot — gpu_consumer.py reads THIS, not the preset
    set_by          UUID REFERENCES users(user_id),
    set_by_role     TEXT NOT NULL,            -- 'drone_operator' | 'control_room_officer' | 'supervisor' | 'system_admin'
    set_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mac_drone_latest ON mission_analytics_config (drone_id, set_at DESC);
-- gpu_consumer.py query per batch:
--   SELECT active_modules FROM mission_analytics_config
--   WHERE drone_id = $1 ORDER BY set_at DESC LIMIT 1;
```

---

## 5. Tracks and detections (the analytics core)

```sql
CREATE TABLE tracks (
    track_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drone_id           TEXT NOT NULL REFERENCES drones(drone_id),
    mission_id         UUID REFERENCES missions(mission_id),
    track_type         TEXT NOT NULL CHECK (track_type IN ('person','vehicle','vessel')),
    first_seen         TIMESTAMPTZ NOT NULL,
    last_seen          TIMESTAMPTZ NOT NULL,
    reidentified_from  UUID REFERENCES tracks(track_id),   -- re-ID link across non-contiguous segments (§5.1)
    metadata           JSONB                                -- non-critical extras only
);
CREATE INDEX idx_tracks_drone_time ON tracks (drone_id, first_seen);

-- Raw per-frame detections: high volume, short retention, mainly for debugging/tuning fusion thresholds
-- rather than long-term legal record — the confirmed_events table below is the authoritative one.
CREATE TABLE detections (
    detection_id     BIGSERIAL,
    track_id         UUID REFERENCES tracks(track_id),
    drone_id         TEXT NOT NULL,
    ts               TIMESTAMPTZ NOT NULL,
    frame_sequence   BIGINT,
    bbox_x1 INT, bbox_y1 INT, bbox_x2 INT, bbox_y2 INT,
    confidence       REAL NOT NULL,
    module           TEXT NOT NULL,        -- which analyzer produced this: 'person'|'face'|'vehicle'|'plate'|'crowd'
    PRIMARY KEY (detection_id, ts)
) PARTITION BY RANGE (ts);
-- Create monthly partitions; set a short retention (e.g. 14–30 days) via the retention_policies table
-- and a cleanup job — this table is operationally useful, not evidentiary.
```

---

## 6. Confirmed events — the authoritative table

This is the single most important table in the schema: only rows here represent §4.4's *"authoritative
event confirmation"* — never raw detections. Everything else (evidence clips, certificates, dashboard
alerts) hangs off this table.

```sql
CREATE TABLE confirmed_events (
    event_id      UUID NOT NULL DEFAULT gen_random_uuid(),
    mission_id    UUID REFERENCES missions(mission_id),
    drone_id      TEXT NOT NULL REFERENCES drones(drone_id),
    zone_id       UUID REFERENCES zones(zone_id),
    track_id      UUID REFERENCES tracks(track_id),        -- NULL for zone-level events (e.g. crowd threshold)
    event_type    TEXT NOT NULL,        -- 'person_detected'|'intrusion'|'accident'|'abandoned_object'
                                         -- |'vehicle_detected'|'crowd_threshold'|'vessel_detected'
    confidence    REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    ts            TIMESTAMPTZ NOT NULL,
    latitude      DOUBLE PRECISION,     -- or GEOGRAPHY(POINT,4326) if PostGIS installed
    longitude     DOUBLE PRECISION,
    attributes    JSONB,                -- type-specific non-critical extras
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reviewed','dismissed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX idx_confirmed_events_drone_ts ON confirmed_events (drone_id, ts);
CREATE INDEX idx_confirmed_events_mission  ON confirmed_events (mission_id);
CREATE INDEX idx_confirmed_events_type     ON confirmed_events (event_type);
```

### 6.1 Vehicle-specific detail

```sql
CREATE TABLE vehicle_attributes (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id         UUID REFERENCES tracks(track_id),
    event_id         UUID REFERENCES confirmed_events(event_id),
    vehicle_type     TEXT,               -- car|truck|bus|motorcycle|auto-rickshaw
    colour_top3      JSONB,              -- [{"colour":"white","prob":0.81}, ...]
    make_model_top3  JSONB,
    speed_kph        REAL,
    speed_reliability TEXT,
    direction_label  TEXT,
    traffic_state    TEXT                -- 'moving'|'parked'|'congested'
);
```

### 6.2 Plate reads — deliberately separate, deliberately gated

```sql
CREATE TABLE plate_reads (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id              UUID REFERENCES tracks(track_id),
    event_id              UUID REFERENCES confirmed_events(event_id),
    plate_text            TEXT,
    ocr_confidence        REAL NOT NULL,
    is_reliable           BOOLEAN NOT NULL DEFAULT false,   -- your gating logic sets this, not raw confidence alone
    plate_crop_frame_id   UUID REFERENCES evidence_frames(frame_id)
);
```
Matches §5.11 exactly: *"does not attempt plate recognition where visibility is insufficient"* — a
low-confidence read is stored with `is_reliable = false` rather than discarded, so it can be reviewed
later, but the dashboard/UI should never present an unreliable read as a confirmed identification.

### 6.3 Crowd density — time series

```sql
CREATE TABLE crowd_density_readings (
    id                BIGSERIAL,
    drone_id          TEXT NOT NULL,
    zone_id           UUID REFERENCES zones(zone_id),   -- NULL if whole-frame count (no zone config yet)
    ts                TIMESTAMPTZ NOT NULL,
    estimated_count   REAL,
    density_per_sqm   REAL,
    heatmap_object_key TEXT,             -- optional: object storage ref for a saved heatmap image
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
```

---

## 7. Evidence — metadata only, object storage holds the bytes

```sql
CREATE TABLE evidence_clips (
    evidence_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id           UUID NOT NULL REFERENCES confirmed_events(event_id) ON DELETE RESTRICT,
    drone_id           TEXT NOT NULL REFERENCES drones(drone_id),
    storage_backend    TEXT NOT NULL DEFAULT 'seaweedfs',
    object_key         TEXT NOT NULL,                 -- bucket/path in object storage
    checksum_sha256    TEXT NOT NULL,                 -- integrity verification, §16.1
    file_size_bytes    BIGINT,
    duration_seconds   REAL,
    pre_event_seconds  REAL,
    post_event_seconds REAL,
    codec              TEXT,
    resolution         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    retention_until    TIMESTAMPTZ,
    legal_hold         BOOLEAN NOT NULL DEFAULT false,
    legal_hold_reason  TEXT,
    is_deleted         BOOLEAN NOT NULL DEFAULT false,  -- soft delete only — see §9 below
    deleted_at         TIMESTAMPTZ
);
CREATE INDEX idx_evidence_event ON evidence_clips (event_id);
CREATE INDEX idx_evidence_retention ON evidence_clips (retention_until) WHERE NOT legal_hold;

CREATE TABLE evidence_frames (
    frame_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id      UUID REFERENCES confirmed_events(event_id),
    evidence_id   UUID REFERENCES evidence_clips(evidence_id),   -- nullable; a crop can stand alone
    object_key    TEXT NOT NULL,
    frame_type    TEXT NOT NULL CHECK (frame_type IN
                      ('annotated_frame','face_crop','plate_crop','person_crop','vehicle_crop')),
    checksum_sha256 TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evidence_certificates (          -- Annexure B, §65B Indian Evidence Act
    certificate_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id            UUID NOT NULL REFERENCES evidence_clips(evidence_id),
    certifying_officer_id  UUID NOT NULL REFERENCES users(user_id),
    certificate_text       TEXT NOT NULL,
    case_reference         TEXT,
    issued_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE legal_holds (
    hold_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id   UUID NOT NULL REFERENCES evidence_clips(evidence_id),
    reason        TEXT NOT NULL,
    ordered_by    TEXT,                  -- court order / inquiry reference
    applied_by    UUID REFERENCES users(user_id),
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at   TIMESTAMPTZ
);
-- Applying a hold sets evidence_clips.legal_hold = true via trigger or application logic;
-- retention cleanup jobs must always check both the flag AND an active (unreleased) row here.
```

**Why `ON DELETE RESTRICT` on `evidence_clips.event_id`:** evidence must never disappear as a side
effect of something else being deleted — chain of custody (§16) depends on evidence rows being
immutable and intentional to remove, never a cascade accident.

**Why `is_deleted` instead of `DELETE`:** §10.3 requires deletions to be policy-driven and logged, not
silent. A retention-expiry job sets `is_deleted = true`, `deleted_at = now()`, and removes the object
storage bytes — the Postgres row stays as a tombstone recording *that* it was deleted, when, and under
which policy, which is itself part of the audit trail.

---

## 8. Users and audit

Postgres doesn't own authentication — Keycloak does. This table is a **read-mostly cache** synced from
Keycloak, needed purely so other tables can have a stable `user_id` foreign key without round-tripping
to Keycloak on every join.

```sql
CREATE TABLE users (
    user_id       UUID PRIMARY KEY,        -- matches Keycloak's 'sub' claim exactly
    username      TEXT UNIQUE NOT NULL,
    full_name     TEXT,
    badge_number  TEXT,
    role          TEXT NOT NULL CHECK (role IN
                      ('drone_operator','control_room_officer','supervisor','senior_officer','system_admin')),
    jurisdiction  TEXT,                     -- feeds ABAC alongside missions.jurisdiction
    is_active     BOOLEAN NOT NULL DEFAULT true,
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    audit_id       BIGSERIAL,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id        UUID REFERENCES users(user_id),
    user_role      TEXT,
    action         TEXT NOT NULL,       -- 'login'|'view_evidence'|'export_evidence'|'preset_selected'|'config_change'|...
    resource_type  TEXT,                -- 'evidence_clip'|'confirmed_event'|'mission_analytics_config'|'user'|...
    resource_id    TEXT,
    source_system  TEXT,                -- 'dashboard'|'edge'|'ingest_api'|'admin_api'
    source_ip      INET,
    outcome        TEXT NOT NULL CHECK (outcome IN ('allow','deny')),
    details        JSONB,
    PRIMARY KEY (audit_id, ts)
) PARTITION BY RANGE (ts);

-- Append-only enforcement: no application role should have UPDATE/DELETE on this table.
REVOKE UPDATE, DELETE ON audit_log FROM dvas_app;
```

Every table interaction that matters legally should produce a row here — evidence views, preset
changes (from the earlier discussion), config edits, login/logout, exports. This is what backs §8.5
and §16.2 directly.

---

## 9. Retention policy

```sql
CREATE TABLE retention_policies (
    policy_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_category   TEXT NOT NULL UNIQUE,   -- 'evidence_clip'|'structured_analytics'|'audit_log'|'crowd_density'
    retention_days  INT NOT NULL,
    configurable    BOOLEAN NOT NULL DEFAULT true,
    updated_by      UUID REFERENCES users(user_id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed values matching §10.2:
INSERT INTO retention_policies (data_category, retention_days) VALUES
    ('evidence_clip', 90),          -- 30–180 day range per proposal; pick a working default
    ('structured_analytics', 730),  -- 1–3 years
    ('crowd_density', 270),         -- 6–12 months
    ('audit_log', 1825);            -- minimum 5 years
```

A scheduled job reads this table, finds `evidence_clips`/`confirmed_events`/etc. past
`created_at + retention_days` **and not under `legal_hold`**, soft-deletes them, and logs the action
to `audit_log`. Never a raw `DELETE ... WHERE created_at < ...` cron job — always policy-table-driven
and always audit-logged, per §10.3.

---

## 10. Partitioning and volume notes (practical, for a two-laptop pilot)

Four tables are genuinely high-volume and partitioned by `ts` above: `detections`, `confirmed_events`,
`crowd_density_readings`, `audit_log`. For a pilot with a handful of drones, **monthly partitions are
enough** — don't over-engineer this yet:

```sql
CREATE TABLE confirmed_events_2026_07 PARTITION OF confirmed_events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
-- repeat per month, or automate with pg_partman if/when this becomes tedious
```

`detections` (raw, pre-fusion) is the one table worth being aggressive about pruning — it's useful for
tuning `FusionEngine` thresholds during development, not something you need years of. Keep 2–4 weeks,
drop old partitions outright (this table isn't evidentiary, so `DROP PARTITION` is fine here, unlike
everywhere else in this schema).

---

## 11. What each dashboard page actually queries

Tying this back to the dashboard integration plan, so the schema isn't abstract:

- **`CommandDashboard.js`** → `confirmed_events` (live via WebSocket bridge off Redis, historical via
  paged query) joined to `evidence_clips` for playback links, `zones` for map overlays.
- **`IngestDashboard.js`** → doesn't touch Postgres much; mostly Redis queue depth — but `drones.status`
  and `mission_drone_assignments` for "which drones are live right now."
- **`AdminDashboard.js`** → `users`, `audit_log`, `retention_policies`, `analytics_presets`.
- **`EdgeDashboard.js`** → mostly Prometheus, not Postgres — maybe `mission_analytics_config` if you
  want the operator's own dashboard to show which preset is currently active for their drone.

---

## 12. Open items to confirm

- Whether PostGIS is worth installing now (real polygon zone math, distance queries) or whether
  lat/lon columns + JSONB GeoJSON is good enough for pilot scale.
- Whether face embeddings for re-ID/de-dup should live in Postgres via `pgvector`, or stay purely in
  the housekeeping scripts' own `.npy` files as they do today — pulling them into Postgres only makes
  sense once de-dup needs to run against the *live* pipeline rather than as an offline batch job.
- Default retention values above are placeholders inside the proposal's stated ranges — actual numbers
  should come from whoever owns the legal/policy sign-off, not be left as engineering defaults.
