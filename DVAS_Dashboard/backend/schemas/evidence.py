from pydantic import BaseModel, Field

class LegalHoldIn(BaseModel):
    reason: str

class LegalHoldReleaseIn(BaseModel):
    reason: str

class DirectExportIn(BaseModel):
    justification: str

class DisposeIn(BaseModel):
    reason: str = Field(..., min_length=20)
