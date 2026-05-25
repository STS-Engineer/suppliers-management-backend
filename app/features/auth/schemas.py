"""Authentication schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class SignInRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class AuthenticatedUser(BaseModel):
    email: str
    full_name: str
    access_profile: str


class SignInResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    user: AuthenticatedUser


class AccessIdentityResponse(BaseModel):
    id_identity: int
    email: str
    full_name: str
    access_profile: str
    auth_source: str
    external_subject: str | None = None
    external_directory: str | None = None
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AccessIdentityCreateRequest(BaseModel):
    email: str = Field(min_length=5, max_length=200)
    full_name: str = Field(min_length=2, max_length=200)
    access_profile: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    auth_source: str = Field(default="local", min_length=2, max_length=50)
    external_subject: str | None = Field(default=None, max_length=255)
    external_directory: str | None = Field(default=None, max_length=150)
    is_active: bool = True


class AccessIdentityUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    access_profile: str | None = Field(default=None, min_length=2, max_length=100)
    auth_source: str | None = Field(default=None, min_length=2, max_length=50)
    external_subject: str | None = Field(default=None, max_length=255)
    external_directory: str | None = Field(default=None, max_length=150)
    is_active: bool | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class PasswordOperationResponse(BaseModel):
    email: str
    changed_at: datetime
