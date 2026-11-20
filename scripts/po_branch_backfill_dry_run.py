#!/usr/bin/env python3
"""Dry-run klasifikasi cabang PO — DEFAULT tidak menulis.

Kategori:
  already_valid   — branch_code ada dan (opsional) ada di Master
  authoritative   — sumber Accurate otoritatif + mapping Master unik
  ambiguous       — lebih dari satu kandidat mapping
  missing_source  — tidak ada field cabang otoritatif
  conflict        — nilai bertentangan antar sumber

Tidak dijalankan dari startup/deploy. Write path memerlukan
ETERNAL_V4_PO_BRANCH_BACKFILL_WRITE=1 + --execute (tidak diimplementasikan
pada pekerjaan ini — sengaja absen agar fail-closed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Pastikan import backend tanpa side-effect DB pada import modul ini.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.object_auth import object_branch_code  # noqa: E402

# Field kandidat otoritatif saja — bukan customer/alamat/kota/PIC/PO number.
AUTH_KEYS = (
    "branch_code",
    "branch_id",
    "branchCode",
    "branchId",
    "branch_no",
    "branchNo",
    "accurate_branch_id",
)


def _walk_auth_candidates(doc: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def consider(path: str, obj: Any) -> None:
        if isinstance(obj, dict):
            # branch: {code|id|no|name}
            if "branch" in obj and isinstance(obj["branch"], dict):
                b = obj["branch"]
                for k in ("code", "id", "no"):
                    if b.get(k) not in (None, ""):
                        found.append((f"{path}.branch.{k}", str(b.get(k)).strip()))
                # name alone is NOT authoritative code — track separately as name-only
            for k, v in obj.items():
                if k in AUTH_KEYS and v not in (None, ""):
                    found.append((f"{path}.{k}" if path else k, str(v).strip()))
                elif isinstance(v, dict):
                    consider(f"{path}.{k}" if path else k, v)
                elif isinstance(v, list) and k == "header":
                    pass

    consider("", doc)
    header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    consider("header", header)
    raw = header.get("raw_sync_meta") if isinstance(header.get("raw_sync_meta"), dict) else {}
    consider("header.raw_sync_meta", raw)
    return found


def classify_po(
    doc: dict[str, Any],
    *,
    master_codes: set[str],
    name_to_codes: dict[str, set[str]],
) -> dict[str, Any]:
    accurate_id = str(doc.get("accurate_id") or "")
    existing = object_branch_code(doc)
    if existing:
        valid = (not master_codes) or existing in master_codes
        return {
            "accurate_id": accurate_id,
            "category": "already_valid" if valid else "conflict",
            "source_field": "branch_code",
            "proposed_code": existing,
            "note": None if valid else "existing_code_not_in_master",
        }

    candidates = _walk_auth_candidates(doc)
    # Name-only (non-authoritative for code) — detect branch.name without inventing code
    header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    branch_obj = None
    for root in (doc, header):
        if isinstance(root.get("branch"), dict):
            branch_obj = root["branch"]
            break
    name_only = None
    if isinstance(branch_obj, dict) and branch_obj.get("name") and not any(
        branch_obj.get(k) for k in ("code", "id", "no")
    ):
        name_only = str(branch_obj.get("name")).strip()

    if not candidates and not name_only:
        return {
            "accurate_id": accurate_id,
            "category": "missing_source",
            "source_field": None,
            "proposed_code": None,
            "note": None,
        }

    if name_only and not candidates:
        mapped = name_to_codes.get(name_only.upper(), set())
        if len(mapped) == 1:
            return {
                "accurate_id": accurate_id,
                "category": "authoritative",
                "source_field": "branch.name→master_name",
                "proposed_code": next(iter(mapped)),
                "note": "name_mapped_uniquely",
            }
        if len(mapped) > 1:
            return {
                "accurate_id": accurate_id,
                "category": "ambiguous",
                "source_field": "branch.name",
                "proposed_code": None,
                "note": f"name_maps_to={sorted(mapped)}",
            }
        return {
            "accurate_id": accurate_id,
            "category": "missing_source",
            "source_field": "branch.name",
            "proposed_code": None,
            "note": "name_without_master_mapping",
        }

    # Deduplicate candidate values
    values = {v.upper(): path for path, v in candidates if str(v).strip()}
    if len(values) > 1:
        return {
            "accurate_id": accurate_id,
            "category": "conflict",
            "source_field": ",".join(sorted(values.values())),
            "proposed_code": None,
            "note": f"values={sorted(values)}",
        }
    code, path = next(iter(values.items()))
    # Accurate branchId numerik ≠ kode Master — butuh keputusan mapping Owner.
    if path.endswith("accurate_branch_id") or path.endswith("branchId") or code.isdigit():
        return {
            "accurate_id": accurate_id,
            "category": "ambiguous",
            "source_field": path,
            "proposed_code": None,
            "note": f"accurate_branch_id={code}; master_mapping_required",
        }
    # Jika nilai tidak ada di Master — jangan mengarang mapping.
    if master_codes and code not in master_codes:
        return {
            "accurate_id": accurate_id,
            "category": "ambiguous",
            "source_field": path,
            "proposed_code": None,
            "note": "value_not_in_master",
        }
    if not re.match(r"^[A-Z0-9][A-Z0-9_-]{0,15}$", code):
        return {
            "accurate_id": accurate_id,
            "category": "ambiguous",
            "source_field": path,
            "proposed_code": None,
            "note": "value_not_code_shaped",
        }
    return {
        "accurate_id": accurate_id,
        "category": "authoritative",
        "source_field": path,
        "proposed_code": code,
        "note": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PO branch backfill dry-run")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URL") or os.environ.get("MONGODB_URI"))
    parser.add_argument("--db-name", default=os.environ.get("DB_NAME", "eternal_v4"))
    parser.add_argument("--allow-production-readonly", action="store_true")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--execute", action="store_true", help="DILARANG pada pekerjaan ini")
    args = parser.parse_args()

    if args.execute or os.environ.get("ETERNAL_V4_PO_BRANCH_BACKFILL_WRITE") == "1":
        print("ERROR: write/execute path tidak diaktifkan pada pekerjaan Branch Readiness.", file=sys.stderr)
        return 3

    uri = args.mongo_uri
    if not uri:
        print("ERROR: set MONGO_URL / --mongo-uri", file=sys.stderr)
        return 2
    if ":27017" in uri:
        print("ERROR: menolak Mongo 27017 (legacy)", file=sys.stderr)
        return 2
    if ":27018" in uri and not args.allow_production_readonly:
        print("ERROR: production 27018 memerlukan --allow-production-readonly", file=sys.stderr)
        return 2

    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    db = client[args.db_name]

    master = list(db["master_branches"].find({}, {"_id": 0, "code": 1, "name": 1, "is_active": 1}))
    master_codes = {str(b.get("code") or "").upper() for b in master if b.get("code")}
    name_to_codes: dict[str, set[str]] = {}
    for b in master:
        name = str(b.get("name") or "").strip().upper()
        code = str(b.get("code") or "").strip().upper()
        if name and code:
            name_to_codes.setdefault(name, set()).add(code)

    rows = []
    for doc in db["accurate_sales_orders"].find({}):
        rows.append(
            classify_po(doc, master_codes=master_codes, name_to_codes=name_to_codes)
        )

    counts = Counter(r["category"] for r in rows)
    report = {
        "dry_run": True,
        "db_name": args.db_name,
        "total_po": len(rows),
        "master_branch_count": len(master_codes),
        "counts": dict(counts),
        "samples": {
            cat: [r["accurate_id"] for r in rows if r["category"] == cat][:8]
            for cat in sorted(counts)
        },
        "permission_risk": (
            "User terbatas akan DENIED pada PO missing_source/ambiguous/conflict "
            "setelah hardening PR #18. Elevated (ops.full / all_branches / SA) tetap bypass."
        ),
        "planned_change": "NONE in this run — Owner approval required before any write backfill.",
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
