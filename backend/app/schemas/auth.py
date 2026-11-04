from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = Field(default=None, max_length=30)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: int
    email: str
    name: str
    phone: str | None
    photo_url: str | None
    is_platform_admin: bool
    is_active: bool
    created_at: datetime


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    photo_url: str | None = Field(default=None, max_length=500)
    new_password: str | None = Field(default=None, min_length=8, max_length=128)
    current_password: str | None = None


class PlatformUserUpdate(BaseModel):
    is_active: bool | None = None
    is_platform_admin: bool | None = None
