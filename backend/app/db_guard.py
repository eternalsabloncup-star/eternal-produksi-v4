"""Guard fail-closed untuk target MongoDB V4 (bukan ERP legacy).

Owner: Mongo V4 test di 127.0.0.1:27018; ERP lama di 27017 — diblokir untuk sync.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# Nama DB yang tidak boleh ditarget sync Accurate → V4.
_LEGACY_OR_OPS_DB_NAMES = frozenset(
    {
        "eternal_v4",  # ops V4 — butuh persetujuan Owner terpisah
        "eternal_produksi_v4",
        "eternal_produksi",
        "eternal",
        "erp",
        "production",
        "prod",
    }
)

_PORT_RE = re.compile(r":(\d+)(?:[/?,]|$)")


class UnsafeMongoTargetError(PermissionError):
    """Target Mongo tidak aman untuk sync Accurate → V4."""

    def __init__(self, message: str, *, code: str = "unsafe_mongo_target") -> None:
        super().__init__(message)
        self.code = code


def extract_mongo_ports(uri: str) -> list[int]:
    """Ambil port dari URI mongodb:// (bukan nilai secret)."""
    text = (uri or "").strip()
    if not text:
        return []
    ports: list[int] = []
    # urlparse butuh netloc (host + port opsional).
    normalized = text
    if text.startswith("mongodb+srv://"):
        # SRV biasanya tanpa port eksplisit — tidak dianggap 27017.
        return []
    if "://" in text:
        parsed = urlparse(text)
        if parsed.port is not None:
            ports.append(int(parsed.port))
        # Multi-host: host1:27017,host2:27018
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.split("@", 1)[1]
        for match in _PORT_RE.finditer(netloc):
            ports.append(int(match.group(1)))
    else:
        for match in _PORT_RE.finditer(text):
            ports.append(int(match.group(1)))
    # Unik, urutan stabil
    seen: set[int] = set()
    out: list[int] = []
    for p in ports:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def extract_mongo_auth_source(uri: str) -> str | None:
    """Ambil authSource dari query URI (bukan password)."""
    text = (uri or "").strip()
    if not text or "://" not in text:
        return None
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    values = query.get("authSource") or query.get("authsource") or []
    if not values:
        return None
    value = str(values[0]).strip()
    return value or None


def assert_mongo_auth_source_contract(
    *,
    uri: str,
    db_name: str,
    app_env: str = "development",
    expected_auth_source: str | None = None,
) -> None:
    """authSource harus sesuai DB tempat user Mongo dibuat.

    Untuk sync VPS test: buat user di ``eternal_v4_test_manual`` lalu
    ``authSource=eternal_v4_test_manual`` — jangan memakai user ops ``eternal_v4``
    tanpa grant yang tepat.

    APP_ENV=test (pytest CI) longgar (boleh authSource=admin root test).
    """
    env = (app_env or "").strip().lower()
    if env == "test":
        return

    auth = extract_mongo_auth_source(uri)
    if not auth:
        raise UnsafeMongoTargetError(
            "MONGO_URL wajib memuat authSource yang sesuai database user.",
            code="mongo_auth_source_missing",
        )
    expected = (expected_auth_source or db_name or "").strip()
    if not expected:
        raise UnsafeMongoTargetError(
            "DB_NAME kosong saat validasi authSource.",
            code="mongo_db_name_missing",
        )
    if auth != expected:
        raise UnsafeMongoTargetError(
            "authSource URI tidak cocok dengan database user test "
            "(kontrak: authSource = DB tempat user dibuat; "
            "jangan memakai user operasional V4 tanpa grant test).",
            code="mongo_auth_source_mismatch",
        )


def assert_safe_mongo_sync_target(
    *,
    uri: str,
    db_name: str,
    app_env: str = "development",
    expected_auth_source: str | None = None,
) -> None:
    """Gagal-tertutup jika URI/DB mengarah ke legacy atau ops berbahaya.

    Pengecualian sempit: APP_ENV=test + DB ``eternal_v4_test_*`` boleh di port
    mana pun (termasuk 27017 di CI lokal) — bukan Mongo ERP produksi.
    """
    name = (db_name or "").strip()
    env = (app_env or "").strip().lower()

    if not name:
        raise UnsafeMongoTargetError(
            "DB_NAME kosong — sync Accurate diblokir.",
            code="mongo_db_name_missing",
        )

    if name in _LEGACY_OR_OPS_DB_NAMES:
        raise UnsafeMongoTargetError(
            "Database target legacy/ops diblokir untuk sync Accurate → V4. "
            "Gunakan DB_NAME test terisolasi (eternal_v4_test_*).",
            code="mongo_legacy_db_blocked",
        )

    is_isolated_test_db = name.startswith("eternal_v4_test_")
    ports = extract_mongo_ports(uri)

    if 27017 in ports:
        # Port ERP lama Owner — hanya izinkan pytest DB terisolasi.
        if env == "test" and is_isolated_test_db:
            return
        raise UnsafeMongoTargetError(
            "Mongo port 27017 diblokir untuk sync Accurate → V4 "
            "(cadangan ERP lama). Gunakan 127.0.0.1:27018 + DB test.",
            code="mongo_legacy_port_blocked",
        )

    if env != "test" and not is_isolated_test_db:
        raise UnsafeMongoTargetError(
            "Sync Accurate hanya ke DB test terisolasi (eternal_v4_test_*).",
            code="mongo_non_test_db_blocked",
        )

    # Kontrak authSource untuk jalur VPS/non-pytest.
    assert_mongo_auth_source_contract(
        uri=uri,
        db_name=name,
        app_env=env,
        expected_auth_source=expected_auth_source,
    )
