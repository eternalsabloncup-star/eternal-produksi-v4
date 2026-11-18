"""Pydantic models for UI table display preferences (not business data)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

TABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}$")
COLUMN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MIN_WIDTH = 72
MAX_WIDTH = 800
MAX_COLUMNS = 50
PREFS_VERSION = 1


class TablePreferencesPayload(BaseModel):
    version: int = Field(default=PREFS_VERSION, ge=1, le=100)
    column_order: list[str] = Field(default_factory=list, max_length=MAX_COLUMNS)
    column_widths: dict[str, int] = Field(default_factory=dict)
    hidden_columns: list[str] = Field(default_factory=list, max_length=MAX_COLUMNS)

    @field_validator("column_order", "hidden_columns")
    @classmethod
    def validate_column_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not COLUMN_ID_RE.match(item):
                raise ValueError("column id tidak valid")
            if item in seen:
                continue
            seen.add(item)
            cleaned.append(item)
        return cleaned

    @field_validator("column_widths")
    @classmethod
    def validate_widths(cls, value: dict[str, Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        if len(value) > MAX_COLUMNS:
            raise ValueError("terlalu banyak column_widths")
        for key, raw in value.items():
            if not isinstance(key, str) or not COLUMN_ID_RE.match(key):
                raise ValueError("column id pada widths tidak valid")
            width = int(raw)
            if width < MIN_WIDTH or width > MAX_WIDTH:
                raise ValueError(
                    f"lebar kolom harus antara {MIN_WIDTH} dan {MAX_WIDTH}"
                )
            out[key] = width
        return out


class TablePreferencesResponse(TablePreferencesPayload):
    table_id: str


def validate_table_id(table_id: str) -> str:
    if not TABLE_ID_RE.match(table_id):
        raise ValueError("tableId tidak valid")
    return table_id
