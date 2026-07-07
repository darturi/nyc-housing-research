import pytest

from app.core.config import Settings


def test_production_requires_secure_session_cookie():
    with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
        Settings(
            app_env="production",
            database_url="postgresql+psycopg://user:pass@example.com/db",
            session_cookie_secure=False,
        )


def test_samesite_none_requires_secure_cookie():
    with pytest.raises(ValueError, match="SESSION_COOKIE_SAMESITE=none"):
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            session_cookie_samesite="none",
            session_cookie_secure=False,
        )
