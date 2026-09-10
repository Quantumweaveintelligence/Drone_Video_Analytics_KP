from pydantic import BaseModel

class LegalHoldIn(BaseModel):
    reason: str

class LegalHoldReleaseIn(BaseModel):
    reason: str

class DirectExportIn(BaseModel):
    justification: str
