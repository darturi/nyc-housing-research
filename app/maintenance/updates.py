from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlparse

import httpx

from app.workspace.network import NetworkPolicy


class UpdateCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateCheck:
    installed_version: str
    latest_tag: str
    update_available: bool
    release_url: str
    published_at: str | None
    migration_notes: str | None
    action_taken: str = "none"


def check_github_release(
    repository_url: str,
    network: NetworkPolicy,
    *,
    client: httpx.Client | None = None,
) -> UpdateCheck:
    parsed = urlparse(repository_url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.hostname != "github.com" or len(parts) != 2:
        raise UpdateCheckError(
            "Repository must be an https://github.com/OWNER/REPOSITORY URL."
        )
    owner, repository = parts
    endpoint = f"https://api.github.com/repos/{owner}/{repository}/releases/latest"
    network.assert_url_allowed(endpoint, purpose="manual application update check")
    owns_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        response = client.get(
            endpoint,
            headers={"Accept": "application/vnd.github+json"},
        )
        if response.status_code == 404:
            raise UpdateCheckError("No published GitHub release was found.")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpdateCheckError("The release check could not be completed.") from exc
    finally:
        if owns_client:
            client.close()
    tag = str(payload.get("tag_name", "")).strip()
    release_url = str(payload.get("html_url", "")).strip()
    if not tag or not release_url.startswith("https://github.com/"):
        raise UpdateCheckError("GitHub returned incomplete release metadata.")
    try:
        installed = version("nyc-housing-rag")
    except PackageNotFoundError:
        installed = "0+unknown"
    normalized_tag = tag[1:] if tag.startswith("v") else tag
    body = str(payload.get("body") or "").strip()
    return UpdateCheck(
        installed_version=installed,
        latest_tag=tag,
        update_available=normalized_tag != installed,
        release_url=release_url,
        published_at=payload.get("published_at"),
        migration_notes=body[:2_000] or None,
    )
