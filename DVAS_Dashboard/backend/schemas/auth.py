"""
Authentication and user management schemas.
"""
from typing import List, Literal, Optional
from pydantic import BaseModel, EmailStr

Role = Literal[
    "drone_operator",
    "control_room_officer",
    "senior_officer",
    "system_administrator",
]

class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserCreateIn(BaseModel):
    email: EmailStr
    name: str
    role: Role
    jurisdiction: str = ""
    mission_scope: List[str] = []
    password: str = "dvas1234"


class UserPatchIn(BaseModel):
    name: Optional[str] = None
    role: Optional[Role] = None
    jurisdiction: Optional[str] = None
    mission_scope: Optional[List[str]] = None
    suspended: Optional[bool] = None
