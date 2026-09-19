from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
import unicodedata
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import insert, select

from app.storage.database import LocalStorage
from app.storage.schema import (
    chunks,
    comparison_changes,
    comparison_reviews,
    documents,
    saved_items,
    source_comparisons,
    source_modules,
    source_versions,
)

NORMALIZATION_VERSION = "legal-text-v1"
MATCHER_VERSION = "citation-stable-id-v1"
CLASSIFICATIONS = {
    "added",
    "removed",
    "text_changed",
    "metadata_only",
    "parser_only",
    "unchanged",
    "uncertain",
}


class ComparisonError(RuntimeError):
    pass


class SourceComparisonService:
    """Deterministic, section-level comparisons for retained official versions."""

    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def create(
        self,
        module_slug: str,
        baseline_version_id: str,
        target_version_id: str,
    ) -> dict[str, object]:
        if baseline_version_id == target_version_id:
            raise ComparisonError("Choose two different retained source versions.")
        baseline_meta = self._version_meta(module_slug, baseline_version_id)
        target_meta = self._version_meta(module_slug, target_version_id)
        comparison_id = hashlib.sha256(
            "\x00".join(
                (
                    module_slug,
                    baseline_version_id,
                    target_version_id,
                    NORMALIZATION_VERSION,
                    MATCHER_VERSION,
                )
            ).encode()
        ).hexdigest()
        with self._storage.state_engine.connect() as connection:
            existing = connection.scalar(
                select(source_comparisons.c.id).where(
                    source_comparisons.c.id == comparison_id
                )
            )
        if existing:
            return self.get(comparison_id)

        old = self._sections(baseline_version_id)
        new = self._sections(target_version_id)
        keys = sorted(set(old) | set(new))
        changes: list[dict[str, object]] = []
        parser_changed = (
            baseline_meta["parser_version"] != target_meta["parser_version"]
        )
        identical_artifact = (
            baseline_meta["content_hash"] == target_meta["content_hash"]
        )
        for key in keys:
            before = old.get(key)
            after = new.get(key)
            classification, certainty = _classify(
                before,
                after,
                parser_changed=parser_changed,
                identical_artifact=identical_artifact,
            )
            change_id = hashlib.sha256(f"{comparison_id}\x00{key}".encode()).hexdigest()
            changes.append(
                {
                    "id": change_id,
                    "comparison_id": comparison_id,
                    "canonical_key": key,
                    "classification": classification,
                    "old_json": _dump(before) if before else None,
                    "new_json": _dump(after) if after else None,
                    "diff_text": _diff(before, after),
                    "certainty": certainty,
                }
            )
        _mark_uncertain_renumberings(changes)
        counts: Counter[str] = Counter(
            str(change["classification"]) for change in changes
        )
        warning = None
        if counts["uncertain"]:
            warning = (
                "Some sections could not be aligned with high confidence; review "
                "the original versions before relying on those rows."
            )
        now = datetime.now(UTC)
        try:
            with self._storage.state_engine.begin() as connection:
                connection.execute(
                    insert(source_comparisons).values(
                        id=comparison_id,
                        module_slug=module_slug,
                        baseline_version_id=baseline_version_id,
                        target_version_id=target_version_id,
                        normalization_version=NORMALIZATION_VERSION,
                        matcher_version=MATCHER_VERSION,
                        status="complete",
                        counts_json=_dump(dict(sorted(counts.items()))),
                        warning=warning,
                        created_at=now,
                    )
                )
                if changes:
                    connection.execute(insert(comparison_changes), changes)
        except Exception:
            # A concurrent identical request may have won the unique constraint.
            with self._storage.state_engine.connect() as connection:
                if connection.scalar(
                    select(source_comparisons.c.id).where(
                        source_comparisons.c.id == comparison_id
                    )
                ):
                    return self.get(comparison_id)
            raise
        return self.get(comparison_id)

    def get(
        self,
        comparison_id: str,
        *,
        classification: str | None = None,
        include_unchanged: bool = False,
    ) -> dict[str, object]:
        if classification and classification not in CLASSIFICATIONS:
            raise ComparisonError("Unknown comparison classification.")
        with self._storage.state_engine.connect() as connection:
            row = (
                connection.execute(
                    select(source_comparisons).where(
                        source_comparisons.c.id == comparison_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ComparisonError("Comparison not found.")
            statement = select(comparison_changes).where(
                comparison_changes.c.comparison_id == comparison_id
            )
            if classification:
                statement = statement.where(
                    comparison_changes.c.classification == classification
                )
            elif not include_unchanged:
                statement = statement.where(
                    comparison_changes.c.classification != "unchanged"
                )
            changes = [
                _change(item)
                for item in connection.execute(
                    statement.order_by(comparison_changes.c.canonical_key)
                ).mappings()
            ]
            return {
                "id": row["id"],
                "module_slug": row["module_slug"],
                "baseline_version_id": row["baseline_version_id"],
                "target_version_id": row["target_version_id"],
                "normalization_version": row["normalization_version"],
                "matcher_version": row["matcher_version"],
                "status": row["status"],
                "counts": json.loads(row["counts_json"]),
                "warning": row["warning"],
                "created_at": row["created_at"].isoformat(),
                "changes": changes,
                "saved_research_impacts": self._impacts(connection, row),
            }

    def record_review(
        self, comparison_id: str, item_id: str, *, note: str = ""
    ) -> dict[str, object]:
        if len(note) > 10_000:
            raise ComparisonError("Review note exceeds 10,000 characters.")
        review_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        with self._storage.state_engine.begin() as connection:
            if (
                connection.scalar(
                    select(source_comparisons.c.id).where(
                        source_comparisons.c.id == comparison_id
                    )
                )
                is None
            ):
                raise ComparisonError("Comparison not found.")
            if (
                connection.scalar(
                    select(saved_items.c.id).where(saved_items.c.id == item_id)
                )
                is None
            ):
                raise ComparisonError("Saved item not found.")
            connection.execute(
                insert(comparison_reviews).values(
                    id=review_id,
                    item_id=item_id,
                    comparison_id=comparison_id,
                    note=note.strip(),
                    reviewed_at=now,
                )
            )
        return {
            "id": review_id,
            "comparison_id": comparison_id,
            "item_id": item_id,
            "note": note.strip(),
            "reviewed_at": now.isoformat(),
        }

    def export(self, comparison_id: str, directory: Path, *, format: str) -> Path:
        if format not in {"json", "markdown"}:
            raise ComparisonError("Comparison export format must be json or markdown.")
        payload = self.get(comparison_id, include_unchanged=True)
        exported_at = datetime.now(UTC).isoformat()
        if format == "json":
            content = (
                _dump({"export_version": 1, "exported_at": exported_at, **payload})
                + "\n"
            ).encode()
            suffix = ".json"
        else:
            content = _markdown(payload, exported_at).encode()
            suffix = ".md"
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=directory, prefix=".comparison-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            final = directory / f"source-comparison-{comparison_id[:10]}{suffix}"
            os.replace(temporary, final)
            return final
        finally:
            temporary.unlink(missing_ok=True)

    def _version_meta(self, module_slug: str, version_id: str) -> dict[str, object]:
        with self._storage.corpus_engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        source_versions.c.id,
                        source_versions.c.content_hash,
                        source_versions.c.parser_version,
                        source_modules.c.slug,
                        source_modules.c.origin,
                    )
                    .join(
                        source_modules,
                        source_modules.c.id == source_versions.c.source_module_id,
                    )
                    .where(source_versions.c.id == version_id)
                )
                .mappings()
                .first()
            )
        if row is None or row["slug"] != module_slug:
            raise ComparisonError(
                "Both versions must be retained versions of the selected source module."
            )
        if row["origin"] == "user":
            raise ComparisonError(
                "Official source comparison does not accept user-uploaded resources."
            )
        return dict(row)

    def _sections(self, version_id: str) -> dict[str, dict[str, object]]:
        with self._storage.corpus_engine.connect() as connection:
            rows = list(
                connection.execute(
                    select(
                        chunks.c.id,
                        chunks.c.stable_id,
                        chunks.c.citation,
                        chunks.c.title,
                        chunks.c.text,
                        chunks.c.text_hash,
                        chunks.c.locator_json,
                        documents.c.stable_id.label("document_stable_id"),
                    )
                    .join(documents, documents.c.id == chunks.c.document_id)
                    .where(chunks.c.source_version_id == version_id)
                    .order_by(documents.c.stable_id, chunks.c.stable_id)
                ).mappings()
            )
        grouped: dict[str, list] = {}
        for row in rows:
            base = _canonical_key(row)
            grouped.setdefault(base, []).append(row)
        output: dict[str, dict[str, object]] = {}
        for key, members in grouped.items():
            combined_text = "\n".join(str(member["text"]) for member in members)
            locators = [
                json.loads(member["locator_json"] or "{}") for member in members
            ]
            titles = list(
                dict.fromkeys(
                    str(member["title"]) for member in members if member["title"]
                )
            )
            output[key] = {
                "chunk_id": members[0]["id"],
                "chunk_ids": [member["id"] for member in members],
                "stable_id": members[0]["stable_id"],
                "stable_ids": [member["stable_id"] for member in members],
                "document_stable_id": members[0]["document_stable_id"],
                "citation": members[0]["citation"],
                "title": " / ".join(titles) if titles else None,
                "text": combined_text,
                "text_hash": hashlib.sha256(combined_text.encode()).hexdigest(),
                "locator": (
                    locators[0]
                    if len(locators) == 1
                    else {"kind": "grouped_section", "members": locators}
                ),
            }
        return output

    def _impacts(self, connection, comparison_row) -> list[dict[str, object]]:
        changed = list(
            connection.execute(
                select(comparison_changes).where(
                    comparison_changes.c.comparison_id == comparison_row["id"],
                    comparison_changes.c.classification.not_in(
                        ["unchanged", "parser_only"]
                    ),
                )
            ).mappings()
        )
        old_chunk_ids: set[str] = set()
        for row in changed:
            if not row["old_json"]:
                continue
            old = json.loads(row["old_json"])
            old_chunk_ids.update(old.get("chunk_ids") or [old["chunk_id"]])
        if not old_chunk_ids:
            return []
        impacts = []
        for item in connection.execute(select(saved_items)).mappings():
            payload = json.loads(item["payload_json"])
            evidence_ids = set(_find_values(payload, "chunk_id"))
            matching = sorted(evidence_ids & old_chunk_ids)
            if matching:
                reviewed = connection.execute(
                    select(comparison_reviews.c.reviewed_at)
                    .where(
                        comparison_reviews.c.comparison_id == comparison_row["id"],
                        comparison_reviews.c.item_id == item["id"],
                    )
                    .order_by(comparison_reviews.c.reviewed_at.desc())
                    .limit(1)
                ).scalar()
                impacts.append(
                    {
                        "item_id": item["id"],
                        "kind": item["kind"],
                        "matching_chunk_ids": matching,
                        "reviewed_at": reviewed.isoformat() if reviewed else None,
                    }
                )
        return impacts


