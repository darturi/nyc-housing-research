from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session as DbSession

from app.auth.sessions import get_session_by_token, session_is_valid, utc_now
from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User

settings = get_settings()
DbDependency = Annotated[DbSession, Depends(get_db)]
SessionCookie = Annotated[
    str | None,
    Cookie(alias=settings.session_cookie_name),
]


def get_current_user(
    db: DbDependency,
    session_token: SessionCookie = None,
) -> User:
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    session = get_session_by_token(db, session_token)
    if not session_is_valid(session):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    session.last_seen_at = utc_now()
    db.commit()
    return session.user


def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user
