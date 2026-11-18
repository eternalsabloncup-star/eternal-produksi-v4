"""Probe pin database numerik dari api-token.do — tanpa mencetak nilai ID/secret.

Alur:
1. Baca identitas dari api-token.do (tanpa pin).
2. Pilih kandidat numerik primer (``d.database.id`` bila ada).
3. Opsional tulis nilai pin ke file 0600 (bukan ke stdout/log).
4. Opsional verifikasi baca SO dengan pin tersebut + ekspektasi smoke.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.integrations.accurate.auth import (
    AccurateAuthError,
    DatabaseIdCandidate,
    classify_database_id,
    collect_database_id_candidates,
    match_database_pin,
)
from app.integrations.accurate.client import (
    PATH_API_TOKEN,
    AccurateReadClient,
    AccurateReadStatus,
)
from app.integrations.accurate.smoke_validate import (
    SmokeExpectations,
    validate_sales_order_smoke,
)


@dataclass
class DbPinProbeResult:
    ok: bool
    code: str
    primary_field: str | None = None
    primary_kind: str | None = None
    primary_type: str | None = None
    primary_length: int | None = None
    pin_file_written: bool = False
    pin_file_path: str | None = None
    so_verify_ok: bool | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def format_report(self) -> str:
        """Laporan aman — tidak berisi nilai ID, token, atau secret."""
        lines = [
            f"probe: {'PASS' if self.ok else 'FAIL'}",
            f"code: {self.code}",
        ]
        if self.primary_field:
            lines.append(
                "primary: "
                f"field={self.primary_field}, type={self.primary_type}, "
                f"kind={self.primary_kind}, length={self.primary_length}"
            )
        if self.pin_file_path is not None:
            lines.append(
                f"pin_file: written={self.pin_file_written} path={self.pin_file_path}"
            )
        if self.so_verify_ok is not None:
            lines.append(f"so_verify: {'ok' if self.so_verify_ok else 'FAIL'}")
        if self.diagnostics:
            lines.append("diagnostics:")
            for row in self.diagnostics:
                parts = [
                    f"field={row.get('field')}",
                    f"type={row.get('type')}",
                    f"kind={row.get('kind')}",
                    f"length={row.get('length')}",
                ]
                if "match" in row:
                    parts.append(f"match={row.get('match')}")
                lines.append(f"  - {', '.join(parts)}")
        if self.errors:
            lines.append("errors:")
            for err in self.errors:
                lines.append(f"  - {err}")
        return "\n".join(lines)


def select_primary_numeric_candidate(
    candidates: list[DatabaseIdCandidate],
) -> DatabaseIdCandidate | None:
    """Utamakan ``d.database.id`` numerik; fallback kandidat numerik pertama."""
    for cand in candidates:
        if cand.field == "d.database.id" and cand.kind == "numeric":
            return cand
    for cand in candidates:
        if cand.kind == "numeric":
            return cand
    return None


def _is_private_dir(path: Path) -> bool:
    """Direktori privat: milik UID proses dan mode group/other tidak writable."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    if st.st_uid != os.getuid():
        return False
    if st.st_mode & 0o022:
        return False
    return True


def _prepare_private_parent(parent: Path) -> Path:
    """Pastikan parent ada, privat, bukan symlink."""
    if parent.exists() or parent.is_symlink():
        if parent.is_symlink():
            raise OSError(f"pin parent adalah symlink: {parent}")
        if not _is_private_dir(parent):
            raise PermissionError(
                f"pin parent harus milik user dan tidak group/world-writable: {parent}"
            )
        return parent
    # Buat rantai parent dengan 0700 bila belum ada.
    parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(parent, 0o700)
    if parent.is_symlink() or not _is_private_dir(parent):
        raise OSError(f"pin parent tidak aman setelah dibuat: {parent}")
    return parent


def write_pin_file(path: str | Path, pin_value: str) -> Path:
    """Tulis pin numerik ke file baru mode 0600 — tanpa log isi, anti-symlink.

    - Menolak jika path sudah ada (tidak overwrite diam-diam).
    - Menolak symlink pada target/parent.
    - Parent harus privat (milik UID, tidak group/world-writable) atau dibuat 0700.
    """
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    target = raw
    if target.exists() or target.is_symlink():
        raise FileExistsError(
            f"pin file sudah ada atau symlink — tolak overwrite diam-diam: {target}"
        )
    _prepare_private_parent(target.parent)

    data = (pin_value.strip() + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(target), flags, 0o600)
    try:
        os.write(fd, data)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    st = os.lstat(target)
    if stat.S_ISLNK(st.st_mode):
        try:
            target.unlink()
        except OSError:
            pass
        raise OSError("pin path berubah menjadi symlink setelah tulis — dibatalkan")
    return target


