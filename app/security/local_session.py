from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, insert, select, update

from app.storage.database import LocalStorage
from app.storage.schema import launcher_tokens, local_installation, local_sessions

SESSION_COOKIE = "nyc_housing_session"
SESSION_HOURS = 12
LAUNCH_MINUTES = 5


class LocalSessionError(ValueError):
    pass


@dataclass(frozen=True)
class ExchangedSession:
    token: str
    csrf_token: str
    expires_at: datetime


class LocalSessionService:
    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage
        self._ensure_installation()

    def begin_process(self) -> str:
        """Invalidate process-local access state and issue one launch credential."""
        now = _now()
        with self._storage.state_engine.begin() as connection:
            connection.execute(delete(launcher_tokens))
            connection.execute(delete(local_sessions))
        return self.issue_launch_token(now=now)

    def issue_launch_token(self, *, now: datetime | None = None) -> str:
        now = now or _now()
        token = secrets.token_urlsafe(32)
        with self._storage.state_engine.begin() as connection:
            connection.execute(
                insert(launcher_tokens).values(
                    id=str(uuid.uuid4()),
                    token_hash=_hash(token),
                    created_at=now,
                    expires_at=now + timedelta(minutes=LAUNCH_MINUTES),
                    consumed_at=None,
                )
            )
        return token

    def exchange(
        self, launch_token: str, *, now: datetime | None = None
    ) -> ExchangedSession:
        now = now or _now()
        if not launch_token or len(launch_token) > 256:
            raise LocalSessionError("Invalid or expired launch credential.")
        with self._storage.state_engine.begin() as connection:
            row = (
                connection.execute(
                    select(launcher_tokens).where(
                        launcher_tokens.c.token_hash == _hash(launch_token)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or row["consumed_at"] is not None
                or _aware(row["expires_at"]) <= now
            ):
                raise LocalSessionError("Invalid or expired launch credential.")
            consumed = connection.execute(
                update(launcher_tokens)
                .where(
                    launcher_tokens.c.id == row["id"],
                    launcher_tokens.c.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                raise LocalSessionError("Invalid or expired launch credential.")
            session_token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(hours=SESSION_HOURS)
            connection.execute(
                insert(local_sessions).values(
                    id=str(uuid.uuid4()),
                    token_hash=_hash(session_token),
                    csrf_hash=_hash(csrf_token),
                    created_at=now,
                    expires_at=expires_at,
                    last_seen_at=now,
                )
            )
        return ExchangedSession(session_token, csrf_token, expires_at)

    def validate(
        self,
        session_token: str | None,
        *,
        csrf_token: str | None = None,
        require_csrf: bool = False,
        now: datetime | None = None,
    ) -> bool:
        if not session_token:
            return False
        now = now or _now()
        with self._storage.state_engine.begin() as connection:
            row = (
                connection.execute(
                    select(local_sessions).where(
                        local_sessions.c.token_hash == _hash(session_token)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or _aware(row["expires_at"]) <= now:
                return False
            if require_csrf and (
                not csrf_token
                or not hmac.compare_digest(row["csrf_hash"], _hash(csrf_token))
            ):
                return False
            connection.execute(
                update(local_sessions)
                .where(local_sessions.c.id == row["id"])
                .values(last_seen_at=now)
            )
        return True

    def rotate_csrf(
        self, session_token: str | None, *, now: datetime | None = None
    ) -> str:
        if not session_token:
            raise LocalSessionError("Local session authentication required.")
        now = now or _now()
        token = secrets.token_urlsafe(32)
        with self._storage.state_engine.begin() as connection:
            result = connection.execute(
                update(local_sessions)
                .where(
                    local_sessions.c.token_hash == _hash(session_token),
                    local_sessions.c.expires_at > now,
                )
                .values(csrf_hash=_hash(token), last_seen_at=now)
            )
        if result.rowcount != 1:
            raise LocalSessionError("Local session authentication required.")
        return token

    def _ensure_installation(self) -> None:
        with self._storage.state_engine.begin() as connection:
            existing = connection.scalar(
                select(local_installation.c.id).where(local_installation.c.id == 1)
            )
            if existing is None:
                connection.execute(
                    insert(local_installation).values(
                        id=1,
                        installation_id=str(uuid.uuid4()),
                        secret_hash=_hash(secrets.token_urlsafe(48)),
                        created_at=_now(),
                    )
                )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
