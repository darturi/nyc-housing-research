from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_at: datetime
    retry_after_seconds: int
    reason: str
