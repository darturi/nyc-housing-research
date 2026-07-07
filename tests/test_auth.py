from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.auth.dependencies import require_admin
from app.auth.password import hash_password, verify_password
from app.auth.sessions import (
    get_session_by_token,
    hash_session_token,
    session_is_valid,
    utc_now,
)
from app.db.session import SessionLocal
from app.models.session import Session
from app.models.user import User


def create_user(
    email: str,
    password: str,
    is_admin: bool = False,
    is_active: bool = True,
):
    with SessionLocal() as db:
        user = User(
            email=email.lower(),
            password_hash=hash_password(password),
            is_admin=is_admin,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_password_hash_verifies_correct_password():
    password_hash = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", password_hash)
    assert not verify_password("wrong horse battery staple", password_hash)
    assert password_hash != "correct horse battery staple"


def test_login_succeeds_and_sets_http_only_cookie():
    create_user("user@example.com", "correct horse battery staple")
    client = TestClient(auth_router_app())

    response = client.post(
        "/auth/login",
        json={"email": "USER@example.com", "password": "correct horse battery staple"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"
    assert "password_hash" not in response.text
    assert "nyc_housing_session=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_login_fails_with_generic_error_for_wrong_password():
    create_user("user@example.com", "correct horse battery staple")
    client = TestClient(auth_router_app())

    wrong_password_response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "wrong password value"},
    )
    unknown_user_response = client.post(
        "/auth/login",
        json={"email": "missing@example.com", "password": "wrong password value"},
    )

    assert wrong_password_response.status_code == 401
    assert unknown_user_response.status_code == 401
    assert wrong_password_response.json() == unknown_user_response.json()


def test_me_requires_valid_session():
    client = TestClient(auth_router_app())

    response = client.get("/auth/me")

    assert response.status_code == 401


def test_me_returns_current_user_with_valid_session():
    create_user("user@example.com", "correct horse battery staple")
    client = TestClient(auth_router_app())
    client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "correct horse battery staple"},
    )

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"


def test_logout_revokes_session_and_clears_access():
    create_user("user@example.com", "correct horse battery staple")
    client = TestClient(auth_router_app())
    login_response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "correct horse battery staple"},
    )
    token = client.cookies.get("nyc_housing_session")

    logout_response = client.post("/auth/logout")
    me_response = client.get("/auth/me")

    assert login_response.status_code == 200
    assert logout_response.status_code == 200
    assert me_response.status_code == 401
    with SessionLocal() as db:
        session = get_session_by_token(db, token)
        assert session is not None
        assert session.revoked_at is not None


def test_expired_session_returns_401():
    user = create_user("user@example.com", "correct horse battery staple")
    with SessionLocal() as db:
        session = Session(
            user_id=user.id,
            token_hash=hash_session_token("expired-token"),
            expires_at=utc_now() - timedelta(hours=1),
        )
        db.add(session)
        db.commit()

    client = TestClient(auth_router_app())
    client.cookies.set("nyc_housing_session", "expired-token")

    response = client.get("/auth/me")

    assert response.status_code == 401


def test_session_validity_accepts_timezone_aware_expiration():
    user = User(
        email="user@example.com",
        password_hash=hash_password("correct horse battery staple"),
        is_active=True,
    )
    session = Session(
        user=user,
        token_hash=hash_session_token("aware-token"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert session_is_valid(session)


def test_inactive_user_session_returns_401():
    create_user(
        "user@example.com",
        "correct horse battery staple",
        is_active=False,
    )
    client = TestClient(auth_router_app())

    login_response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "correct horse battery staple"},
    )

    assert login_response.status_code == 401


def test_require_admin_allows_admin_user():
    create_user("admin@example.com", "correct horse battery staple", is_admin=True)
    client = TestClient(admin_test_app())
    client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "correct horse battery staple"},
    )

    response = client.get("/admin-only")

    assert response.status_code == 200


def test_require_admin_rejects_normal_user():
    create_user("user@example.com", "correct horse battery staple")
    client = TestClient(admin_test_app())
    client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "correct horse battery staple"},
    )

    response = client.get("/admin-only")

    assert response.status_code == 403


def auth_router_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    return app


def admin_test_app() -> FastAPI:
    app = auth_router_app()

    @app.get("/admin-only")
    def admin_only(current_user: Annotated[User, Depends(require_admin)]):
        return {"email": current_user.email}

    return app
