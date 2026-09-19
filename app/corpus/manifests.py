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
    role: str
    authority_category: str
    pack_id: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class SourcePack:
    id: str
    catalog_version: int
    catalog_verified_at: str
    title: str
    description: str
    jurisdiction: str
    support_status: str
    supported_topics: tuple[str, ...]
    limitations: tuple[str, ...]
    missing_modules: tuple[str, ...]
    dependencies: tuple[str, ...]
    module_slugs: tuple[str, ...]
    raw: dict[str, Any]


def _load_payload(filename: str) -> dict[str, Any]:
    resource = files("app").joinpath(f"resources/sources/{filename}")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("format_version") != 1:
        raise ValueError(f"Unsupported source manifest format: {filename}")
    return payload


def _manifest_from_item(
    item: dict[str, Any], *, role: str, pack_id: str | None = None
) -> SourceManifest:
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
        role=role,
        authority_category=item.get("authority_category", item["source_type"]),
        pack_id=pack_id,
        raw=item,
    )
    if not manifest.source_url.startswith("https://"):
        raise ValueError(f"Source URL must use HTTPS: {manifest.slug}")
    if not manifest.download_url.startswith("https://"):
        raise ValueError(f"Download URL must use HTTPS: {manifest.slug}")
    if manifest.role not in {"core", "optional"}:
        raise ValueError(f"Invalid source role: {manifest.slug}")
    return manifest


def load_core_manifests() -> dict[str, SourceManifest]:
    payload = _load_payload("core.json")
    manifests: dict[str, SourceManifest] = {}
    for item in payload.get("sources", []):
        manifest = _manifest_from_item(item, role="core")
        if manifest.slug in manifests:
            raise ValueError(f"Duplicate source manifest slug: {manifest.slug}")
        manifests[manifest.slug] = manifest
    if not manifests:
        raise ValueError("Core source manifest is empty.")
    return manifests


def load_source_packs() -> dict[str, SourcePack]:
    payload = _load_payload("packs.json")
    catalog_version = int(payload.get("catalog_version", 1))
    packs: dict[str, SourcePack] = {}
    for item in payload.get("packs", []):
        pack_id = item["id"]
        if pack_id in packs:
            raise ValueError(f"Duplicate source pack id: {pack_id}")
        module_slugs = tuple(module["slug"] for module in item.get("modules", []))
        if not module_slugs:
            raise ValueError(f"Source pack has no installable modules: {pack_id}")
        if len(set(module_slugs)) != len(module_slugs):
            raise ValueError(f"Source pack has duplicate module slugs: {pack_id}")
        packs[pack_id] = SourcePack(
            id=pack_id,
            catalog_version=catalog_version,
            catalog_verified_at=item["catalog_verified_at"],
            title=item["title"],
            description=item["description"],
            jurisdiction=item["jurisdiction"],
            support_status=item["support_status"],
            supported_topics=tuple(item.get("supported_topics", [])),
            limitations=tuple(item.get("limitations", [])),
            missing_modules=tuple(item.get("missing_modules", [])),
            dependencies=tuple(item.get("dependencies", [])),
            module_slugs=module_slugs,
            raw=item,
        )
    return packs


def load_optional_manifests() -> dict[str, SourceManifest]:
    manifests: dict[str, SourceManifest] = {}
    for pack in load_source_packs().values():
        for item in pack.raw.get("modules", []):
            manifest = _manifest_from_item(item, role="optional", pack_id=pack.id)
            if manifest.slug in manifests:
                raise ValueError(f"Duplicate source manifest slug: {manifest.slug}")
            manifests[manifest.slug] = manifest
    return manifests


def load_all_manifests() -> dict[str, SourceManifest]:
    manifests = load_core_manifests()
    for slug, manifest in load_optional_manifests().items():
        if slug in manifests:
            raise ValueError(f"Duplicate source manifest slug: {slug}")
        manifests[slug] = manifest
    return manifests
