from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.db.session import database_is_healthy

router = APIRouter()
logger = get_logger(__name__)


@router.get("/health")
def health() -> JSONResponse:
    if database_is_healthy():
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ok", "database": "ok"},
        )

    logger.warning("health_check_failed", extra={"database": "error"})
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "error", "database": "error"},
    )

