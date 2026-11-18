"""Authenticated UI table preference endpoints (display only)."""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import AuthContext, require_csrf, require_session
from app.db import get_db, table_preferences_collection
from app.models.table_prefs import (
    PREFS_VERSION,
    TablePreferencesPayload,
    TablePreferencesResponse,
    validate_table_id,
)

router = APIRouter(prefix="/api/me/table-preferences", tags=["table-preferences"])


def _user_oid(auth: AuthContext) -> ObjectId:
    return auth.user["_id"]


@router.get("/{table_id}", response_model=TablePreferencesResponse)
def get_table_preferences(
    table_id: str,
    auth: AuthContext = Depends(require_session),
) -> TablePreferencesResponse:
    try:
        tid = validate_table_id(table_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    db = get_db()
    doc = table_preferences_collection(db).find_one(
        {"user_id": _user_oid(auth), "table_id": tid}
    )
    if not doc:
        return TablePreferencesResponse(
            table_id=tid,
            version=PREFS_VERSION,
            column_order=[],
            column_widths={},
            hidden_columns=[],
        )
    return TablePreferencesResponse(
        table_id=tid,
        version=int(doc.get("version") or PREFS_VERSION),
        column_order=list(doc.get("column_order") or []),
        column_widths=dict(doc.get("column_widths") or {}),
        hidden_columns=list(doc.get("hidden_columns") or []),
    )


@router.put("/{table_id}", response_model=TablePreferencesResponse)
def put_table_preferences(
    table_id: str,
    body: TablePreferencesPayload,
    auth: AuthContext = Depends(require_csrf),
) -> TablePreferencesResponse:
    try:
        tid = validate_table_id(table_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Ensure payload column ids referenced in widths exist in order ∪ hidden ∪ widths keys only
    known = set(body.column_order) | set(body.hidden_columns) | set(body.column_widths)
    if len(known) > 50:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "terlalu banyak kolom pada preferensi",
        )

    now = datetime.now(timezone.utc)
    db = get_db()
    col = table_preferences_collection(db)
    col.update_one(
        {"user_id": _user_oid(auth), "table_id": tid},
        {
            "$set": {
                "user_id": _user_oid(auth),
                "table_id": tid,
                "version": body.version,
                "column_order": body.column_order,
                "column_widths": body.column_widths,
                "hidden_columns": body.hidden_columns,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return TablePreferencesResponse(
        table_id=tid,
        version=body.version,
        column_order=body.column_order,
        column_widths=body.column_widths,
        hidden_columns=body.hidden_columns,
    )


@router.delete("/{table_id}", response_model=dict)
def delete_table_preferences(
    table_id: str,
    auth: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        tid = validate_table_id(table_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    db = get_db()
    result = table_preferences_collection(db).delete_one(
        {"user_id": _user_oid(auth), "table_id": tid}
    )
    return {"deleted": bool(result.deleted_count)}
