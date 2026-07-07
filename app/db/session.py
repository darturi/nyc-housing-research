from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

if settings.database_url_value.startswith("sqlite"):
    engine = create_engine(
        settings.database_url_value,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    engine = create_engine(
        settings.database_url_value,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        connect_args={"connect_timeout": settings.healthcheck_timeout_seconds},
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def database_is_healthy() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        logger.exception("database_health_check_failed")
        return False


def set_statement_timeout(db: Session, timeout_seconds: int) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    timeout_ms = max(1, int(timeout_seconds * 1000))
    db.execute(
        text("SELECT set_config('statement_timeout', :timeout_ms, true)"),
        {"timeout_ms": f"{timeout_ms}ms"},
    )
