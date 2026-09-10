from typing import Literal
from pydantic import BaseModel


class ReportGenerateIn(BaseModel):
    report_type: Literal["daily_incident", "weekly_response", "drone_utilisation", "mission_after_action"]
    params: dict = {}
