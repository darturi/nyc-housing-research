from fastapi import HTTPException, status

from app.limits.schemas import RateLimitDecision


def rate_limit_exception(decision: RateLimitDecision) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "detail": "Rate limit exceeded.",
            "reason": decision.reason,
            "retry_after_seconds": decision.retry_after_seconds,
        },
        headers={
            "Retry-After": str(decision.retry_after_seconds),
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
            "X-RateLimit-Reset": decision.reset_at.isoformat(),
        },
    )
