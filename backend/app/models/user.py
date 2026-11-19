"""User and auth request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserPublic(BaseModel):
    id: str
    email: str
    display_name: str
    # super_admin | admin | user — never include password fields
    role: str
    # Peran operasional (production-head, designer, …) — opsional.
    operational_role: str | None = None


class MeResponse(BaseModel):
    user: UserPublic
    csrf_token: str


class MessageResponse(BaseModel):
    message: str


def normalize_email(email: str) -> str:
    return email.strip().lower()