def _canonical_key(row) -> str:
    citation = row["citation"]
    if citation:
        normalized = re.sub(r"\s+", " ", citation.strip().casefold())
        return f"citation:{normalized}"
    document = row["document_stable_id"] or "document"
    stable = row["stable_id"] or "chunk"
    return f"stable:{document}::{stable}"


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _mark_uncertain_renumberings(changes: list[dict[str, object]]) -> None:
    """Label exact-text add/remove pairs without asserting an identity mapping."""
    removed: dict[str, list[dict[str, object]]] = {}
    added: dict[str, list[dict[str, object]]] = {}
    for change in changes:
        classification = change["classification"]
        if classification == "removed":
            before = json.loads(str(change["old_json"]))
            removed.setdefault(_normalize(before["text"]), []).append(change)
        elif classification == "added":
            after = json.loads(str(change["new_json"]))
            added.setdefault(_normalize(after["text"]), []).append(change)
    for normalized_text in set(removed) & set(added):
        # Duplicate boilerplate is not enough evidence for a mapping. Only a unique
        # exact-text pair is labeled as a possible renumbering, never auto-matched.
        if not normalized_text:
            continue
        if len(removed[normalized_text]) != 1 or len(added[normalized_text]) != 1:
            continue
        for change in (removed[normalized_text][0], added[normalized_text][0]):
            change["classification"] = "uncertain"
            change["certainty"] = "low"


