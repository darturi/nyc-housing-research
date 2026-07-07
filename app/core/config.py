from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = Field(default="local")
    app_name: str = Field(default="nyc-housing-rag")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")
    database_url: SecretStr
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)
    healthcheck_timeout_seconds: int = Field(default=2, ge=1)
    session_cookie_name: str = Field(default="nyc_housing_session")
    session_cookie_secure: bool = Field(default=False)
    session_cookie_samesite: str = Field(default="lax")
    session_ttl_hours: int = Field(default=12, ge=1)
    password_min_length: int = Field(default=12, ge=8)
    artifact_storage_backend: str = Field(default="local")
    artifact_storage_path: str = Field(default=".artifacts")
    artifact_s3_bucket: str | None = None
    artifact_s3_prefix: str = Field(default="")
    artifact_s3_region: str | None = None
    artifact_s3_endpoint_url: str | None = None
    artifact_s3_access_key_id: SecretStr | None = None
    artifact_s3_secret_access_key: SecretStr | None = None
    ingestion_http_timeout_seconds: int = Field(default=30, ge=1)
    ingestion_http_max_retries: int = Field(default=2, ge=0)
    ingestion_user_agent: str = Field(default="nyc-housing-rag-mvp/0.1")
    hpd_violations_limit: int = Field(default=5000, ge=1)
    embedding_provider: str = Field(default="fake")
    embedding_model: str = Field(default="fake-small")
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str = Field(default="https://api.openai.com/v1")
    embedding_dimension: int = Field(default=16, ge=1)
    embedding_batch_size: int = Field(default=64, ge=1)
    embedding_timeout_seconds: int = Field(default=30, ge=1)
    embedding_max_retries: int = Field(default=2, ge=0)
    search_default_limit: int = Field(default=10, ge=1)
    search_max_limit: int = Field(default=25, ge=1)
    search_vector_weight: float = Field(default=0.45, ge=0)
    search_keyword_weight: float = Field(default=0.35, ge=0)
    search_citation_weight: float = Field(default=1.0, ge=0)
    answer_llm_provider: str = Field(default="fake")
    answer_llm_model: str = Field(default="fake-answer-small")
    answer_llm_api_key: SecretStr | None = None
    answer_llm_base_url: str = Field(default="https://api.openai.com/v1")
    answer_llm_timeout_seconds: int = Field(default=60, ge=1)
    answer_llm_max_retries: int = Field(default=1, ge=0)
    answer_max_context_chunks: int = Field(default=8, ge=1)
    answer_max_context_chars: int = Field(default=16000, ge=1000)
    answer_max_output_tokens: int = Field(default=900, ge=1)
    answer_temperature: float = Field(default=0, ge=0)
    answer_include_source_coverage: bool = Field(default=True)
    rate_limit_enabled: bool = Field(default=True)
    login_failed_limit: int = Field(default=5, ge=1)
    login_failed_window_seconds: int = Field(default=900, ge=1)
    login_cooldown_seconds: int = Field(default=900, ge=1)
    search_requests_per_hour: int = Field(default=120, ge=1)
    answer_requests_per_hour: int = Field(default=30, ge=1)
    user_daily_llm_token_budget: int = Field(default=30000, ge=1)
    max_request_body_bytes: int = Field(default=65536, ge=1)
    search_timeout_seconds: int = Field(default=10, ge=1)
    answer_timeout_seconds: int = Field(default=30, ge=1)
    rate_limit_event_retention_days: int = Field(default=30, ge=1)
    allow_fake_providers_in_production: bool = Field(default=False)

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        allowed = {"local", "staging", "production", "test"}
        if value not in allowed:
            raise ValueError(f"APP_ENV must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("session_cookie_samesite")
    @classmethod
    def validate_session_cookie_samesite(cls, value: str) -> str:
        normalized = value.lower()
        allowed = {"lax", "strict", "none"}
        if normalized not in allowed:
            raise ValueError(
                f"SESSION_COOKIE_SAMESITE must be one of: {', '.join(sorted(allowed))}"
            )
        return normalized

    @field_validator("artifact_storage_backend")
    @classmethod
    def validate_artifact_storage_backend(cls, value: str) -> str:
        normalized = value.lower()
        allowed = {"local", "s3"}
        if normalized not in allowed:
            raise ValueError(
                f"ARTIFACT_STORAGE_BACKEND must be one of: "
                f"{', '.join(sorted(allowed))}"
            )
        return normalized

    @field_validator("embedding_provider")
    @classmethod
    def validate_embedding_provider(cls, value: str) -> str:
        normalized = value.lower()
        allowed = {"fake", "openai", "openai_compatible"}
        if normalized not in allowed:
            raise ValueError(
                f"EMBEDDING_PROVIDER must be one of: {', '.join(sorted(allowed))}"
            )
        return normalized

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError(
                "SESSION_COOKIE_SAMESITE=none requires SESSION_COOKIE_SECURE=true."
            )

        if self.app_env == "production":
            if not self.session_cookie_secure:
                raise ValueError(
                    "SESSION_COOKIE_SECURE must be true when APP_ENV=production."
                )
            if not self.rate_limit_enabled:
                raise ValueError(
                    "RATE_LIMIT_ENABLED must be true when APP_ENV=production."
                )
            if self.database_url_value.startswith("sqlite"):
                raise ValueError("SQLite cannot be used when APP_ENV=production.")
            if (
                not self.allow_fake_providers_in_production
                and (
                    self.embedding_provider == "fake"
                    or self.answer_llm_provider == "fake"
                )
            ):
                raise ValueError(
                    "Fake providers cannot be used in production unless "
                    "ALLOW_FAKE_PROVIDERS_IN_PRODUCTION=true."
                )

        if self.artifact_storage_backend == "s3" and not self.artifact_s3_bucket:
            raise ValueError(
                "ARTIFACT_S3_BUCKET is required when ARTIFACT_STORAGE_BACKEND=s3."
            )

        if self.embedding_provider != "fake" and not _secret_has_value(
            self.embedding_api_key
        ):
            raise ValueError(
                "EMBEDDING_API_KEY is required when EMBEDDING_PROVIDER is not fake."
            )

        if self.answer_llm_provider != "fake" and not _secret_has_value(
            self.answer_llm_api_key
        ):
            raise ValueError(
                "ANSWER_LLM_API_KEY is required when ANSWER_LLM_PROVIDER is not fake."
            )
        return self

    @field_validator("answer_llm_provider")
    @classmethod
    def validate_answer_llm_provider(cls, value: str) -> str:
        normalized = value.lower()
        allowed = {"fake", "openai", "openai_compatible"}
        if normalized not in allowed:
            raise ValueError(
                f"ANSWER_LLM_PROVIDER must be one of: {', '.join(sorted(allowed))}"
            )
        return normalized

    @property
    def database_url_value(self) -> str:
        return self.database_url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _secret_has_value(value: SecretStr | None) -> bool:
    return bool(value and value.get_secret_value())
