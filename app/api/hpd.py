from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.hpd.search import search_hpd_violations as run_hpd_violation_search
from app.limits.dependencies import enforce_search_limit
from app.models.user import User
from app.schemas.hpd import HpdViolationSearchRequest, HpdViolationSearchResponse

router = APIRouter(prefix="/hpd", tags=["hpd"])
DbDependency = Annotated[DbSession, Depends(get_db)]
CurrentUserDependency = Annotated[User, Depends(get_current_user)]


@router.post("/violations/search", response_model=HpdViolationSearchResponse)
def search_hpd_violations(
    payload: HpdViolationSearchRequest,
    request: Request,
    db: DbDependency,
    current_user: CurrentUserDependency,
) -> HpdViolationSearchResponse:
    enforce_search_limit(request, db, current_user)
    return run_hpd_violation_search(db, payload)
