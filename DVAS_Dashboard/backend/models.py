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
    Text,
    DateTime,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    jurisdiction = Column(String, default="")
    mission_scope = Column(JSONB, default=list)  # list of mission IDs
    password_hash = Column(String, nullable=False)
    suspended = Column(Boolean, default=False)
    created_at = Column(String)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True)
    ts = Column(String, nullable=False, index=True)
    actor_id = Column(String)
    actor_email = Column(String)
    actor_role = Column(String)
    action = Column(String)
    target = Column(String, default="")
    details = Column(Text, default="")
    ip = Column(String, default="-")
    prev_hash = Column(String)
    hash = Column(String)


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
    jurisdiction = Column(String)
    status = Column(String)
    lat = Column(Float)
    lng = Column(Float)
    altitude_m = Column(Integer)
    battery_pct = Column(Integer)
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
