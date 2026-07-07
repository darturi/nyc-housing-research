from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from app.core.config import get_settings

password_hasher = PasswordHasher()


def validate_password_strength(password: str) -> None:
    settings = get_settings()
    if not password or not password.strip():
        raise ValueError("Password cannot be empty.")
    if len(password) < settings.password_min_length:
        raise ValueError(
            f"Password must be at least {settings.password_min_length} characters."
        )


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False
