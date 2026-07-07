import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.models.session import Session
from app.models.user import User


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: DbSession, user: User, request: Request) -> str:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    session = Session(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=utc_now() + timedelta(hours=settings.session_ttl_hours),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    user.last_login_at = utc_now()
    db.add(session)
    db.commit()
    return token


def get_session_by_token(db: DbSession, token: str) -> Session | None:
    token_hash = hash_session_token(token)
    return db.scalar(select(Session).where(Session.token_hash == token_hash))


def session_is_valid(session: Session | None) -> bool:
    if session is None:
        return False
    if session.revoked_at is not None:
        return False
    if utc_datetime(session.expires_at) <= utc_now():
        return False
    if not session.user.is_active:
        return False
    return True


def revoke_session(db: DbSession, token: str) -> None:
    session = get_session_by_token(db, token)
    if session is None:
        return
    session.revoked_at = utc_now()
    db.commit()


def delete_expired_sessions(db: DbSession) -> int:
    expired_sessions = db.scalars(
        select(Session).where(Session.expires_at <= utc_now())
    ).all()
    count = len(expired_sessions)
    for session in expired_sessions:
        db.delete(session)
    db.commit()
    return count
