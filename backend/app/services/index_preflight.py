"""Preflight / require indeks delivery/task/audit — READ-ONLY terhadap target Mongo.

Tidak createIndex. Tidak migrasi. Tidak mencetak credential/URI.
Topologi audit: 27017 FROZEN, 27018 production READ ONLY, 27019 test OK.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pymongo.database import Database

from app.repositories.delivery_repository import (
    LOADING_ACTIVE,
    REQUIRED_DELIVERY_INDEX_SPECS,
)
from app.repositories.task_repository import REQUIRED_TASK_INDEX_SPECS
from app.services.audit_log import REQUIRED_AUDIT_INDEX_SPECS

FORBIDDEN_PROD_PORTS = {27017, 27018}
ALLOWED_TEST_PORTS = {27019}

# Cache verifikasi per DB: nama indeks yang sudah lolos cek spesifikasi.
_VERIFIED_SPECS: dict[str, frozenset[str]] = {}


class MissingRequiredIndexError(RuntimeError):
    """Indeks wajib hilang atau spek tidak cocok — mutasi ditolak (fail-closed)."""

    code = "MISSING_REQUIRED_INDEX"

    def __init__(
        self,
        message: str,
        *,
        missing: list[str] | None = None,
        conflict: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing = list(missing or [])
        self.conflict = list(conflict or [])


def clear_index_verification_cache(db_name: str | None = None) -> None:
    """Hapus cache verifikasi (uji / setelah maintenance indeks)."""
    if db_name is None:
        _VERIFIED_SPECS.clear()
    else:
        _VERIFIED_SPECS.pop(db_name, None)


def mark_index_specs_verified(db_name: str, names: list[str]) -> None:
    """Dipakai setelah ensure_* eksplisit (bootstrap/test) — bukan request path."""
    prev = _VERIFIED_SPECS.get(db_name, frozenset())
    _VERIFIED_SPECS[db_name] = prev | frozenset(names)


def extract_mongo_ports_from_uri(uri: str) -> list[int]:
    raw = (uri or "").strip()
    if not raw:
        return []
    ports: list[int] = []
    without_scheme = raw.split("://", 1)[-1]
    hosts_part = without_scheme.split("@")[-1].split("/")[0].split("?")[0]
    for host in hosts_part.split(","):
        host = host.strip()
        if not host or host.startswith("["):
            continue
        if ":" in host:
            try:
                ports.append(int(host.rsplit(":", 1)[-1]))
            except ValueError:
                continue
    return ports


def assert_preflight_target_safe(
    *,
    uri: str,
    db_name: str,
    allow_production_readonly: bool = False,
) -> dict[str, Any]:
    """Tolak target production kecuali mode audit read-only eksplisit."""
    ports = extract_mongo_ports_from_uri(uri)
    parsed = urlparse(uri if "://" in uri else f"mongodb://{uri}")
    host = (parsed.hostname or "").lower()

    if db_name in {"eternal_v4", "eternal_produksi_v4"} and not allow_production_readonly:
        raise PermissionError(
            "Preflight menolak DB production tanpa allow_production_readonly"
        )

    touches_forbidden = any(p in FORBIDDEN_PROD_PORTS for p in ports)
    if touches_forbidden and not allow_production_readonly:
        raise PermissionError(
            f"Preflight menolak port production/legacy "
            f"{sorted(set(ports) & FORBIDDEN_PROD_PORTS)}"
        )

    return {
        "ok": True,
        "host": host or None,
        "ports": ports,
        "db_name": db_name,
        "allow_production_readonly": allow_production_readonly,
        "writes": False,
    }


def _normalize_key_list(keys: Any) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if isinstance(keys, list):
        for item in keys:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                out.append((str(item[0]), int(item[1])))
            elif isinstance(item, dict):
                for k, v in item.items():
                    out.append((str(k), int(v)))
    elif isinstance(keys, dict):
        for k, v in keys.items():
            out.append((str(k), int(v)))
    return out


def index_matches_spec(existing: dict[str, Any], spec: dict[str, Any]) -> bool:
    """Cocokkan name + unique + keys + partialFilterExpression (bukan nama saja)."""
    if existing.get("name") != spec["name"]:
        return False
    if bool(existing.get("unique")) != bool(spec.get("unique")):
        return False
    if _normalize_key_list(existing.get("key")) != [
        (k, int(v)) for k, v in spec["keys"]
    ]:
        return False
    want_partial = spec.get("partialFilterExpression")
    if want_partial is not None:
        have_partial = existing.get("partialFilterExpression")
        if have_partial != want_partial:
            return False
    return True


def inspect_index_specs(db: Database, specs: list[dict[str, Any]]) -> dict[str, Any]:
    """Bandingkan indeks aktual vs spek — tanpa createIndex."""
    present: list[str] = []
    missing: list[str] = []
    conflict: list[dict[str, Any]] = []
    by_coll: dict[str, list[dict[str, Any]]] = {}

    for spec in specs:
        coll_name = str(spec["collection"])
        if coll_name not in by_coll:
            by_coll[coll_name] = list(db[coll_name].list_indexes())
        indexes = by_coll[coll_name]
        match = next((ix for ix in indexes if ix.get("name") == spec["name"]), None)
        if match is None:
            missing.append(spec["name"])
            continue
        if index_matches_spec(match, spec):
            present.append(spec["name"])
        else:
            conflict.append(
                {
                    "name": spec["name"],
                    "collection": coll_name,
                    "expected_unique": bool(spec.get("unique")),
                    "actual_unique": bool(match.get("unique")),
                    "expected_keys": [(k, int(v)) for k, v in spec["keys"]],
                    "actual_keys": _normalize_key_list(match.get("key")),
                    "expected_partial": spec.get("partialFilterExpression"),
                    "actual_partial": match.get("partialFilterExpression"),
                }
            )

    return {
        "ok": not missing and not conflict,
        "writes": False,
        "db_name": db.name,
        "required": [s["name"] for s in specs],
        "present": present,
        "missing": missing,
        "conflict": conflict,
    }


def require_index_specs(db: Database, specs: list[dict[str, Any]]) -> None:
    """Fail-closed: indeks wajib harus cocok spek. Tidak pernah createIndex."""
    names = frozenset(str(s["name"]) for s in specs)
    cached = _VERIFIED_SPECS.get(db.name, frozenset())
    if names <= cached:
        return

    report = inspect_index_specs(db, specs)
    if report["missing"] or report["conflict"]:
        raise MissingRequiredIndexError(
            "Indeks Mongo wajib belum tersedia atau spek tidak cocok. "
            "Jalankan maintenance createIndex setelah persetujuan Owner "
            f"(missing={report['missing']}, conflict="
            f"{[c.get('name') for c in report['conflict']]}).",
            missing=report["missing"],
            conflict=report["conflict"],
        )
    mark_index_specs_verified(db.name, list(names))


def require_delivery_txn_indexes(db: Database) -> None:
    """Indeks wajib sebelum transaksi Loading/cancel/revisi SJ."""
    require_index_specs(
        db,
        list(REQUIRED_DELIVERY_INDEX_SPECS)
        + list(REQUIRED_TASK_INDEX_SPECS)
        + list(REQUIRED_AUDIT_INDEX_SPECS),
    )


def inspect_required_indexes(db: Database) -> dict[str, Any]:
    """Preflight lengkap delivery + task (+ audit) — tanpa createIndex."""
    specs = (
        list(REQUIRED_DELIVERY_INDEX_SPECS)
        + list(REQUIRED_TASK_INDEX_SPECS)
        + list(REQUIRED_AUDIT_INDEX_SPECS)
    )
    report = inspect_index_specs(db, specs)
    blockers: list[dict[str, Any]] = []
    if "uniq_accurate_sj_id" in report["missing"] or any(
        c["name"] == "uniq_accurate_sj_id" for c in report["conflict"]
    ):
        blockers.append(
            {
                "index": "uniq_accurate_sj_id",
                "note": "Periksa duplicate accurate_sj_id sebelum createIndex unique",
            }
        )
    return {
        **report,
        "duplicate_data_blockers": blockers,
        "partial_active_loading_status": LOADING_ACTIVE,
        "create_index_commands_for_owner_approval": [
            {
                "collection": s["collection"],
                "name": s["name"],
                "unique": bool(s.get("unique")),
                "keys": [(k, int(v)) for k, v in s["keys"]],
                "partialFilterExpression": s.get("partialFilterExpression"),
            }
            for s in specs
            if s["name"] in report["missing"]
            or any(c["name"] == s["name"] for c in report["conflict"])
        ],
    }
