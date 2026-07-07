import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from mimetypes import guess_extension
from time import sleep

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.ingestion.artifacts import write_artifact
from app.models.source import Source
from app.models.source_version import SourceVersion


@dataclass(frozen=True)
class DownloadedArtifact:
    content: bytes
    content_hash: str
    content_type: str | None
    byte_size: int
    extension: str
    source_url: str


def hash_bytes(content_bytes: bytes) -> str:
    return hashlib.sha256(content_bytes).hexdigest()


def extension_from_content_type(content_type: str | None, source_url: str) -> str:
    if source_url.endswith(".json"):
        return "json"
    if source_url.endswith(".html") or "text/html" in (content_type or ""):
        return "html"
    if "json" in (content_type or ""):
        return "json"
    if "text/plain" in (content_type or ""):
        return "txt"
    guessed = guess_extension(content_type or "") if content_type else None
    return (guessed or ".bin").lstrip(".")


def download_url(url: str) -> DownloadedArtifact:
    settings = get_settings()
    headers = {"User-Agent": settings.ingestion_user_agent}
    last_error: Exception | None = None
    for attempt in range(settings.ingestion_http_max_retries + 1):
        try:
            with httpx.Client(
                timeout=settings.ingestion_http_timeout_seconds,
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = client.get(url)
            response.raise_for_status()
            break
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < settings.ingestion_http_max_retries:
                sleep(0.5 * (attempt + 1))
                continue
            raise
    else:
        raise RuntimeError("Download failed.") from last_error
    content_type = response.headers.get("content-type", "").split(";")[0] or None
    content = response.content
    return DownloadedArtifact(
        content=content,
        content_hash=hash_bytes(content),
        content_type=content_type,
        byte_size=len(content),
        extension=extension_from_content_type(content_type, str(response.url)),
        source_url=str(response.url),
    )


def create_or_get_source_version(
    db: DbSession,
    source: Source,
    artifact: DownloadedArtifact,
) -> SourceVersion:
    existing = db.scalar(
        select(SourceVersion).where(
            SourceVersion.source_id == source.id,
            SourceVersion.content_hash == artifact.content_hash,
        )
    )
    if existing is not None:
        return existing

    artifact_uri = write_artifact(
        source.slug,
        artifact.content_hash,
        artifact.content,
        artifact.extension,
    )
    db.execute(
        update(SourceVersion)
        .where(SourceVersion.source_id == source.id)
        .values(is_current=False)
    )
    source_version = SourceVersion(
        source_id=source.id,
        retrieved_at=datetime.now(UTC).replace(tzinfo=None),
        source_url=artifact.source_url,
        content_hash=artifact.content_hash,
        artifact_uri=artifact_uri,
        content_type=artifact.content_type,
        byte_size=artifact.byte_size,
        is_current=True,
    )
    db.add(source_version)
    db.commit()
    db.refresh(source_version)
    return source_version
