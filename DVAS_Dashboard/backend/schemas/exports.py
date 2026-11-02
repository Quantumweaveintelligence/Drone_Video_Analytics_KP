"""
Schemas for evidence export requests and approvals.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class ExportRequestIn(BaseModel):
    """Export one evidence item, or a whole case (evidence plan E4)."""
    evidence_id: Optional[str] = None
    case_id: Optional[str] = None
    case_number: Optional[str] = None
    justification: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _one_target(self):
        if bool(self.evidence_id) == bool(self.case_id):
            raise ValueError("give exactly one of evidence_id or case_id")
        return self


class ExportDecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = ""
