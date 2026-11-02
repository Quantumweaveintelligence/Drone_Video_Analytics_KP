"""Schemas for cases (evidence plan E4)."""
from typing import Optional
from pydantic import BaseModel, Field


class CaseCreateIn(BaseModel):
    case_number: str = Field(..., min_length=1, max_length=100)
    fir_number: Optional[str] = None
    police_station: Optional[str] = None
    title: Optional[str] = None
    jurisdiction: Optional[str] = None


class CaseEvidenceIn(BaseModel):
    evidence_id: str = Field(..., min_length=1)
