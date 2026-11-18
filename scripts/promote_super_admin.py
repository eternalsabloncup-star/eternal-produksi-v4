#!/usr/bin/env python3
"""Promosi manual satu kali → Super Admin (Owner only).

Default: DRY-RUN (tidak menulis DB).

Identitas kanonik:
  email: prasnda21@gmail.com
  display_name: Dimas
  role: super_admin

Syarat target:
  Mongo host 127.0.0.1/localhost port 27018
  DB_NAME=eternal_v4

Contoh dry-run:
  MONGO_URL='mongodb://...@127.0.0.1:27018/eternal_v4?authSource=eternal_v4' \\
  DB_NAME=eternal_v4 \\
  PYTHONPATH=backend python3 scripts/promote_super_admin.py

Eksekusi nyata (butuh persetujuan Owner eksplisit):
  ... python3 scripts/promote_super_admin.py --execute --i-approve-owner-promotion

Tidak mengubah password_hash. Tidak membuat/menghapus user.
Tidak dijalankan dari deploy/startup.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import clear_settings_cache, get_settings  # noqa: E402
from app.db import close_client, get_db  # noqa: E402
from app.services.promote_super_admin import (  # noqa: E402
    CANONICAL_SUPER_ADMIN_DISPLAY_NAME,
    CANONICAL_SUPER_ADMIN_EMAIL,
    PromoteError,
    assert_promote_target_env,
    promote_to_super_admin,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Promosi akun existing → super_admin (manual Owner)."
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Tulis database (default: dry-run).",
    )
    p.add_argument(
        "--i-approve-owner-promotion",
        action="store_true",
        help="Wajib bersama --execute: konfirmasi persetujuan Owner.",
    )
    p.add_argument(
        "--email",
        default=CANONICAL_SUPER_ADMIN_EMAIL,
        help="Email target (default kanonik Owner).",
    )
    p.add_argument(
        "--display-name",
        default=CANONICAL_SUPER_ADMIN_DISPLAY_NAME,
        help="Display name target (default Dimas).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    dry_run = not args.execute

    if args.execute and not args.i_approve_owner_promotion:
        print(
            "Ditolak: --execute memerlukan --i-approve-owner-promotion "
            "(persetujuan Owner).",
            file=sys.stderr,
        )
        return 2

    # Tolak email non-kanonik kecuali Owner mengubah secara sadar via flag
    # dan hanya untuk dry-run exploration — eksekusi nyata hanya email kanonik.
    if args.execute and args.email.strip().lower() != CANONICAL_SUPER_ADMIN_EMAIL:
        print(
            "Ditolak: eksekusi nyata hanya untuk email kanonik "
            f"{CANONICAL_SUPER_ADMIN_EMAIL}.",
            file=sys.stderr,
        )
        return 2

    import os

    # Guard memakai env mentah dulu — gagal cepat sebelum Settings penuh.
    uri = (
        os.environ.get("MONGO_URL")
        or os.environ.get("MONGODB_URI")
        or ""
    )
    db_name = os.environ.get("DB_NAME") or os.environ.get("MONGODB_DB") or ""
    try:
        assert_promote_target_env(mongodb_uri=uri, mongodb_db=db_name)
    except PromoteError as exc:
        print(f"Target env ditolak: {exc}", file=sys.stderr)
        return 2

    os.environ.setdefault("COOKIE_SECURE", "false")
    os.environ.setdefault("APP_ENV", "development")
    clear_settings_cache()
    settings = get_settings()
    # Double-check setelah Settings (alias MONGO_URL/DB_NAME).
    try:
        assert_promote_target_env(
            mongodb_uri=settings.mongodb_uri,
            mongodb_db=settings.mongodb_db,
        )
    except PromoteError as exc:
        print(f"Target env ditolak: {exc}", file=sys.stderr)
        return 2

    db = get_db(settings)
    try:
        result = promote_to_super_admin(
            db,
            email=args.email,
            display_name=args.display_name,
            dry_run=dry_run,
        )
    except PromoteError as exc:
        print(str(exc), file=sys.stderr)
        close_client()
        return 1

    # Jangan cetak password_hash.
    print(json.dumps(result, indent=2, default=str))
    close_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
