"""
Schemas for ML models, watchlists, and presets.
"""
from typing import List
from pydantic import BaseModel


class ModelUpdateIn(BaseModel):
    active: List[str]


class WatchlistAddIn(BaseModel):
    plate: str
    reason: str = ""
    vehicle_type: str = ""


class PresetCreateIn(BaseModel):
    name: str
    filters: dict
    is_shared: bool = False
