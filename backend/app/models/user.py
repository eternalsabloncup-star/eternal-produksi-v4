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
    # super_admin | admin | user | custom role_key — never include password fields
    role: str
    # Peran operasional (production-head, designer, …) — opsional.
    operational_role: str | None = None
    branch_ids: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class MeResponse(BaseModel):
    user: UserPublic
    csrf_token: str
    # Capability UI (otorisasi tetap di backend).
    capabilities: dict[str, bool] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    message: str


def normalize_email(email: str) -> str:
    return email.strip().lower()
