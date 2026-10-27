"""Create, list, disable and re-password operator accounts.

    python -m api.manage_user create  admin@example.com
    python -m api.manage_user passwd  admin@example.com
    python -m api.manage_user list
    python -m api.manage_user disable admin@example.com
    python -m api.manage_user enable  admin@example.com

Passwords are prompted for and never echoed, so they do not end up in shell
history, `ps` output, or the server's journal. Pass --password only in an
automated first-boot script, and be aware of where that script is stored.

This exists so credentials can be managed without hand-writing SQL and
bcrypt hashes at a psql prompt — the step most likely to be got wrong at 2am.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select

from dealer_scraper.db import SessionLocal
from dealer_scraper.models import User

from .auth import hash_password

MIN_PASSWORD_LENGTH = 10


def _prompt_password() -> str:
    """Ask twice, refuse anything short. Confirmation matters here: a typo
    creates an account nobody can sign in to, and the only fix is another
    trip to the server."""
    while True:
        first = getpass.getpass("Password: ")
        if len(first) < MIN_PASSWORD_LENGTH:
            print(f"  Too short — use at least {MIN_PASSWORD_LENGTH} characters.")
            continue
        if first != getpass.getpass("Confirm password: "):
            print("  Passwords do not match.")
            continue
        return first


def cmd_create(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    password = args.password or _prompt_password()
    with SessionLocal() as db:
        existing = db.execute(
            select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
        if existing:
            print(f"User {email} already exists (id={existing.id}). "
                  f"Use 'passwd' to change the password.", file=sys.stderr)
            return 1
        user = User(email=email, password_hash=hash_password(password),
                    full_name=args.name, is_active=True)
        db.add(user)
        db.commit()
        print(f"Created user {email} (id={user.id}).")
    return 0


def cmd_passwd(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    password = args.password or _prompt_password()
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
        if not user:
            print(f"No user {email}.", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        db.commit()
        print(f"Password updated for {email}. Existing sessions remain valid "
              f"until they expire.")
    return 0


def _set_active(email: str, active: bool) -> int:
    email = email.strip().lower()
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
        if not user:
            print(f"No user {email}.", file=sys.stderr)
            return 1
        user.is_active = active
        db.commit()
        print(f"{email} is now {'active' if active else 'disabled'}.")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    return _set_active(args.email, False)


def cmd_enable(args: argparse.Namespace) -> int:
    return _set_active(args.email, True)


def cmd_list(_args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        users = db.execute(select(User).order_by(User.id)).scalars().all()
        if not users:
            print("No users yet. Create one:\n"
                  "  python -m api.manage_user create you@example.com")
            return 0
        print(f"{'id':>4}  {'email':<36} {'active':<7} last login")
        for u in users:
            last = u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "never"
            print(f"{u.id:>4}  {u.email:<36} {str(u.is_active):<7} {last}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.manage_user",
                                     description="Manage admin-panel logins.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn, needs_pw in (("create", cmd_create, True), ("passwd", cmd_passwd, True),
                               ("disable", cmd_disable, False), ("enable", cmd_enable, False)):
        p = sub.add_parser(name)
        p.add_argument("email")
        if needs_pw:
            p.add_argument("--password", help="Skip the prompt (avoid: lands in shell history)")
            p.add_argument("--name", default=None, help="Display name")
        p.set_defaults(func=fn)

    sub.add_parser("list").set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
