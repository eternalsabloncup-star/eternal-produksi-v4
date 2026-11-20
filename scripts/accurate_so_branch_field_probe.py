#!/usr/bin/env python3
"""Probe read-only field cabang pada Sales Order Accurate.

- Hanya GET list/detail resmi.
- Tidak menulis Accurate.
- Tidak menulis / sync Mongo.
- Tidak mencetak token/credential.
- Tidak mengaktifkan auto-sync.

Output: matriks FIELD / SOURCE / SAMPLE (disanitasi) / CONFIDENCE / SUPPORT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


BRANCHISH_KEYS = {
    "branch",
    "branchId",
    "branchName",
    "branchCode",
    "branchNo",
    "branch_id",
    "branch_code",
    "branch_name",
    "cabang",
}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value[:5]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    if len(text) > 80:
        return text[:40] + "…" + text[-10:]
    return text


def _find_branchish(obj: Any, path: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k in BRANCHISH_KEYS or "branch" in k.lower() or k.lower() == "cabang":
                found.append((p, v))
            found.extend(_find_branchish(v, p))
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:3]):
            found.extend(_find_branchish(item, f"{path}[{i}]"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--so-id", default="", help="Optional Accurate SO id for detail.do")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    # Refuse sync write flags
    for k in ("V4_PO_AUTO_SYNC_ENABLED", "V4_SJ_SYNC_ENABLED"):
        if os.environ.get(k, "0") not in ("0", "false", "False", ""):
            # Probe still read-only; just warn
            print(f"WARN: {k} is not 0 — probe tetap read-only, tidak sync.", file=sys.stderr)

    from app.integrations.accurate.client import AccurateReadClient

    client = AccurateReadClient.from_settings()
    matrix: list[dict[str, Any]] = []

    # LIST
    rows = client.fetch_sales_order_list(params={"sp.page": 1, "sp.pageSize": 1})
    sample_list = rows[0] if isinstance(rows, list) and rows else None
    list_hits = _find_branchish(sample_list) if isinstance(sample_list, dict) else []
    if list_hits:
        for path, val in list_hits:
            matrix.append(
                {
                    "FIELD_ACCURATE": path,
                    "SOURCE_ENDPOINT": "sales-order/list.do",
                    "SAMPLE_VALUE": _sanitize(val),
                    "CONFIDENCE": "observed_live",
                    "MAPPING_MASTER_BRANCH": "NOT_MAPPED",
                    "STATUS": "SUPPORTED_FIELD_PRESENT",
                }
            )
    else:
        matrix.append(
            {
                "FIELD_ACCURATE": "(none)",
                "SOURCE_ENDPOINT": "sales-order/list.do",
                "SAMPLE_VALUE": None,
                "CONFIDENCE": "observed_live",
                "MAPPING_MASTER_BRANCH": "N/A",
                "STATUS": "NOT_FOUND",
            }
        )

    so_id = args.so_id
    if not so_id and isinstance(sample_list, dict):
        so_id = str(sample_list.get("id") or sample_list.get("accurate_id") or "")

    if so_id:
        detail = client.fetch_sales_order_detail(so_id)
        payload = detail if isinstance(detail, dict) else {}
        # unwrap common envelopes
        for key in ("d", "salesOrder", "data"):
            inner = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(inner, dict) and (
                "id" in inner or "number" in inner or "detailItem" in inner
            ):
                payload = inner
                break
        detail_hits = _find_branchish(payload)
        if detail_hits:
            for path, val in detail_hits:
                status = "SUPPORTED_FIELD_PRESENT"
                if path.endswith(".name") or path.endswith("branchName"):
                    status = "NAME_ONLY_NOT_AUTHORITATIVE_CODE"
                matrix.append(
                    {
                        "FIELD_ACCURATE": path,
                        "SOURCE_ENDPOINT": f"sales-order/detail.do?id={so_id}",
                        "SAMPLE_VALUE": _sanitize(val),
                        "CONFIDENCE": "observed_live",
                        "MAPPING_MASTER_BRANCH": "NOT_MAPPED",
                        "STATUS": status,
                    }
                )
        else:
            matrix.append(
                {
                    "FIELD_ACCURATE": "(none)",
                    "SOURCE_ENDPOINT": f"sales-order/detail.do?id={so_id}",
                    "SAMPLE_VALUE": None,
                    "CONFIDENCE": "observed_live",
                    "MAPPING_MASTER_BRANCH": "N/A",
                    "STATUS": "NOT_FOUND",
                }
            )
    else:
        matrix.append(
            {
                "FIELD_ACCURATE": "(skipped)",
                "SOURCE_ENDPOINT": "sales-order/detail.do",
                "SAMPLE_VALUE": None,
                "CONFIDENCE": "no_so_id",
                "MAPPING_MASTER_BRANCH": "N/A",
                "STATUS": "NOT_FOUND",
            }
        )

    # Code path: normalize currently does not map branch → document that.
    matrix.append(
        {
            "FIELD_ACCURATE": "normalize_sales_order.branch*",
            "SOURCE_ENDPOINT": "backend/app/integrations/accurate/normalize.py",
            "SAMPLE_VALUE": None,
            "CONFIDENCE": "static_code_audit",
            "MAPPING_MASTER_BRANCH": "N/A",
            "STATUS": "NOT_SUPPORTED",
        }
    )

    report = {
        "so_id_probed": so_id or None,
        "matrix": matrix,
        "verdict": (
            "NOT_FOUND"
            if all(m["STATUS"] in {"NOT_FOUND", "NOT_SUPPORTED"} for m in matrix if m["SOURCE_ENDPOINT"].startswith("sales-order"))
            else "FIELDS_OBSERVED_SEE_MATRIX"
        ),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
