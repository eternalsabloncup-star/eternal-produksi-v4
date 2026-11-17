#!/usr/bin/env python3
"""Manual MongoDB bootstrap for Eternal Production V4.

Usage examples:
  python scripts/bootstrap_mongo.py --ensure-indexes
  python scripts/bootstrap_mongo.py --create-admin

Never run from API startup, Docker app entrypoint, or deploy.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.bootstrap import admin_exists, create_admin_user, init_indexes  # noqa: E402
from app.config import clear_settings_cache, get_settings  # noqa: E402
from app.db import close_client, get_db  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap MongoDB indexes and/or first admin (manual only)."
    )
    parser.add_argument(
        "--ensure-indexes",
        action="store_true",
        help="Create users/sessions indexes idempotently.",
    )
    parser.add_argument(
        "--create-admin",
        action="store_true",
        help="Create the first admin via secure interactive password input.",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Admin email (optional; prompted if omitted).",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Admin display name (optional; prompted if omitted).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.ensure_indexes and not args.create_admin:
        print("Pilih minimal satu aksi: --ensure-indexes dan/atau --create-admin", file=sys.stderr)
        return 2

    # Password must never come from argv.
    if any(flag.startswith("--password") for flag in sys.argv[1:]):
        print("Password tidak boleh diberikan lewat argumen CLI.", file=sys.stderr)
        return 2

    clear_settings_cache()
    settings = get_settings()
    db = get_db(settings)

    if args.ensure_indexes:
        created = init_indexes(db)
        print("Indexes OK:")
        for collection, names in created.items():
            print(f"  {collection}: {', '.join(names)}")

    if args.create_admin:
        if admin_exists(db):
            print(
                "Admin sudah tersedia. Operasi ditolak tanpa prosedur khusus Owner.",
                file=sys.stderr,
            )
            return 1

        email = args.email or input("Email admin: ").strip()
        display_name = args.display_name or input("Nama tampilan: ").strip()
        password = getpass.getpass("Kata sandi admin (min 12 karakter): ")
        confirm = getpass.getpass("Ulangi kata sandi: ")
        if password != confirm:
            print("Kata sandi tidak cocok.", file=sys.stderr)
            return 1

        # Optional non-logging env fallback for automation in isolated test only.
        # Production operators should use interactive getpass.
        if os.environ.get("BOOTSTRAP_ALLOW_ENV_PASSWORD") == "1" and not password:
            password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")

        try:
            user = create_admin_user(
                db,
                email=email,
                display_name=display_name,
                password=password,
            )
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print(
            f"Admin dibuat: {user['email_normalized']} ({user['display_name']}) id={user['id']}"
        )

    close_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
