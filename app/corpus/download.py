from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from urllib.parse import urlsplit

import httpx

from app.corpus.manifests import SourceManifest, load_all_manifests, load_core_manifests
from app.corpus.service import SourceArtifact
from app.ingestion.hpd_guidance import (
    HpdGuidancePage,
    extract_guidance_title,
    guidance_urls,
    hpd_guidance_bundle_bytes,
)
from app.workspace.context import WorkspaceContext

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 2
MAX_ARTIFACT_BYTES = 250 * 1024 * 1024
USER_AGENT = "nyc-housing-rag-local/0.1"


class SourceDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class _DownloadState:
    artifact_path: Path
    source_url: str
    content_type: str | None
    etag: str | None
    last_modified: str | None


def download_source_artifacts(
    context: WorkspaceContext,
    slugs: Iterable[str] | None = None,
    *,
    progress: Callable[[str], None] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[SourceArtifact]:
    manifests = load_all_manifests()
    requested = list(load_core_manifests()) if slugs is None else list(slugs)
    unknown = sorted(set(requested) - set(manifests))
    if unknown:
        raise SourceDownloadError("Unknown source(s): " + ", ".join(unknown))
    prior_downloads = _active_download_state(context)
    artifacts: list[SourceArtifact] = []
    with httpx.Client(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        transport=transport,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for index, slug in enumerate(requested, start=1):
            manifest = manifests[slug]
            if progress:
                progress(f"Downloading source {index}/{len(requested)}: {slug}")
            if manifest.parser == "hpd_guidance_bundle":
                artifacts.append(_download_guidance(context, client, manifest))
            elif manifest.parser == "curated_guidance_bundle":
                artifacts.append(_download_curated_guidance(context, client, manifest))
            else:
                prior = prior_downloads.get(slug)
                headers = _conditional_headers(prior)
                response = _get(
                    context,
                    client,
                    manifest.download_url,
                    purpose=slug,
                    headers=headers,
                )
                checked_at = datetime.now(UTC)
                if response.status_code == 304:
                    if prior is None:
                        raise SourceDownloadError(
                            f"Source {slug} returned not-modified without local data."
                        )
                    content = _read_prior_artifact(prior, slug)
                    source_url = prior.source_url
                    content_type = prior.content_type
                else:
                    content = response.content
                    source_url = str(response.url)
                    content_type = _content_type(response)
                artifacts.append(
                    SourceArtifact(
                        slug=slug,
                        content=content,
                        source_url=source_url,
                        content_type=content_type,
                        retrieved_at=checked_at,
                        etag=_response_validator(response, "etag")
                        or (prior.etag if prior else None),
                        last_modified=_response_validator(response, "last-modified")
                        or (prior.last_modified if prior else None),
                    )
                )
    return artifacts


def _download_guidance(
    context: WorkspaceContext,
    client: httpx.Client,
    manifest: SourceManifest,
) -> SourceArtifact:
    pages: list[HpdGuidancePage] = []
    for url in guidance_urls(manifest.source_url):
        response = _get(context, client, url, purpose=manifest.slug)
        html = response.content.decode("utf-8", errors="replace")
        pages.append(
            HpdGuidancePage(
                url=str(response.url),
                title=extract_guidance_title(html, url),
                html=html,
            )
        )
    content = hpd_guidance_bundle_bytes(pages)
    return SourceArtifact(
        slug=manifest.slug,
        content=content,
        source_url=manifest.source_url,
        content_type="application/json",
        retrieved_at=datetime.now(UTC),
    )


def _download_curated_guidance(
    context: WorkspaceContext,
    client: httpx.Client,
    manifest: SourceManifest,
) -> SourceArtifact:
    raw_urls = manifest.raw.get("document_urls")
    if not isinstance(raw_urls, list) or not raw_urls:
        raise SourceDownloadError(
            f"Source {manifest.slug} has no curated document URLs."
        )
    expected_host = urlsplit(manifest.source_url).hostname
    pages: list[HpdGuidancePage] = []
    for raw_url in raw_urls:
        if not isinstance(raw_url, str):
            raise SourceDownloadError(
                f"Source {manifest.slug} has an invalid document URL."
            )
        parsed = urlsplit(raw_url)
        if parsed.scheme != "https" or parsed.hostname != expected_host:
            raise SourceDownloadError(
                f"Source {manifest.slug} document URL is outside its publisher host."
            )
        response = _get(context, client, raw_url, purpose=manifest.slug)
        if urlsplit(str(response.url)).hostname != expected_host:
            raise SourceDownloadError(
                f"Source {manifest.slug} redirected outside its publisher host."
            )
        content_type = _content_type(response) or ""
        looks_like_html = (
            response.content.lstrip().lower().startswith((b"<!doctype html", b"<html"))
        )
        if "html" not in content_type.lower() and not looks_like_html:
            raise SourceDownloadError(
                f"Source {manifest.slug} returned a non-HTML guidance document."
            )
        html = response.content.decode("utf-8", errors="replace")
        pages.append(
            HpdGuidancePage(
                url=str(response.url),
                title=extract_guidance_title(html, raw_url)
                .split(" | Homes and Community Renewal", 1)[0]
                .strip(),
                html=html,
            )
        )
    content = hpd_guidance_bundle_bytes(pages)
    return SourceArtifact(
        slug=manifest.slug,
        content=content,
        source_url=manifest.source_url,
        content_type="application/json",
        retrieved_at=datetime.now(UTC),
    )


def _get(
    context: WorkspaceContext,
    client: httpx.Client,
    url: str,
    *,
    purpose: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    context.network.assert_url_allowed(url, purpose=f"source download: {purpose}")
    last_error: Exception | None = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 304:
                    response.read()
                    return response
                response.raise_for_status()
                size = response.headers.get("Content-Length")
                if size and size.isdigit() and int(size) > MAX_ARTIFACT_BYTES:
                    raise SourceDownloadError(
                        f"Source {purpose} exceeds the byte limit."
                    )
                content = bytearray()
                for piece in response.iter_bytes(chunk_size=64 * 1024):
                    if len(content) + len(piece) > MAX_ARTIFACT_BYTES:
                        raise SourceDownloadError(
                            f"Source {purpose} exceeds the "
                            f"{MAX_ARTIFACT_BYTES}-byte limit."
                        )
                    content.extend(piece)
                response_headers = dict(response.headers)
                # iter_bytes has decoded the transfer; do not decode it twice.
                response_headers.pop("content-encoding", None)
                response_headers.pop("content-length", None)
                return httpx.Response(
                    response.status_code,
                    headers=response_headers,
                    content=bytes(content),
                    request=response.request,
                )
        except (httpx.HTTPError, SourceDownloadError) as exc:
            last_error = exc
            if attempt < DEFAULT_RETRIES and _retryable(exc):
                sleep(0.25 * (attempt + 1))
                continue
            break
    raise SourceDownloadError(f"Could not download source {purpose}.") from last_error


def _retryable(error: Exception) -> bool:
    if isinstance(error, SourceDownloadError):
        return False
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 429, 500, 502, 503, 504}
    return True


def _content_type(response: httpx.Response) -> str | None:
    value = response.headers.get("content-type", "").split(";", 1)[0].strip()
    return value or None


def _conditional_headers(prior: _DownloadState | None) -> dict[str, str]:
    if prior is None:
        return {}
    headers: dict[str, str] = {}
    if prior.etag:
        headers["If-None-Match"] = prior.etag
    if prior.last_modified:
        headers["If-Modified-Since"] = prior.last_modified
    return headers


def _response_validator(response: httpx.Response, name: str) -> str | None:
    value = response.headers.get(name, "").strip()
    if (
        not value
        or len(value) > 1_000
        or any(ord(character) < 32 for character in value)
    ):
        return None
    return value


def _read_prior_artifact(prior: _DownloadState, slug: str) -> bytes:
    try:
        content = prior.artifact_path.read_bytes()
    except OSError as exc:
        raise SourceDownloadError(
            f"Source {slug} was not modified, but its local artifact is unavailable."
        ) from exc
    if len(content) > MAX_ARTIFACT_BYTES:
        raise SourceDownloadError(
            f"Source {slug} exceeds the {MAX_ARTIFACT_BYTES}-byte limit."
        )
    return content


def _active_download_state(context: WorkspaceContext) -> dict[str, _DownloadState]:
    database = context.paths.corpus_database
    if not database.is_file():
        return {}
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT sm.slug, sv.artifact_uri, sv.validation_json
                FROM corpus_state AS cs
                JOIN generation_sources AS gs
                  ON gs.generation_id = cs.active_generation_id
                JOIN source_versions AS sv ON sv.id = gs.source_version_id
                JOIN source_modules AS sm ON sm.id = sv.source_module_id
                WHERE cs.id = 1
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise SourceDownloadError(
            "Could not read existing source download metadata."
        ) from exc
    result: dict[str, _DownloadState] = {}
    artifact_root = context.paths.artifacts.resolve()
    for row in rows:
        try:
            validation = json.loads(row["validation_json"])
        except (TypeError, json.JSONDecodeError):
            validation = {}
        http = validation.get("http", {}) if isinstance(validation, dict) else {}
        if not isinstance(http, dict):
            http = {}
        artifact_path = (context.paths.root / row["artifact_uri"]).resolve()
        try:
            artifact_path.relative_to(artifact_root)
        except ValueError as exc:
            raise SourceDownloadError(
                f"Stored artifact path escapes the workspace for {row['slug']}."
            ) from exc
        result[str(row["slug"])] = _DownloadState(
            artifact_path=artifact_path,
            source_url=str(http.get("source_url") or ""),
            content_type=(
                str(http["content_type"]) if http.get("content_type") else None
            ),
            etag=_stored_validator(http.get("etag")),
            last_modified=_stored_validator(http.get("last_modified")),
        )
    return result


def _stored_validator(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not value
        or len(value) > 1_000
        or any(ord(character) < 32 for character in value)
    ):
        return None
    return value
