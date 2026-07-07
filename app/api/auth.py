from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.auth.password import verify_password
from app.auth.sessions import create_session, revoke_session
from app.core.config import get_settings
from app.db.session import get_db
from app.limits.dependencies import enforce_login_limit, record_failed_login
from app.models.user import User
from app.schemas.auth import CurrentUserResponse, LoginRequest, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
DbDependency = Annotated[DbSession, Depends(get_db)]
CurrentUserDependency = Annotated[User, Depends(get_current_user)]


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        is_active=user.is_active,
    )


@router.post("/login", response_model=UserResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbDependency,
) -> UserResponse:
    enforce_login_limit(request, db)
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not user.is_active:
        record_failed_login(request, db)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not verify_password(payload.password, user.password_hash):
        record_failed_login(request, db)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    settings = get_settings()
    token = create_session(db, user, request)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=settings.session_ttl_hours * 60 * 60,
    )
    return user_response(user)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: DbDependency,
    current_user: CurrentUserDependency,
) -> dict[str, str]:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        revoke_session(db, token)
    response.delete_cookie(key=settings.session_cookie_name)
    return {"status": "ok"}


@router.get("/me", response_model=CurrentUserResponse)
def me(current_user: CurrentUserDependency) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=current_user.id,
        email=current_user.email,
        is_admin=current_user.is_admin,
        is_active=current_user.is_active,
    )
