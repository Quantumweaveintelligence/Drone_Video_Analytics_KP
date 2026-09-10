"""
SQLAlchemy 2.x ORM table definitions for DVAS.

Maps former MongoDB collections → PostgreSQL tables.
"""

from sqlalchemy import (
    Column,
    String,
    Boolean,
    Float,
    Integer,
    BigInteger,
    Text,
    DateTime,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True)
    ts = Column(String, nullable=False, index=True)

    # ── Actor fields ──
    actor_id = Column(String, index=True)
    actor_email = Column(String)
    actor_role = Column(String, index=True)
    actor_name = Column(String)                                # display name
    actor_session_id = Column(String)                          # Keycloak session ID
    actor_device_id = Column(String)                           # User-Agent fingerprint hash

    # ── Action ──
    action = Column(String, index=True)                        # raw action key, e.g. "user_create"
    action_type = Column(String, index=True)                   # normalized: create/update/delete/login/logout/export/system
    category = Column(String, index=True)                      # authentication/user_mgmt/operations/evidence/data/configuration/security
    severity = Column(String, index=True, default="info")      # info/warning/critical

    # ── Target resource ──
    target = Column(String, default="")                        # legacy free-text target
    target_type = Column(String)                               # entity type: user/mission/drone/evidence/export/policy/watchlist/system
    target_id = Column(String)                                 # entity primary key
    target_name = Column(String)                               # human-readable label

    # ── Diff / details ──
    details = Column(Text, default="")                         # legacy free-text details
    before_values = Column(JSONB, nullable=True)               # snapshot before change
    after_values = Column(JSONB, nullable=True)                # snapshot after change

    # ── Result ──
    status = Column(String, default="success", index=True)     # success/failure/error
    error_code = Column(String, nullable=True)                 # HTTP status or app error code

    # ── Tracing ──
    source_module = Column(String)                             # dashboard/ingest/edge/analytics
    source_endpoint = Column(String)                           # API path, e.g. "POST /api/users"
    request_id = Column(String, index=True)                    # UUID per HTTP request
    correlation_id = Column(String, index=True)                # groups related actions (session/transaction)

    # ── Compliance & Legal ──
    compliance_tags = Column(JSONB, default=list)              # e.g. ["IEA_65B", "DPDPA", "IT_ACT_2000"]

    # ── Integrity (hash chain) ──
    ip = Column(String, default="-")
    prev_hash = Column(String)
    hash = Column(String)


class AuditLegalHold(Base):
    """Statutory Legal Hold markers preventing deletion under retention policies."""
    __tablename__ = "audit_legal_holds"

    id = Column(String, primary_key=True)
    audit_log_id = Column(String, ForeignKey("audit_logs.id"), nullable=False, index=True)
    case_number = Column(String, nullable=False, index=True)
    hold_reason = Column(Text, nullable=False)
    placed_by = Column(String, nullable=False)
    placed_by_name = Column(String)
    placed_at = Column(String, nullable=False)
    active = Column(Boolean, default=True, nullable=False, index=True)
    released_by = Column(String, nullable=True)
    released_at = Column(String, nullable=True)
    release_reason = Column(Text, nullable=True)


class AuditFilterPreset(Base):
    """Saved filter presets for the Audit Log Explorer."""
    __tablename__ = "audit_filter_presets"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    owner_id = Column(String, nullable=False, index=True)      # Keycloak user sub
    filters = Column(JSONB, nullable=False)                    # serialized filter state
    is_shared = Column(Boolean, default=False)                 # visible to all authorized users
    created_at = Column(String)
    updated_at = Column(String)


class Mission(Base):
    __tablename__ = "missions"

    mission_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    drone_id = Column(String, nullable=True)
    operator_name = Column(String, nullable=True)
    jurisdiction = Column(String)
    started_at = Column(String)
    ended_at = Column(String, nullable=True)
    operator_ids = Column(JSONB, default=list)  # list of user IDs
    supervisor = Column(String)
    zone = Column(String)
    objective = Column(String)


