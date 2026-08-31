"""
Schemas for evidence export requests and approvals.
"""
from typing import Literal
from pydantic import BaseModel


class ExportRequestIn(BaseModel):
    evidence_id: str
    justification: str


class ExportDecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = ""
