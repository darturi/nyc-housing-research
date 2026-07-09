from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DbSession

from app.auth.sessions import get_session_by_token, session_is_valid, utc_now
from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
settings = get_settings()

DbDependency = Annotated[DbSession, Depends(get_db)]


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: DbDependency):
    if current_user_from_request(request, db) is not None:
        return RedirectResponse(url="/app", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: DbDependency):
    if current_user_from_request(request, db) is not None:
        return RedirectResponse(url="/app", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "app_name": settings.app_name,
        },
    )


@router.get("/app", response_class=HTMLResponse)
def app_page(request: Request, db: DbDependency):
    current_user = current_user_from_request(request, db)
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "app.html",
        {
            "app_name": settings.app_name,
            "current_user": current_user,
        },
    )


def current_user_from_request(request: Request, db: DbSession) -> User | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    session = get_session_by_token(db, token)
    if not session_is_valid(session):
        return None
    session.last_seen_at = utc_now()
    db.commit()
    return session.user
