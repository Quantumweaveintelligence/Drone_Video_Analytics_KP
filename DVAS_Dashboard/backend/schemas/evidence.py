from typing import Literal, Optional

from pydantic import BaseModel, Field

class LegalHoldIn(BaseModel):
    reason: str

class LegalHoldReleaseIn(BaseModel):
    reason: str

class DirectExportIn(BaseModel):
    justification: str = Field(..., min_length=1)
    case_number: str = Field(..., min_length=1)    # E4: a direct export must name the case

class DisposeIn(BaseModel):
    reason: str = Field(..., min_length=20)


class AckIn(BaseModel):
    """R1: optional disposition when acknowledging an alert."""
    disposition: Optional[Literal["true_positive", "false_positive", "duplicate", "handled"]] = None
    note: Optional[str] = Field(default=None, max_length=500)
