#!/usr/bin/env python3
"""Historical PO recovery allowlist — DRY_RUN default; APPLY production fail-closed.

Tidak menerima arbitrary PO ID. Tidak terhubung timer/startup/API.
Tidak mengubah date floor global.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Selective historical PO recovery (sealed allowlist only)."
    )
    p.add_argument(
        "--manifest",
        default=None,
        help="Path manifest sealed (default: package manifests).",
    )
    p.add_argument(
        "--expect-manifest-sha256",
        required=True,
        help="Wajib cocok dengan manifest_sha256 sealed.",
    )
    p.add_argument(
        "--expect-po-count",
        type=int,
        required=True,
        help=(
            "Exact sealed count: 80 untuk historical_po_recovery_20261121; "
            "10 untuk historical_parent_po_recovery_p8c; "
            "1 untuk historical_po_recovery_m924_po_71100."
        ),
    )
    p.add_argument(
        "--expect-batch-id",
        default="historical_po_recovery_20261121",
        help=(
            "Batch id exact dari registry sealed "
            "(historical_po_recovery_20261121 | historical_parent_po_recovery_p8c | "
            "historical_po_recovery_m924_po_71100)."
        ),
    )
    p.add_argument(
        "--expect-id-list-sha256",
        default=None,
        help="Wajib untuk batch P8C (id_list_sha256 sealed).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Mode APPLY (default: dry-run). Production tetap butuh guard tambahan.",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Wajib bersama --apply.",
    )
    p.add_argument(
        "--confirm-batch-id",
        default=None,
        help="Wajib exact batch id saat --apply.",
    )
    p.add_argument(
        "--i-understand-this-writes-production",
        action="store_true",
        help="Wajib untuk APPLY saat APP_ENV=production.",
    )
    p.add_argument(
        "--expect-live-sha",
        default=None,
        help="SHA40 live exact — wajib APPLY production.",
    )
    p.add_argument(
        "--live-repo",
        default="/home/eternaladmin/eternal-production-v4",
        help="Repo live untuk resolve HEAD (production APPLY).",
    )
    p.add_argument(
        "--report-out",
        default=None,
        help="Tulis laporan JSON (tanpa payload pelanggan).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, get_db
    from app.integrations.accurate.client import AccurateReadClient
    from app.services.historical_po_recovery import (
        HistoricalRecoveryError,
        RecoveryApproval,
        run_historical_po_recovery,
    )

    clear_settings_cache()
    close_client()
    settings = get_settings()
    mode = "apply" if args.apply else "dry_run"
    approval = RecoveryApproval(
        mode=mode,  # type: ignore[arg-type]
        expect_manifest_sha256=args.expect_manifest_sha256,
        expect_po_count=int(args.expect_po_count),
        expect_batch_id=str(args.expect_batch_id),
        execute=bool(args.execute),
        i_understand_this_writes_production=bool(
            args.i_understand_this_writes_production
        ),
        expect_live_sha=args.expect_live_sha,
        confirm_batch_id=args.confirm_batch_id,
        expect_id_list_sha256=args.expect_id_list_sha256,
    )

    live_sha = None
    if mode == "apply" and (settings.app_env or "").lower() == "production":
        repo = Path(args.live_repo)
        try:
            import subprocess

            live_sha = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                text=True,
            ).strip()
        except Exception:  # noqa: BLE001
            live_sha = None

    try:
        db = get_db(settings)
        client = AccurateReadClient.from_settings(settings)
        report = run_historical_po_recovery(
            db,
            approval=approval,
            client=client,
            mongo_guard="production"
            if (settings.app_env or "").lower() == "production"
            else "test",
            manifest_path=Path(args.manifest) if args.manifest else None,
            app_env=settings.app_env,
            live_sha=live_sha,
        )
    except HistoricalRecoveryError as exc:
        out = {"ok": False, "code": exc.code, "error": str(exc)}
        print(json.dumps(out, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "code": "runtime_error", "error": type(exc).__name__},
                ensure_ascii=False,
            )
        )
        return 3

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_out:
        Path(args.report_out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
