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
