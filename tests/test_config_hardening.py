import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def test_production_requires_explicit_fake_provider_allowance():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url=SecretStr("postgresql+psycopg://user:pass@db/app"),
            session_cookie_secure=True,
            rate_limit_enabled=True,
            embedding_provider="fake",
            answer_llm_provider="fake",
        )


def test_non_fake_embedding_requires_api_key():
    with pytest.raises(ValidationError):
        Settings(
            database_url=SecretStr("sqlite+pysqlite:///:memory:"),
            embedding_provider="openai",
            embedding_api_key=None,
        )
