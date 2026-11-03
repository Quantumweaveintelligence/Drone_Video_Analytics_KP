"""
Schemas for ML models, watchlists, and presets.
"""
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class ModelUpdateIn(BaseModel):
    active: List[str]


class FpsPolicyIn(BaseModel):
    """Analytics FPS governor policy (Detection Models page, SO/SA)."""
    model_config = ConfigDict(extra="forbid")
    target_min: int = Field(ge=1, le=60)
    target_max: int = Field(ge=1, le=60)
    floor: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def _ordered(self):
        if not self.floor <= self.target_min <= self.target_max:
            raise ValueError("floor ≤ target_min ≤ target_max is required")
        return self


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
    case_number: str = Field(..., min_length=1, max_length=120)
    # `justification` is the Audit page's name for the reason; either is accepted.
    hold_reason: Optional[str] = Field(None, max_length=4000)
    justification: Optional[str] = Field(None, max_length=4000)
    legal_authority: Optional[str] = Field(None, max_length=500)
    contact_email: Optional[EmailStr] = None

    @model_validator(mode="after")
    def _reason_required(self):
        self.case_number = self.case_number.strip()
        self.hold_reason = (self.hold_reason or self.justification or "").strip()
        if not self.case_number or not self.hold_reason:
            raise ValueError("case_number and a hold reason (hold_reason or justification) are required")
        return self


class LegalHoldReleaseIn(BaseModel):
    release_reason: Optional[str] = Field(None, max_length=4000)
    release_notes: Optional[str] = Field(None, max_length=4000)

    @model_validator(mode="after")
    def _reason_required(self):
        self.release_reason = (self.release_reason or self.release_notes or "").strip()
        if not self.release_reason:
            raise ValueError("A release reason (release_reason or release_notes) is required")
        return self


class PurgeDryRunIn(BaseModel):
    older_than_days: int = 365


class PurgeExecuteIn(BaseModel):
    older_than_days: int = 365
    confirmation_phrase: str


class AuditAccessLogIn(BaseModel):
    session_id: Optional[str] = None
    referrer: Optional[str] = None

