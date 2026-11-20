"""Application configuration — secrets never logged."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: Literal["development", "production", "test"] = Field(
        default="development", alias="APP_ENV"
    )
    # Independen dari ERP lama: MONGO_URL / DB_NAME (alias MONGODB_*).
    mongodb_uri: str = Field(
        validation_alias=AliasChoices("MONGO_URL", "MONGODB_URI"),
    )
    mongodb_db: str = Field(
        default="eternal_v4",
        validation_alias=AliasChoices("DB_NAME", "MONGODB_DB"),
    )
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

    # Accurate api_token — account base (bukan host SO). Tidak di-log.
    accurate_base_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ACCURATE_BASE_URL",
            "ACCURATE_ACCOUNT_BASE",
            "V4_ACCURATE_BASE_URL",
            "V4_ACCURATE_ACCOUNT_BASE",
        ),
    )
    accurate_api_token: str = Field(
        default="",
        validation_alias=AliasChoices("ACCURATE_API_TOKEN", "V4_ACCURATE_API_TOKEN"),
    )
    accurate_api_signature_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ACCURATE_API_SIGNATURE_SECRET",
            "ACCURATE_SIGNATURE_SECRET",
            "V4_ACCURATE_API_SIGNATURE_SECRET",
        ),
    )
    # Pin numerik d.database.id — utamakan V4_* agar terpisah dari UUID ERP lama.
    accurate_db_id: str = Field(
        default="",
        validation_alias=AliasChoices("V4_ACCURATE_DB_ID", "ACCURATE_DB_ID"),
    )
    # Produksi: jangan izinkan Accurate tanpa pin kecuali di-override eksplisit.
    accurate_require_db_pin: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "V4_ACCURATE_REQUIRE_DB_PIN",
            "ACCURATE_REQUIRE_DB_PIN",
        ),
    )
    accurate_http_timeout_seconds: float = Field(
        default=30.0,
        alias="ACCURATE_HTTP_TIMEOUT_SECONDS",
        gt=0,
    )
    # Kill switch sinkronisasi Surat Jalan Accurate → Mongo V4.
    # Default OFF. Non-prod/prod hanya aktif setelah Owner/CEO menyalakan eksplisit.
    sj_sync_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("V4_SJ_SYNC_ENABLED", "SJ_SYNC_ENABLED"),
    )
    # Packing Non-Cetak (T1): boundary aktivasi eksplisit Owner.
    # Kosong/default = fitur OFF (tidak derive task Non-Cetak).
    # ISO-8601 UTC, contoh: 2026-11-20T17:00:00+00:00
    # Hanya PO dengan acceptance.accepted_at >= nilai ini yang eligible.
    non_cetak_packing_activated_at: str = Field(
        default="",
        validation_alias=AliasChoices(
            "V4_NON_CETAK_PACKING_ACTIVATED_AT",
            "NON_CETAK_PACKING_ACTIVATED_AT",
        ),
    )
    # T2+: Loading membandingkan item Non-Cetak ke ledger Packing (bukan SJ↔SJ).
    # Default OFF = jalur legacy self-compare tetap (aman deploy tanpa migrasi).
    # ON = fail-closed bila ledger Packing Non-Cetak tidak ada / qty kurang.
    loading_packing_ledger_enforced: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "V4_LOADING_PACKING_LEDGER_ENFORCED",
            "LOADING_PACKING_LEDGER_ENFORCED",
        ),
    )
    # T3+: derive tugas loading-sj per SJ + gate assignee.
    # Default OFF = Loading API lama tetap (pengiriman.confirm) tanpa task Loading baru.
    loading_sj_task_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "V4_LOADING_SJ_TASK_ENABLED",
            "LOADING_SJ_TASK_ENABLED",
        ),
    )
    # T5+: Selesai monitoring memakai checker fulfillment ketat.
    # Default OFF = membership Selesai mengikuti node status existing.
    strict_po_completion_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "V4_STRICT_PO_COMPLETION_ENABLED",
            "STRICT_PO_COMPLETION_ENABLED",
        ),
    )
    # Auto-sync Accurate — semua default OFF; worker one-shot terpisah dari FastAPI.
    po_auto_sync_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("V4_PO_AUTO_SYNC_ENABLED", "PO_AUTO_SYNC_ENABLED"),
    )
    receipt_auto_sync_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "V4_RECEIPT_AUTO_SYNC_ENABLED", "RECEIPT_AUTO_SYNC_ENABLED"
        ),
    )
    invoice_auto_sync_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "V4_INVOICE_AUTO_SYNC_ENABLED", "INVOICE_AUTO_SYNC_ENABLED"
        ),
    )
    # Interval target scheduler eksternal (detik). Bukan loop di dalam API process.
    accurate_auto_sync_interval_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "V4_ACCURATE_AUTO_SYNC_INTERVAL_SECONDS",
            "ACCURATE_AUTO_SYNC_INTERVAL_SECONDS",
        ),
        ge=15,
        le=3600,
    )
    # Date floor auto-sync Accurate (tanggal transaksi, Asia/Jakarta).
    # Live kontrak: filter.transDate BETWEEN val[0]..val[1] (dd/MM/yyyy).
    accurate_sync_start_date: str = Field(
        default="2026-11-01",
        validation_alias=AliasChoices(
            "ACCURATE_SYNC_START_DATE",
            "V4_ACCURATE_SYNC_START_DATE",
        ),
    )

    @field_validator("mongodb_uri")
    @classmethod
    def mongodb_uri_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("MONGO_URL or MONGODB_URI is required")
        return value.strip()

    @field_validator("mongodb_db")
    @classmethod
    def mongodb_db_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("DB_NAME or MONGODB_DB is required")
        return value.strip()

    @field_validator("accurate_sync_start_date")
    @classmethod
    def accurate_sync_start_date_iso(cls, value: str) -> str:
        text = (value or "").strip() or "2026-11-01"
        # Validasi ketat lewat helper bersama (tolak suffix / format non-ISO).
        from app.services.accurate_auto_sync.date_floor import parse_iso_date_floor

        return parse_iso_date_floor(text).isoformat()

    @model_validator(mode="after")
    def derive_cookie_secure(self) -> Settings:
        if self.cookie_secure is None:
            object.__setattr__(self, "cookie_secure", self.app_env == "production")
        return self

    @model_validator(mode="after")
    def derive_accurate_require_db_pin(self) -> Settings:
        """Produksi default wajib pin; development/test boleh tanpa pin untuk smoke."""
        if self.accurate_require_db_pin is None:
            object.__setattr__(
                self,
                "accurate_require_db_pin",
                self.app_env == "production",
            )
        return self

    @property
    def accurate_db_pin_required(self) -> bool:
        return bool(self.accurate_require_db_pin)

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
            "accurate_base_url_configured": bool(self.accurate_base_url.strip()),
            "accurate_api_token_configured": bool(self.accurate_api_token.strip()),
            "accurate_signature_configured": bool(
                self.accurate_api_signature_secret.strip()
            ),
            "accurate_db_id_configured": bool(self.accurate_db_id.strip()),
            "accurate_db_id_length": (
                len(self.accurate_db_id.strip()) if self.accurate_db_id.strip() else 0
            ),
            "accurate_require_db_pin": self.accurate_db_pin_required,
            "accurate_auth_mode": "api_token",
            "accurate_auth_headers": [
                "Authorization",
                "X-Api-Timestamp",
                "X-Api-Signature",
                "X-Session-ID (hanya jika session non-kosong)",
            ],
            "sj_sync_enabled": bool(self.sj_sync_enabled),
            # Flag diagnostik saja — parse/eligibility di po_tasks (invalid → OFF).
            "non_cetak_packing_activated": bool(
                str(self.non_cetak_packing_activated_at or "").strip()
            ),
            "loading_packing_ledger_enforced": bool(
                self.loading_packing_ledger_enforced
            ),
            "loading_sj_task_enabled": bool(self.loading_sj_task_enabled),
            "strict_po_completion_enabled": bool(self.strict_po_completion_enabled),
            "po_auto_sync_enabled": bool(self.po_auto_sync_enabled),
            "receipt_auto_sync_enabled": bool(self.receipt_auto_sync_enabled),
            "invoice_auto_sync_enabled": bool(self.invoice_auto_sync_enabled),
            "accurate_auto_sync_interval_seconds": int(
                self.accurate_auto_sync_interval_seconds
            ),
            "invoice_sync_contract": "verified_live_read_only",
            "receipt_sync_contract": "verified_live_read_only",
            "accurate_paths": {
                "api_token": "/api/api-token.do",
                "open_db": "/api/open-db.do",
                "so_list": "/accurate/api/sales-order/list.do",
                "so_detail": "/accurate/api/sales-order/detail.do",
                "do_list": "/accurate/api/delivery-order/list.do",
                "do_detail": "/accurate/api/delivery-order/detail.do",
                "si_list": "/accurate/api/sales-invoice/list.do",
                "si_detail": "/accurate/api/sales-invoice/detail.do",
                "sr_list": "/accurate/api/sales-receipt/list.do",
                "sr_detail": "/accurate/api/sales-receipt/detail.do",
            },
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
    try:
        from app.services.accurate_auto_sync.date_floor import clear_date_floor_cache

        clear_date_floor_cache()
    except Exception:  # noqa: BLE001 — cache helper opsional saat import parsial
        pass
