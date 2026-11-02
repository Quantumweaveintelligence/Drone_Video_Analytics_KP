from typing import Literal
from pydantic import BaseModel


class ReportGenerateIn(BaseModel):
    """report_type is validated against services/report_types.REPORT_TYPES (R5)."""
    model_config = {"extra": "allow"}
    report_type: str
    params: dict = {}


class ReportDecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = ""