def _classify(before, after, *, parser_changed: bool, identical_artifact: bool):
    if before is None:
        return "added", "high"
    if after is None:
        return "removed", "high"
    old_text = _normalize(before["text"])
    new_text = _normalize(after["text"])
    if old_text != new_text:
        return "text_changed", "high"
    metadata_fields = ("citation", "title", "locator")
    metadata_changed = any(before[key] != after[key] for key in metadata_fields)
    if (
        identical_artifact
        and parser_changed
        and (before["text_hash"] != after["text_hash"] or metadata_changed)
    ):
        return "parser_only", "high"
    if metadata_changed:
        return "metadata_only", "high"
    return "unchanged", "high"


def _diff(before, after) -> str | None:
    if before is None or after is None:
        return None
    if _normalize(before["text"]) == _normalize(after["text"]):
        return None
    return "\n".join(
        difflib.unified_diff(
            before["text"].splitlines(),
            after["text"].splitlines(),
            fromfile="baseline",
            tofile="target",
            lineterm="",
            n=3,
        )
    )


def _change(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "canonical_key": row["canonical_key"],
        "classification": row["classification"],
        "old": json.loads(row["old_json"]) if row["old_json"] else None,
        "new": json.loads(row["new_json"]) if row["new_json"] else None,
        "diff": row["diff_text"],
        "certainty": row["certainty"],
    }


def _find_values(value: object, key: str):
    if isinstance(value, dict):
        for name, child in value.items():
            if name == key and isinstance(child, str):
                yield child
            yield from _find_values(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _find_values(child, key)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _markdown(payload: dict[str, object], exported_at: str) -> str:
    lines = [
        f"# Source comparison: {payload['module_slug']}",
        "",
        f"Exported: {exported_at}",
        f"Baseline: `{payload['baseline_version_id']}`",
        f"Target: `{payload['target_version_id']}`",
        f"Normalization: `{payload['normalization_version']}`",
        f"Matcher: `{payload['matcher_version']}`",
        "",
        "## Counts",
        "",
    ]
    for classification, count in sorted(payload["counts"].items()):
        lines.append(f"- {classification}: {count}")
    lines.extend(["", "## Changes", ""])
    for change in payload["changes"]:
        lines.extend(
            [
                f"### {change['canonical_key']}",
                "",
                f"Classification: **{change['classification']}**",
                f"Certainty: {change['certainty']}",
                "",
            ]
        )
        if change["diff"]:
            lines.extend(["```diff", change["diff"], "```", ""])
    return "\n".join(lines)
