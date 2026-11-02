"""
Schemas for ML models, watchlists, and presets.
"""
from typing import List, Optional
from pydantic import BaseModel


class ModelUpdateIn(BaseModel):
    active: List[str]


class WatchlistAddIn(BaseModel):
    plate: str
    reason: str = ""
    vehicle_type: str = ""
    case_ref: str = ""           # FIR / case number, stored in plate_watchlist.case_ref


class PresetCreateIn(BaseModel):
    name: str
    filters: dict
    is_shared: bool = False


class LegalHoldCreateIn(BaseModel):
    case_number: str
    hold_reason: str


class LegalHoldReleaseIn(BaseModel):
    release_reason: str = ""


class PurgeDryRunIn(BaseModel):
    older_than_days: int = 365


class PurgeExecuteIn(BaseModel):
    older_than_days: int = 365
    confirmation_phrase: str


class AuditAccessLogIn(BaseModel):
    session_id: Optional[str] = None
    referrer: Optional[str] = None

