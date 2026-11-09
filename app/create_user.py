"""Create an application account interactively.

Example:
    python -m app.create_user admin@example.com --role admin
"""

import argparse
from getpass import getpass
import re
import sys

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import ALLOWED_ROLES, hash_password
from app.models.user import UserAccount


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Video Auditor user")
    parser.add_argument("email", help="Account email address")
    parser.add_argument(
        "--role", choices=sorted(ALLOWED_ROLES), default="operator",
        help="Account role (default: operator)",
    )
    args = parser.parse_args()
    email = args.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        parser.error("email must be a valid email address")

    password = getpass("New password (minimum 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 12 or len(password) > 128:
        print("Password must contain between 12 and 128 characters.", file=sys.stderr)
        return 2
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        existing = db.scalar(select(UserAccount).where(UserAccount.email == email))
        if existing is not None:
            print(f"Account already exists: {email}", file=sys.stderr)
            return 1
        user = UserAccount(
            email=email,
            password_hash=hash_password(password),
            role=args.role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"Created {args.role} account: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
