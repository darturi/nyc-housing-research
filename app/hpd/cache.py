from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, insert, select, update

from app.hpd.connector import (
    BuildingCandidate,
    HpdSocrataConnector,
    HpdViolationRecord,
    PropertyConnectorError,
    PropertyQuery,
    PropertySearchResponse,
)
from app.jobs.runtime import Deadline, OperationDeadlineExceeded
from app.storage.database import LocalStorage
from app.storage.schema import maintenance_state, property_cache
from app.workspace.network import NetworkAccessDenied

SUCCESS_TTL = timedelta(hours=24)
EMPTY_TTL = timedelta(hours=1)


class PropertyCacheError(RuntimeError):
    pass


class CachedPropertyRepository:
    def __init__(
        self,
        storage: LocalStorage,
        connector: HpdSocrataConnector,
        *,
        max_bytes: int = 500 * 1024**2,
        retention_days: int = 30,
    ) -> None:
        if not 1 <= retention_days <= 365:
            raise ValueError("Property cache retention must be between 1 and 365 days.")
        self._storage = storage
        self._connector = connector
        self._max_bytes = max_bytes
        self._stale_retention = timedelta(days=retention_days)

    def search(
        self,
        query: PropertyQuery,
        *,
        refresh: bool = False,
        deadline: Deadline | None = None,
        now: datetime | None = None,
    ) -> PropertySearchResponse:
        query.validate()
        now = now or datetime.now(UTC)
        key = _cache_key(query, self._connector.manifest["connector_version"])
        cached = self._read(key, now)
        if cached and not refresh and cached[1] > now:
            return replace(cached[0], cache_status="fresh_cache", stale=False)
        try:
            response = self._connector.search(query, deadline=deadline)
        except (
            NetworkAccessDenied,
            OperationDeadlineExceeded,
            PropertyConnectorError,
        ):
            if (
                cached
                and cached[2] > now
                and cached[0].source_status != "verified_zero"
            ):
                return replace(cached[0], cache_status="stale_cache", stale=True)
            raise
        self._store(key, response, now)
        self._evict()
        return response

    def cached(
        self,
        query: PropertyQuery,
        *,
        allow_stale: bool = True,
        now: datetime | None = None,
    ) -> PropertySearchResponse:
        query.validate()
        now = now or datetime.now(UTC)
        key = _cache_key(query, self._connector.manifest["connector_version"])
        cached = self._read(key, now)
        if cached is None:
            raise PropertyCacheError(
                "This exact property result set is not available in the local cache."
            )
        response, expires_at, stale_until = cached
        if expires_at > now:
            return replace(response, cache_status="fresh_cache", stale=False)
        if (
            allow_stale
            and stale_until > now
            and response.source_status != "verified_zero"
        ):
            return replace(response, cache_status="stale_cache", stale=True)
        raise PropertyCacheError("This cached property result set has expired.")

    def clear(self) -> int:
        with self._storage.state_engine.begin() as connection:
            self._assert_writable(connection)
            rows = list(connection.scalars(select(property_cache.c.response_artifact)))
            connection.execute(delete(property_cache))
        removed = 0
        for relative in rows:
            path = self._safe_artifact(relative)
            if path.exists():
                path.unlink()
                removed += 1
        return removed

    def pin(self, query: PropertyQuery, *, pinned: bool) -> bool:
        """Protect or release an exact cached page during a summary/export."""
        query.validate()
        key = _cache_key(query, self._connector.manifest["connector_version"])
        with self._storage.state_engine.begin() as connection:
            self._assert_writable(connection)
            result = connection.execute(
                update(property_cache)
                .where(property_cache.c.cache_key == key)
                .values(pinned=pinned)
            )
        return bool(result.rowcount)

    def _read(self, key: str, now: datetime):
        with self._storage.state_engine.begin() as connection:
            row = (
                connection.execute(
                    select(property_cache).where(property_cache.c.cache_key == key)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            connection.execute(
                update(property_cache)
                .where(property_cache.c.cache_key == key)
                .values(last_accessed_at=now)
            )
        path = self._safe_artifact(row["response_artifact"])
        try:
            content = path.read_bytes()
            response = _decode_response(json.loads(content))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise PropertyCacheError("Cached property response is corrupt.") from exc
        configured_stale_until = _aware(row["fetched_at"]) + self._stale_retention
        effective_stale_until = min(
            _aware(row["stale_until"]), configured_stale_until
        )
        return response, _aware(row["expires_at"]), effective_stale_until

    def _store(self, key: str, response: PropertySearchResponse, now: datetime) -> None:
        content = (
            json.dumps(
                _encode_response(response), sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode()
        cache_dir = self._storage.paths.artifacts / "property-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        destination = cache_dir / f"{hashlib.sha256(content).hexdigest()}.json"
        if not destination.exists():
            _write_atomic(destination, content)
        relative = destination.relative_to(self._storage.paths.root).as_posix()
        ttl = EMPTY_TTL if response.source_status == "verified_zero" else SUCCESS_TTL
        values = {
            "request_json": json.dumps(asdict(response.query), sort_keys=True),
            "connector_version": response.connector_version,
            "response_artifact": relative,
            "status": response.source_status,
            "is_complete": response.is_complete,
            "fetched_at": response.fetched_at,
            "expires_at": now + ttl,
            "stale_until": now + self._stale_retention,
            "last_accessed_at": now,
            "size_bytes": len(content),
            "pinned": False,
        }
        previous_artifact = None
        with self._storage.state_engine.begin() as connection:
            self._assert_writable(connection)
            existing = connection.execute(
                select(
                    property_cache.c.cache_key,
                    property_cache.c.response_artifact,
                ).where(
                    property_cache.c.cache_key == key
                )
            ).mappings().one_or_none()
            if existing is None:
                connection.execute(
                    insert(property_cache).values(cache_key=key, **values)
                )
            else:
                previous_artifact = existing["response_artifact"]
                connection.execute(
                    update(property_cache)
                    .where(property_cache.c.cache_key == key)
                    .values(**values)
                )
        if previous_artifact and previous_artifact != relative:
            self._remove_if_unreferenced(previous_artifact)

    def _evict(self) -> None:
        with self._storage.state_engine.begin() as connection:
            total = int(
                connection.scalar(
                    select(func.coalesce(func.sum(property_cache.c.size_bytes), 0))
                )
                or 0
            )
            rows = list(
                connection.execute(
                    select(
                        property_cache.c.cache_key,
                        property_cache.c.response_artifact,
                        property_cache.c.size_bytes,
                    )
                    .where(property_cache.c.pinned.is_(False))
                    .order_by(property_cache.c.last_accessed_at)
                ).mappings()
            )
            removed = []
            for row in rows:
                if total <= self._max_bytes:
                    break
                connection.execute(
                    delete(property_cache).where(
                        property_cache.c.cache_key == row["cache_key"]
                    )
                )
                total -= row["size_bytes"]
                removed.append(row["response_artifact"])
        for relative in removed:
            path = self._safe_artifact(relative)
            if path.exists():
                path.unlink()

    def _safe_artifact(self, relative: str) -> Path:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise PropertyCacheError("Cached property path escapes its workspace.")
        path = self._storage.paths.root / relative_path
        cache_root = (self._storage.paths.artifacts / "property-cache").resolve()
        try:
            path.parent.resolve().relative_to(cache_root)
        except ValueError as exc:
            raise PropertyCacheError(
                "Cached property path escapes its workspace."
            ) from exc
        if path.is_symlink():
            raise PropertyCacheError("Cached property artifact cannot be a symlink.")
        return path

    def _remove_if_unreferenced(self, relative: str) -> None:
        with self._storage.state_engine.connect() as connection:
            references = int(
                connection.scalar(
                    select(func.count())
                    .select_from(property_cache)
                    .where(property_cache.c.response_artifact == relative)
                )
                or 0
            )
        if references == 0:
            self._safe_artifact(relative).unlink(missing_ok=True)

    @staticmethod
    def _assert_writable(connection) -> None:
        if connection.scalar(
            select(maintenance_state.c.active).where(maintenance_state.c.id == 1)
        ):
            raise PropertyCacheError(
                "Workspace maintenance is active; property cache writes are paused."
            )


def _cache_key(query: PropertyQuery, connector_version: str) -> str:
    identity = {
        "connector_version": connector_version,
        "query": asdict(query),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _encode_response(response: PropertySearchResponse) -> dict:
    payload = asdict(response)
    payload["fetched_at"] = response.fetched_at.isoformat()
    for field in ("source_update_time", "fetch_started_at", "fetch_completed_at"):
        value = getattr(response, field)
        payload[field] = value.isoformat() if value is not None else None
    return payload


def _decode_response(payload: dict) -> PropertySearchResponse:
    return PropertySearchResponse(
        query=PropertyQuery(**payload["query"]),
        candidates=tuple(
            BuildingCandidate(**candidate) for candidate in payload["candidates"]
        ),
        records=tuple(HpdViolationRecord(**record) for record in payload["records"]),
        requires_selection=bool(payload["requires_selection"]),
        continuation=payload["continuation"],
        is_complete=bool(payload["is_complete"]),
        returned_count=int(payload["returned_count"]),
        fetched_at=datetime.fromisoformat(payload["fetched_at"]),
        dataset_id=payload["dataset_id"],
        dataset_url=payload["dataset_url"],
        connector_version=payload["connector_version"],
        source_status=payload["source_status"],
        total_count=(
            int(payload["total_count"])
            if payload.get("total_count") is not None
            else None
        ),
        has_more=bool(payload.get("has_more", payload.get("continuation"))),
        next_cursor=payload.get("next_cursor", payload.get("continuation")),
        source_update_time=_optional_datetime(payload.get("source_update_time")),
        fetch_started_at=_optional_datetime(payload.get("fetch_started_at")),
        fetch_completed_at=_optional_datetime(
            payload.get("fetch_completed_at", payload.get("fetched_at"))
        ),
        cache_status=payload.get("cache_status", "live"),
        stale=bool(payload.get("stale", False)),
    )


def _write_atomic(destination: Path, content: bytes) -> None:
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=".property-", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise TypeError("Cached property timestamp must be a string or null.")
    return datetime.fromisoformat(value)
