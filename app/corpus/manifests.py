from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any


@dataclass(frozen=True)
class SourceManifest:
    slug: str
    name: str
    source_type: str
    publisher: str
    jurisdiction: str
    source_url: str
    download_url: str
    parser: str
    parser_version: str
    scope: str
    required_citations: tuple[str, ...]
    minimum_documents: int
    license_status: str
    redistribution_allowed: bool | None
    raw: dict[str, Any]


def load_core_manifests() -> dict[str, SourceManifest]:
    resource = files("app").joinpath("resources/sources/core.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("format_version") != 1:
        raise ValueError("Unsupported core source manifest format.")
    manifests: dict[str, SourceManifest] = {}
    for item in payload.get("sources", []):
        manifest = SourceManifest(
            slug=item["slug"],
            name=item["name"],
            source_type=item["source_type"],
            publisher=item["publisher"],
            jurisdiction=item["jurisdiction"],
            source_url=item["source_url"],
            download_url=item["download_url"],
            parser=item["parser"],
            parser_version=item["parser_version"],
            scope=item["scope"],
            required_citations=tuple(item.get("required_citations", [])),
            minimum_documents=int(item.get("minimum_documents", 1)),
            license_status=item["license_status"],
            redistribution_allowed=item.get("redistribution_allowed"),
            raw=item,
        )
        if manifest.slug in manifests:
            raise ValueError(f"Duplicate source manifest slug: {manifest.slug}")
        if not manifest.source_url.startswith("https://"):
            raise ValueError(f"Source URL must use HTTPS: {manifest.slug}")
        manifests[manifest.slug] = manifest
    if not manifests:
        raise ValueError("Core source manifest is empty.")
    return manifests