class Drone(Base):
    __tablename__ = "drones"

    id = Column(String, primary_key=True)
    operator_id = Column(String)
    operator_name = Column(String)
    mission_id = Column(String)
    mission_name = Column(String)
    jurisdiction = Column(String)
    status = Column(String)
    lat = Column(Float)
    lng = Column(Float)
    altitude_m = Column(Integer)
    battery_pct = Column(Integer)
    speed_mps = Column(Float)
    signal_pct = Column(Integer)
    last_analytics = Column(JSONB, default=dict)  # crowd_density, vehicles, etc.


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(String, primary_key=True)
    mission_id = Column(String)
    jurisdiction = Column(String)
    drone_id = Column(String)
    type = Column(String)
    confidence = Column(Float)
    zone = Column(String)
    ts = Column(String, index=True)
    acknowledged_by = Column(String, nullable=True)
    acknowledged_at = Column(String, nullable=True)


class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(String, primary_key=True)
    label = Column(String)
    mission_id = Column(String)
    jurisdiction = Column(String)
    drone_id = Column(String)
    event_type = Column(String)
    confidence = Column(Float)
    duration_s = Column(Integer)
    video_url = Column(String)
    thumbnail = Column(String)
    ts = Column(String, index=True)
    legal_hold = Column(Boolean, default=False)
    retention_until = Column(String)
    # Phase 10: Legal hold details
    legal_hold_reason = Column(Text, nullable=True)
    legal_hold_by = Column(String, nullable=True)
    legal_hold_at = Column(String, nullable=True)
    # Phase 10: Analytics confirmed-event dedup key
    source_event_id = Column(String, nullable=True, unique=True, index=True)


class Export(Base):
    __tablename__ = "exports"

    id = Column(String, primary_key=True)
    evidence_id = Column(String)
    evidence_label = Column(String)
    jurisdiction = Column(String)
    requested_by = Column(String)
    requested_at = Column(String)
    justification = Column(Text)
    status = Column(String, default="pending")
    decision_by = Column(String, nullable=True)
    decision_at = Column(String, nullable=True)
    decision_comment = Column(Text, nullable=True)


class ModelConfig(Base):
    __tablename__ = "model_config"

    scope = Column(String, primary_key=True)
    active = Column(JSONB, default=list)  # list of model keys
    updated_at = Column(String, nullable=True)
    updated_by = Column(String, nullable=True)


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String, nullable=False)  # LOW, MEDIUM, HIGH, CRITICAL
    category = Column(String, default="hardware")  # hardware, stream, telemetry, network, tactical
    created_by = Column(String, nullable=False)
    location = Column(String, nullable=True)
    timestamp = Column(String, index=True)
    mission_id = Column(String, nullable=True)
    drone_id = Column(String, nullable=True)
    status = Column(String, default="OPEN")  # OPEN, ACKNOWLEDGED, RESOLVED
    sent_to_ingest = Column(Boolean, default=True)


class AIModelSuggestion(Base):
    __tablename__ = "ai_model_suggestions"

    id = Column(String, primary_key=True)
    models = Column(JSONB, default=list)        # ["crowd", "person", "intrusion", "anpr", "vehicle"]
    model_names = Column(JSONB, default=list)  # ["Crowd Monitoring", ...]
    priority = Column(String, default="normal") # normal, high, urgent
    reason = Column(Text, nullable=True)
    location = Column(String, nullable=True)
    suggested_by = Column(String, nullable=False)
    timestamp = Column(String, index=True)
    status = Column(String, default="PENDING")   # PENDING, ACKNOWLEDGED, DEPLOYED_TO_GPU
    drone_id = Column(String, nullable=True)
    mission_id = Column(String, nullable=True)


class Report(Base):
    """Generated on-demand report metadata.

    PDF bytes are stored in MinIO bucket ``dvas-evidence``
    under key ``reports/{id}.pdf``.
    """
    __tablename__ = "reports"

    id = Column(String, primary_key=True)
    report_type = Column(String, nullable=False)      # daily_incident | weekly_response | drone_utilisation | mission_after_action
    title = Column(String, nullable=False)
    requested_by = Column(String, nullable=False)
    requested_at = Column(String, nullable=False)
    jurisdiction = Column(String)
    status = Column(String, default="generating")     # generating | ready | failed
    params = Column(JSONB, default=dict)              # date range, mission_id, etc.
    file_key = Column(String, nullable=True)          # MinIO object key
    file_size_bytes = Column(BigInteger, nullable=True)
    generated_at = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    watermark_text = Column(String, nullable=True)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    jurisdiction = Column(String, nullable=True)
    mission_scope = Column(JSONB, default=list)
    password_hash = Column(String, nullable=False)
    suspended = Column(Boolean, default=False)
    created_at = Column(String, nullable=True)

