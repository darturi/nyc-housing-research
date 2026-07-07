import argparse
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.password import hash_password, validate_password_strength
from app.db.session import SessionLocal
from app.models.user import User


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized:
        raise ValueError("Invalid email address.")
    return normalized


def prompt_password() -> str:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise ValueError("Passwords do not match.")
    validate_password_strength(password)
    return password


def create_user(email: str, is_admin: bool) -> None:
    normalized_email = normalize_email(email)
    password = prompt_password()
    with SessionLocal() as db:
        existing_user = db.scalar(select(User).where(User.email == normalized_email))
        if existing_user is not None:
            raise ValueError("A user with that email already exists.")

        user = User(
            email=normalized_email,
            password_hash=hash_password(password),
            is_admin=is_admin,
            is_active=True,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError("A user with that email already exists.") from exc

    role = "admin" if is_admin else "user"
    print(f"Created {role}: {normalized_email}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage application users.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("create-admin", "create-user"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--email", required=True)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        create_user(args.email, is_admin=args.command == "create-admin")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