def _fetch_api_token_payload(client: AccurateReadClient) -> dict[str, Any]:
    """POST api-token.do sekali — payload mentah untuk kandidat ID."""
    if client.status != AccurateReadStatus.READY:
        raise AccurateAuthError(
            "Accurate belum siap (account base + token + signature).",
            code="accurate_credentials_missing",
        )
    account = client.account_base_url.rstrip("/")
    url = f"{account}{PATH_API_TOKEN}"
    with client._http(follow_redirects=False) as http:
        try:
            resp = http.post(url, headers=client._signed_request_headers(session=""))
        except httpx.TimeoutException as exc:
            raise TimeoutError("Timeout Accurate api-token.do") from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"api-token.do unreachable: {exc.__class__.__name__}"
            ) from exc
        if resp.status_code >= 400:
            raise AccurateAuthError(
                f"api-token.do HTTP {resp.status_code}",
                code="accurate_api_token_rejected",
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise AccurateAuthError(
                "api-token.do response bukan JSON",
                code="accurate_api_token_invalid",
            ) from exc
        if not isinstance(payload, dict):
            raise AccurateAuthError(
                "api-token.do shape tidak dikenali",
                code="accurate_api_token_shape",
            )
        if payload.get("s") is False or payload.get("success") is False:
            raise AccurateAuthError(
                "api-token.do menolak kredensial (s/success=false).",
                code="accurate_api_token_business_reject",
            )
        return payload


def run_db_pin_probe(
    client: AccurateReadClient,
    *,
    pin_out_path: str | Path | None = None,
    verify_sales_order: bool = True,
    expectations: SmokeExpectations | None = None,
) -> DbPinProbeResult:
    """Probe + opsional tulis pin file + verifikasi SO (nilai ID tidak ke report)."""
    exp = expectations or SmokeExpectations()
    try:
        payload = _fetch_api_token_payload(client)
    except AccurateAuthError as exc:
        return DbPinProbeResult(
            ok=False,
            code=exc.code,
            diagnostics=list(exc.diagnostics),
            errors=[str(exc)],
        )
    except TimeoutError:
        return DbPinProbeResult(ok=False, code="timeout", errors=["timeout Accurate"])
    except ConnectionError as exc:
        return DbPinProbeResult(
            ok=False, code="connection_error", errors=[exc.__class__.__name__]
        )

    candidates = collect_database_id_candidates(payload)
    primary = select_primary_numeric_candidate(candidates)
    diagnostics = [c.safe_diag() for c in candidates]

    if primary is None:
        return DbPinProbeResult(
            ok=False,
            code="accurate_database_id_missing",
            diagnostics=diagnostics,
            errors=["tidak ada kandidat database id numerik di api-token.do"],
        )

    pin_value = primary.normalized
    if classify_database_id(pin_value) != "numeric":
        return DbPinProbeResult(
            ok=False,
            code="accurate_database_id_kind_mismatch",
            primary_field=primary.field,
            primary_kind=primary.kind,
            primary_type=primary.python_type,
            primary_length=primary.length,
            diagnostics=diagnostics,
            errors=["kandidat primer bukan numeric"],
        )

    pin_file_written = False
    pin_file_path: str | None = None
    if pin_out_path is not None:
        try:
            written = write_pin_file(pin_out_path, pin_value)
        except (FileExistsError, PermissionError, OSError) as exc:
            return DbPinProbeResult(
                ok=False,
                code="pin_file_unsafe",
                primary_field=primary.field,
                primary_kind=primary.kind,
                primary_type=primary.python_type,
                primary_length=primary.length,
                diagnostics=diagnostics,
                errors=[f"{exc.__class__.__name__}: tulis pin ditolak (aman)"],
            )
        pin_file_written = True
        pin_file_path = str(written)

    so_ok: bool | None = None
    if verify_sales_order:
        # Client baru dengan pin numerik — pastikan pin + baca PO sesuai ekspektasi.
        pinned = AccurateReadClient(
            account_base_url=client.account_base_url,
            api_token=client.api_token,
            signature_secret=client.signature_secret,
            db_id=pin_value,
            timeout_seconds=client.timeout_seconds,
            transport=client.transport,
        )
        try:
            detail = pinned.fetch_sales_order_detail(exp.accurate_id)
            check = validate_sales_order_smoke(detail, expectations=exp, auth_ok=True)
            so_ok = check.ok
            if not check.ok:
                return DbPinProbeResult(
                    ok=False,
                    code="so_verify_failed",
                    primary_field=primary.field,
                    primary_kind=primary.kind,
                    primary_type=primary.python_type,
                    primary_length=primary.length,
                    pin_file_written=pin_file_written,
                    pin_file_path=pin_file_path,
                    so_verify_ok=False,
                    diagnostics=diagnostics,
                    errors=list(check.errors),
                )
        except AccurateAuthError as exc:
            return DbPinProbeResult(
                ok=False,
                code=exc.code,
                primary_field=primary.field,
                primary_kind=primary.kind,
                primary_type=primary.python_type,
                primary_length=primary.length,
                pin_file_written=pin_file_written,
                pin_file_path=pin_file_path,
                so_verify_ok=False,
                diagnostics=list(exc.diagnostics) or diagnostics,
                errors=[f"auth saat verifikasi SO: {exc.code}"],
            )
        except Exception as exc:  # noqa: BLE001
            return DbPinProbeResult(
                ok=False,
                code="so_verify_error",
                primary_field=primary.field,
                primary_kind=primary.kind,
                primary_type=primary.python_type,
                primary_length=primary.length,
                pin_file_written=pin_file_written,
                pin_file_path=pin_file_path,
                so_verify_ok=False,
                diagnostics=diagnostics,
                errors=[exc.__class__.__name__],
            )

    # Sanity: pin yang ditulis harus match kandidat (tanpa echo nilai).
    pin_check = match_database_pin(pin_value, candidates)
    if not pin_check.ok:
        return DbPinProbeResult(
            ok=False,
            code=pin_check.code,
            primary_field=primary.field,
            primary_kind=primary.kind,
            primary_type=primary.python_type,
            primary_length=primary.length,
            pin_file_written=pin_file_written,
            pin_file_path=pin_file_path,
            so_verify_ok=so_ok,
            diagnostics=pin_check.diagnostics,
            errors=["pin primer gagal self-match"],
        )

    return DbPinProbeResult(
        ok=True,
        code="ok",
        primary_field=primary.field,
        primary_kind=primary.kind,
        primary_type=primary.python_type,
        primary_length=primary.length,
        pin_file_written=pin_file_written,
        pin_file_path=pin_file_path,
        so_verify_ok=so_ok,
        diagnostics=diagnostics,
    )
