"""Application configuration — secrets never logged."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "production", "test"] = Field(
        default="development", alias="APP_ENV"
    )
    mongodb_uri: str = Field(alias="MONGODB_URI")
    mongodb_db: str = Field(default="eternal_produksi_v4", alias="MONGODB_DB")
    cookie_secure: bool | None = Field(default=None, alias="COOKIE_SECURE")
    session_ttl_seconds: int = Field(default=86400, alias="SESSION_TTL_SECONDS", ge=60)
    cookie_name: str = Field(default="eternal_session", alias="COOKIE_NAME")
    csrf_cookie_name: str = Field(default="eternal_csrf", alias="CSRF_COOKIE_NAME")
    cors_origins: str = Field(default="", alias="CORS_ORIGINS")
    login_rate_limit_attempts: int = Field(
        default=5, alias="LOGIN_RATE_LIMIT_ATTEMPTS", ge=1
    )
    login_rate_limit_window_seconds: int = Field(
        default=60, alias="LOGIN_RATE_LIMIT_WINDOW_SECONDS", ge=1
    )

    @field_validator("mongodb_uri")
    @classmethod
    def mongodb_uri_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("MONGODB_URI is required")
        return value.strip()

    @field_validator("mongodb_db")
    @classmethod
    def mongodb_db_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("MONGODB_DB is required")
        return value.strip()

    @model_validator(mode="after")
    def derive_cookie_secure(self) -> Settings:
        if self.cookie_secure is None:
            object.__setattr__(self, "cookie_secure", self.app_env == "production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        if not self.cors_origins.strip():
            return []
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]

    def safe_summary(self) -> dict[str, object]:
        """Non-secret config summary for diagnostics."""
        return {
            "app_env": self.app_env,
            "mongodb_db": self.mongodb_db,
            "cookie_secure": self.cookie_secure,
            "session_ttl_seconds": self.session_ttl_seconds,
            "cors_origins": self.cors_origin_list,
            "login_rate_limit_attempts": self.login_rate_limit_attempts,
            "login_rate_limit_window_seconds": self.login_rate_limit_window_seconds,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
