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
    is_platform_admin: bool  # Super-Admin (conta só de gestão)
    is_active: bool
    has_password: bool = True
    google_linked: bool = False
    deleted_at: datetime | None = None
    created_at: datetime


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    photo_url: str | None = Field(default=None, max_length=500)
    new_password: str | None = Field(default=None, min_length=8, max_length=128)
    current_password: str | None = None


class AccountDeleteIn(BaseModel):
    confirm: str = Field(max_length=20, description="Digite EXCLUIR")
    password: str | None = Field(default=None, max_length=128, description="Obrigatória para contas com senha")


class PlatformUserUpdate(BaseModel):
    is_active: bool | None = None
    is_platform_admin: bool | None = None  # Super-Admin


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class GoogleCredentialIn(BaseModel):
    credential: str = Field(min_length=20, max_length=4096, description="ID token devolvido pelo Google")


class AuthConfigOut(BaseModel):
    google_client_id: str | None
