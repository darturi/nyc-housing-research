from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.answers import router as answers_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.hpd import router as hpd_router
from app.api.query import router as query_router
from app.api.search import router as search_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import request_size_middleware

settings = get_settings()
configure_logging(settings.log_level, settings.app_env)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("application_startup", extra={"app_env": settings.app_env})
    yield
    logger.info("application_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.middleware("http")(request_size_middleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(search_router)
    app.include_router(answers_router)
    app.include_router(hpd_router)
    app.include_router(query_router)
    return app


app = create_app()
