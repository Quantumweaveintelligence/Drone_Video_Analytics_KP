from typing import Literal
from pydantic import BaseModel


class ReportGenerateIn(BaseModel):
    model_config = {"extra": "allow"}
    report_type: Literal["daily_incident", "weekly_response", "drone_utilisation", "mission_after_action"]
    params: dict = {}

