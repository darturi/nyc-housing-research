from __future__ import annotations

import csv
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from app.hpd.connector import PropertySearchResponse
from app.workspace.paths import WorkspacePaths


class ExportError(RuntimeError):
    pass


class ResearchExporter:
    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    def answer(self, result: dict, *, output_format: str) -> Path:
        if output_format not in {"json", "markdown"}:
            raise ExportError("Answer export format must be json or markdown.")
        created = datetime.now(UTC).isoformat()
        payload = {"exported_at": created, "export_version": 1, **result}
        if output_format == "json":
            content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
            suffix = ".json"
        else:
            content = _answer_markdown(payload).encode()
            suffix = ".md"
        return self._write("legal-research", suffix, content)

    def property_csv(self, result: PropertySearchResponse) -> Path:
        descriptor, name = tempfile.mkstemp(
            dir=self._paths.exports, prefix=".property-export-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "export_version",
                        "dataset_id",
                        "dataset_url",
                        "connector_version",
                        "fetched_at",
                        "fetch_started_at",
                        "fetch_completed_at",
                        "source_update_time",
                        "cache_status",
                        "stale",
                        "request_scope_json",
                        "loaded_scope_complete",
                        "total_count",
                        "has_more",
                        "continuation_available",
                        "violation_id",
                        "building_id",
                        "registration_id",
                        "borough",
                        "house_number",
                        "street_name",
                        "zip_code",
                        "apartment",
                        "class",
                        "inspection_date",
                        "current_status",
                        "description",
                    ]
                )
                for row in result.records:
                    writer.writerow(
                        [
                            2,
                            result.dataset_id,
                            result.dataset_url,
                            result.connector_version,
                            result.fetched_at.isoformat(),
                            (
                                result.fetch_started_at.isoformat()
                                if result.fetch_started_at
                                else ""
                            ),
                            (
                                result.fetch_completed_at.isoformat()
                                if result.fetch_completed_at
                                else ""
                            ),
                            (
                                result.source_update_time.isoformat()
                                if result.source_update_time
                                else ""
                            ),
                            result.cache_status,
                            result.stale,
                            json.dumps(
                                {
                                    key: value
                                    for key, value in (
                                        result.query.identity_dict().items()
                                    )
                                    if value is not None
                                },
                                sort_keys=True,
                            ),
                            result.is_complete,
                            result.total_count,
                            result.has_more or bool(result.continuation),
                            bool(result.continuation),
                            row.violation_id,
                            row.building_id,
                            row.registration_id,
                            row.borough,
                            row.house_number,
                            row.street_name,
                            row.zip_code,
                            row.apartment,
                            row.violation_class,
                            row.inspection_date,
                            row.current_status or row.violation_status,
                            _spreadsheet_safe(row.description),
                        ]
                    )
                handle.flush()
                os.fsync(handle.fileno())
            final = self._destination("property-research", ".csv")
            os.replace(temporary, final)
            return final
        finally:
            temporary.unlink(missing_ok=True)

    def _write(self, prefix: str, suffix: str, content: bytes) -> Path:
        descriptor, name = tempfile.mkstemp(
            dir=self._paths.exports, prefix=f".{prefix}-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            final = self._destination(prefix, suffix)
            os.replace(temporary, final)
            return final
        finally:
            temporary.unlink(missing_ok=True)

    def _destination(self, prefix: str, suffix: str) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return (
            self._paths.exports / f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}{suffix}"
        )


def safe_export_path(paths: WorkspacePaths, filename: str) -> Path:
    if Path(filename).name != filename or not filename:
        raise ExportError("Export filename is invalid.")
    path = (paths.exports / filename).resolve()
    try:
        path.relative_to(paths.exports.resolve())
    except ValueError as exc:
        raise ExportError("Export path escapes the workspace.") from exc
    if not path.is_file():
        raise ExportError("Export does not exist.")
    return path


def _answer_markdown(payload: dict) -> str:
    lines = [
        "# NYC Housing research export",
        "",
        f"Exported: {payload['exported_at']}",
        f"Generation: {payload.get('generation_id', 'unknown')}",
        f"Answer profile: {payload.get('answer_profile_id', 'unknown')}",
        f"Embedding profile: {payload.get('embedding_profile_id') or 'not used'}",
        f"Retrieval method: {payload.get('retrieval_method', 'unknown')}",
        f"Prompt version: {payload.get('prompt_version', 'unknown')}",
        f"Status: {payload.get('status', 'unknown')}",
        "",
        "## Question",
        "",
        str(payload.get("question", "")),
        "",
        "## Answer",
        "",
        str(payload.get("answer", "")),
        "",
        "## Evidence",
        "",
    ]
    for item in payload.get("evidence", []):
        label = item.get("citation") or item.get("title") or item.get("marker")
        url = item.get("source_url")
        source = item.get("source_name", "Source")
        if _safe_web_url(url):
            lines.append(f"- **{label}** — [{source}]({url})")
        else:
            lines.append(f"- **{label}** — {source}")
        lines.append(f"  - Evidence ID: `{item.get('chunk_id', 'unknown')}`")
        lines.append(f"  - Publisher: {item.get('publisher', 'unknown')}")
        lines.append(f"  - Retrieved: {item.get('retrieved_at', 'unknown')}")
        lines.append(f"  - Last checked: {item.get('last_checked_at', 'unknown')}")
        lines.append(f"  - Effective from: {item.get('effective_from', 'unknown')}")
        lines.append(f"  - Effective to: {item.get('effective_to', 'unknown')}")
        lines.append(f"  - Excerpt: {item.get('excerpt', '')}")
    lines.extend(["", str(payload.get("disclaimer", "")), ""])
    return "\n".join(lines)


def _safe_web_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _spreadsheet_safe(value: str | None) -> str:
    if not value:
        return ""
    stripped = value.lstrip()
    if stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
