"""Pembacaan field payload Accurate yang dipakai bersama SO dan Delivery Order.

Accurate mengirim sebagian field sebagai objek lookup (``{"id": .., "name": ..}``)
dan sebagian sebagai string. Helper di sini mengubahnya menjadi label bisnis tanpa
pernah menghasilkan ``"{'name': ...}"`` atau ``"[object Object]"`` di UI.
"""

from __future__ import annotations

import ast
import json
from typing import Any

_DICT_NAME_KEYS = (
    "name",
    "paymentTermName",
    "shipmentName",
    "customerName",
    "unitName",
    "no",
    "number",
    "code",
    "title",
)


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list, tuple)):
        return default
    text = str(value).strip()
    if text == "[object Object]":
        return default
    return text


def _label_from_mapping(mapping: dict[str, Any], default: str = "") -> str:
    for key in _DICT_NAME_KEYS:
        hit = mapping.get(key)
        if hit is None or isinstance(hit, (dict, list, tuple)):
            continue
        text = as_text(hit)
        if text:
            return text
    return default


def _parse_dict_repr(text: str) -> dict[str, Any] | None:
    trimmed = text.strip()
    if not (trimmed.startswith("{") and trimmed.endswith("}")):
        return None
    for loader in (
        lambda s: ast.literal_eval(s),
        lambda s: json.loads(s.replace("'", '"')),
    ):
        try:
            parsed = loader(trimmed)
        except (ValueError, SyntaxError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def business_label(value: Any, default: str = "") -> str:
    """Object Accurate → label bisnis; kosong jika tidak ada nama."""
    if value is None:
        return default
    if isinstance(value, dict):
        return _label_from_mapping(value, default)
    if isinstance(value, (list, tuple)):
        parts = [business_label(item) for item in value]
        joined = ", ".join(p for p in parts if p)
        return joined or default
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "[object Object]":
            return default
        parsed = _parse_dict_repr(text)
        if parsed is not None:
            return _label_from_mapping(parsed, default)
        return text
    return as_text(value, default)


def nested_label(obj: Any, *keys: str) -> str:
    """Label pertama yang terisi dari beberapa kandidat key pada satu objek."""
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        hit = obj.get(key)
        if hit is None:
            continue
        if isinstance(hit, dict):
            name = business_label(hit)
            if name:
                return name
            continue
        text = as_text(hit)
        if text:
            return text
    return ""
