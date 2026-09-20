from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from app.storage.database import LocalStorage
from app.storage.schema import (
    comparison_reviews,
    matter_items,
    matter_notes,
    matters,
    save_receipts,
    saved_items,
)


class MatterError(RuntimeError):
    pass


class MatterConflict(MatterError):
    pass


class MatterService:
    """Own immutable research payloads and their editable matter organization."""

    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def create(
        self,
        title: str,
        *,
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, object]:
        title = _bounded_text(title, "title", 255, required=True)
        description = _bounded_text(description, "description", 20_000)
        clean_tags = _tags(tags or [])
        matter_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        with self._storage.state_engine.begin() as connection:
            connection.execute(
                insert(matters).values(
                    id=matter_id,
                    title=title,
                    description=description,
                    tags_json=_json(clean_tags),
                    archived=False,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._refresh_fts(connection, matter_id)
        return self.get(matter_id)

    def list(
        self,
        *,
        query: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 250:
            raise MatterError("Matter limit must be between 1 and 250.")
        with self._storage.state_engine.connect() as connection:
            statement = select(matters)
            if not include_archived:
                statement = statement.where(matters.c.archived.is_(False))
            if query and query.strip():
                matching = text(
                    "SELECT DISTINCT matter_id FROM matter_fts "
                    "WHERE matter_fts MATCH :query"
                )
                try:
                    ids = set(connection.scalars(matching, {"query": query.strip()}))
                except Exception as exc:
                    raise MatterError("Matter search query is invalid.") from exc
                if not ids:
                    return []
                statement = statement.where(matters.c.id.in_(ids))
            rows = connection.execute(
                statement.order_by(matters.c.updated_at.desc()).limit(limit)
            ).mappings()
            return [self._matter_summary(connection, row) for row in rows]

    def get(self, matter_id: str) -> dict[str, object]:
        with self._storage.state_engine.connect() as connection:
            row = (
                connection.execute(select(matters).where(matters.c.id == matter_id))
                .mappings()
                .first()
            )
            if row is None:
                raise MatterError("Matter not found.")
            payload = self._matter_summary(connection, row)
            item_rows = connection.execute(
                select(saved_items, matter_items.c.display_order)
                .join(matter_items, matter_items.c.item_id == saved_items.c.id)
                .where(matter_items.c.matter_id == matter_id)
                .order_by(matter_items.c.display_order, matter_items.c.created_at)
            ).mappings()
            payload["items"] = [_saved_item(row) for row in item_rows]
            note_rows = connection.execute(
                select(matter_notes)
                .where(matter_notes.c.matter_id == matter_id)
                .order_by(matter_notes.c.created_at)
            ).mappings()
            payload["notes"] = [_note(row) for row in note_rows]
            return payload

    def update(
        self,
        matter_id: str,
        changes: dict[str, object],
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        allowed = {"title", "description", "tags", "archived"}
        if not changes or not set(changes) <= allowed:
            raise MatterError("Matter update contains unsupported fields.")
        values: dict[str, object] = {
            "revision": expected_revision + 1,
            "updated_at": datetime.now(UTC),
        }
        if "title" in changes:
            values["title"] = _bounded_text(
                changes["title"], "title", 255, required=True
            )
        if "description" in changes:
            values["description"] = _bounded_text(
                changes["description"], "description", 20_000
            )
        if "tags" in changes:
            if not isinstance(changes["tags"], list):
                raise MatterError("tags must be a list of strings.")
            values["tags_json"] = _json(_tags(changes["tags"]))
        if "archived" in changes:
            if not isinstance(changes["archived"], bool):
                raise MatterError("archived must be true or false.")
            values["archived"] = changes["archived"]
        with self._storage.state_engine.begin() as connection:
            result = connection.execute(
                update(matters)
                .where(
                    matters.c.id == matter_id,
                    matters.c.revision == expected_revision,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                exists = connection.scalar(
                    select(matters.c.id).where(matters.c.id == matter_id)
                )
                if exists is None:
                    raise MatterError("Matter not found.")
                raise MatterConflict(
                    "Matter changed since it was loaded; refresh and retry."
                )
            self._refresh_fts(connection, matter_id)
        return self.get(matter_id)

    def delete(self, matter_id: str, *, apply: bool = False) -> dict[str, object]:
        with self._storage.state_engine.begin() as connection:
            row = connection.execute(
                select(matters.c.title).where(matters.c.id == matter_id)
            ).first()
            if row is None:
                raise MatterError("Matter not found.")
            item_count = int(
                connection.scalar(
                    select(func.count())
                    .select_from(matter_items)
                    .where(matter_items.c.matter_id == matter_id)
                )
                or 0
            )
            preview = {
                "matter_id": matter_id,
                "title": row.title,
                "linked_item_count": item_count,
                "saved_items_deleted": 0,
                "saved_items_retained": item_count,
                "apply_required": True,
            }
            if not apply:
                return preview
            connection.execute(
                text("DELETE FROM matter_fts WHERE matter_id = :matter_id"),
                {"matter_id": matter_id},
            )
            connection.execute(delete(matters).where(matters.c.id == matter_id))
            preview["status"] = "deleted"
            preview["apply_required"] = False
            return preview

    def save_payload(
        self,
        matter_id: str,
        *,
        kind: str,
        payload: dict[str, object],
        source_identity: str,
        idempotency_key: str,
        original_operation_id: str | None = None,
    ) -> dict[str, object]:
        if kind not in {"answer", "search", "property_dossier", "comparison"}:
            raise MatterError("Unsupported saved-item kind.")
        source_identity = _bounded_text(
            source_identity, "source identity", 255, required=True
        )
        idempotency_key = _bounded_text(
            idempotency_key, "idempotency key", 160, required=True
        )
        content = _json(payload)
        digest = hashlib.sha256(content.encode()).hexdigest()
        now = datetime.now(UTC)
        with self._storage.state_engine.begin() as connection:
            if (
                connection.scalar(select(matters.c.id).where(matters.c.id == matter_id))
                is None
            ):
                raise MatterError("Matter not found.")
            receipt = (
                connection.execute(
                    select(save_receipts).where(
                        save_receipts.c.idempotency_key == idempotency_key
                    )
                )
                .mappings()
                .first()
            )
            if receipt:
                if receipt["source_identity"] != source_identity:
                    raise MatterConflict(
                        "Idempotency key was already used for another result."
                    )
                self._link(connection, matter_id, receipt["item_id"], now)
                self._refresh_fts(connection, matter_id)
                return self._get_item(connection, receipt["item_id"])
            item_id = str(uuid.uuid4())
            connection.execute(
                insert(saved_items).values(
                    id=item_id,
                    kind=kind,
                    payload_version=1,
                    payload_json=content,
                    payload_hash=digest,
                    source_identity=source_identity,
                    original_operation_id=original_operation_id,
                    parent_item_id=None,
                    created_at=now,
                )
            )
            self._link(connection, matter_id, item_id, now)
            try:
                connection.execute(
                    insert(save_receipts).values(
                        idempotency_key=idempotency_key,
                        source_identity=source_identity,
                        item_id=item_id,
                        created_at=now,
                    )
                )
            except IntegrityError as exc:
                raise MatterConflict("Concurrent save conflict; retry safely.") from exc
            self._refresh_fts(connection, matter_id)
            return self._get_item(connection, item_id)

    def link(self, matter_id: str, item_id: str) -> dict[str, object]:
        with self._storage.state_engine.begin() as connection:
            if (
                connection.scalar(select(matters.c.id).where(matters.c.id == matter_id))
                is None
            ):
                raise MatterError("Matter not found.")
            if (
                connection.scalar(
                    select(saved_items.c.id).where(saved_items.c.id == item_id)
                )
                is None
            ):
                raise MatterError("Saved item not found.")
            self._link(connection, matter_id, item_id, datetime.now(UTC))
            self._refresh_fts(connection, matter_id)
            return self._get_item(connection, item_id)

    def get_item(self, item_id: str) -> dict[str, object]:
        with self._storage.state_engine.connect() as connection:
            return self._get_item(connection, item_id)

    def delete_item(
        self,
        item_id: str,
        *,
        matter_id: str | None = None,
        apply: bool = False,
    ) -> dict[str, object]:
        with self._storage.state_engine.begin() as connection:
            linked = list(
                connection.scalars(
                    select(matter_items.c.matter_id).where(
                        matter_items.c.item_id == item_id
                    )
                )
            )
            if (
                not linked
                and connection.scalar(
                    select(saved_items.c.id).where(saved_items.c.id == item_id)
                )
                is None
            ):
                raise MatterError("Saved item not found.")
            if matter_id is not None and matter_id not in linked:
                raise MatterError("Saved item is not linked to that matter.")
            affected = [matter_id] if matter_id else linked
            remaining = [linked_id for linked_id in linked if linked_id not in affected]
            preview = {
                "item_id": item_id,
                "operation": "unlink" if matter_id else "delete",
                "affected_matter_ids": affected,
                "remaining_matter_ids": remaining,
                "saved_item_will_be_deleted": not matter_id or not remaining,
                "apply_required": True,
            }
            if not apply:
                return preview
            if not matter_id or not remaining:
                # Deleting a saved item also retires its save receipt. Reusing
                # that key afterward is a fresh save, never a dangling receipt.
                connection.execute(
                    delete(save_receipts).where(save_receipts.c.item_id == item_id)
                )
                connection.execute(
                    delete(comparison_reviews).where(
                        comparison_reviews.c.item_id == item_id
                    )
                )
                connection.execute(
                    update(saved_items)
                    .where(saved_items.c.parent_item_id == item_id)
                    .values(parent_item_id=None)
                )
            if matter_id:
                connection.execute(
                    delete(matter_items).where(
                        matter_items.c.item_id == item_id,
                        matter_items.c.matter_id == matter_id,
                    )
                )
                if not remaining:
                    connection.execute(
                        delete(saved_items).where(saved_items.c.id == item_id)
                    )
            else:
                connection.execute(
                    delete(saved_items).where(saved_items.c.id == item_id)
                )
            for linked_matter_id in affected:
                self._refresh_fts(connection, linked_matter_id)
            preview["status"] = "deleted" if not remaining else "unlinked"
            preview["apply_required"] = False
            return preview

    def put_note(
        self,
        *,
        body: str,
        matter_id: str | None = None,
        item_id: str | None = None,
        note_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        if bool(matter_id) == bool(item_id):
            raise MatterError("A note must belong to exactly one matter or item.")
        body = _bounded_text(body, "note", 50_000)
        now = datetime.now(UTC)
        with self._storage.state_engine.begin() as connection:
            if (
                matter_id
                and connection.scalar(
                    select(matters.c.id).where(matters.c.id == matter_id)
                )
                is None
            ):
                raise MatterError("Matter not found.")
            if (
                item_id
                and connection.scalar(
                    select(saved_items.c.id).where(saved_items.c.id == item_id)
                )
                is None
            ):
                raise MatterError("Saved item not found.")
            if note_id:
                if expected_revision is None:
                    raise MatterError("expected_revision is required for note edits.")
                ownership = (
                    matter_notes.c.matter_id == matter_id
                    if matter_id
                    else matter_notes.c.item_id == item_id
                )
                result = connection.execute(
                    update(matter_notes)
                    .where(
                        matter_notes.c.id == note_id,
                        matter_notes.c.revision == expected_revision,
                        ownership,
                    )
                    .values(
                        body=body,
                        revision=expected_revision + 1,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    raise MatterConflict(
                        "Note changed since it was loaded; refresh and retry."
                    )
            else:
                note_id = str(uuid.uuid4())
                connection.execute(
                    insert(matter_notes).values(
                        id=note_id,
                        matter_id=matter_id,
                        item_id=item_id,
                        body=body,
                        revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            affected = matter_id
            if not affected and item_id:
                affected = connection.scalar(
                    select(matter_items.c.matter_id)
                    .where(matter_items.c.item_id == item_id)
                    .limit(1)
                )
            if affected:
                self._refresh_fts(connection, affected)
            row = (
                connection.execute(
                    select(matter_notes).where(matter_notes.c.id == note_id)
                )
                .mappings()
                .one()
            )
            return _note(row)

    def export(self, matter_id: str, destination_directory: Path) -> Path:
        matter = self.get(matter_id)
        created = datetime.now(UTC).isoformat()
        matter_json = (
            _json({"export_version": 1, "exported_at": created, **matter}) + "\n"
        ).encode()
        matter_markdown = _matter_markdown(matter, created).encode()
        files = {"matter.json": matter_json, "matter.md": matter_markdown}
        manifest = {
            "format": "nyc-housing-research-matter",
            "format_version": 1,
            "matter_id": matter_id,
            "exported_at": created,
            "files": {
                name: {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
                for name, content in files.items()
            },
        }
        files["manifest.json"] = (_json(manifest) + "\n").encode()
        destination_directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination_directory, prefix=".matter-", suffix=".tmp"
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        final = destination_directory / (
            f"matter-{matter_id[:8]}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip"
        )
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, content in sorted(files.items()):
                    archive.writestr(name, content)
            os.replace(temporary, final)
        finally:
            temporary.unlink(missing_ok=True)
        return final

    def _matter_summary(self, connection, row) -> dict[str, object]:
        item_count = int(
            connection.scalar(
                select(func.count())
                .select_from(matter_items)
                .where(matter_items.c.matter_id == row["id"])
            )
            or 0
        )
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "tags": json.loads(row["tags_json"]),
            "archived": bool(row["archived"]),
            "revision": row["revision"],
            "item_count": item_count,
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    def _link(self, connection, matter_id: str, item_id: str, now: datetime) -> None:
        present = connection.execute(
            select(matter_items.c.matter_id).where(
                matter_items.c.matter_id == matter_id,
                matter_items.c.item_id == item_id,
            )
        ).first()
        if present:
            return
        display_order = (
            int(
                connection.scalar(
                    select(
                        func.coalesce(func.max(matter_items.c.display_order), 0)
                    ).where(matter_items.c.matter_id == matter_id)
                )
                or 0
            )
            + 1
        )
        connection.execute(
            insert(matter_items).values(
                matter_id=matter_id,
                item_id=item_id,
                display_order=display_order,
                created_at=now,
            )
        )

    def _get_item(self, connection, item_id: str) -> dict[str, object]:
        row = (
            connection.execute(select(saved_items).where(saved_items.c.id == item_id))
            .mappings()
            .first()
        )
        if row is None:
            raise MatterError("Saved item not found.")
        return _saved_item(row)

    def _refresh_fts(self, connection, matter_id: str) -> None:
        row = (
            connection.execute(select(matters).where(matters.c.id == matter_id))
            .mappings()
            .first()
        )
        connection.execute(
            text("DELETE FROM matter_fts WHERE matter_id = :matter_id"),
            {"matter_id": matter_id},
        )
        if row is None:
            return
        item_rows = connection.execute(
            select(saved_items.c.id, saved_items.c.payload_json)
            .join(matter_items, matter_items.c.item_id == saved_items.c.id)
            .where(matter_items.c.matter_id == matter_id)
        )
        note_text = " ".join(
            connection.scalars(
                select(matter_notes.c.body).where(
                    (matter_notes.c.matter_id == matter_id)
                    | (
                        matter_notes.c.item_id.in_(
                            select(matter_items.c.item_id).where(
                                matter_items.c.matter_id == matter_id
                            )
                        )
                    )
                )
            )
        )
        items = list(item_rows)
        body = " ".join(
            [row["description"], note_text, *[item.payload_json for item in items]]
        )
        connection.execute(
            text(
                "INSERT INTO matter_fts(matter_id,item_id,title,body,tags) "
                "VALUES(:matter_id,:item_id,:title,:body,:tags)"
            ),
            {
                "matter_id": matter_id,
                "item_id": " ".join(item.id for item in items),
                "title": row["title"],
                "body": body,
                "tags": " ".join(json.loads(row["tags_json"])),
            },
        )


def _saved_item(row) -> dict[str, object]:
    payload = {
        "id": row["id"],
        "kind": row["kind"],
        "payload_version": row["payload_version"],
        "payload": json.loads(row["payload_json"]),
        "payload_hash": row["payload_hash"],
        "source_identity": row["source_identity"],
        "original_operation_id": row["original_operation_id"],
        "parent_item_id": row["parent_item_id"],
        "created_at": row["created_at"].isoformat(),
    }
    if "display_order" in row:
        payload["display_order"] = row["display_order"]
    return payload


def _note(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "matter_id": row["matter_id"],
        "item_id": row["item_id"],
        "body": row["body"],
        "revision": row["revision"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _bounded_text(
    value: object, label: str, limit: int, *, required: bool = False
) -> str:
    if not isinstance(value, str):
        raise MatterError(f"{label} must be text.")
    cleaned = value.strip()
    if required and not cleaned:
        raise MatterError(f"{label} is required.")
    if len(cleaned) > limit:
        raise MatterError(f"{label} exceeds {limit} characters.")
    return cleaned


def _tags(values: list[object]) -> list[str]:
    if len(values) > 25:
        raise MatterError("A matter can have at most 25 tags.")
    result = []
    for value in values:
        tag = _bounded_text(value, "tag", 60, required=True)
        if tag.casefold() not in {item.casefold() for item in result}:
            result.append(tag)
    return result


def _json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise MatterError("Saved payload must be valid JSON data.") from exc


def _matter_markdown(matter: dict[str, object], exported_at: str) -> str:
    lines = [
        f"# {matter['title']}",
        "",
        f"Exported: {exported_at}",
        f"Matter ID: `{matter['id']}`",
        f"Tags: {', '.join(matter['tags']) or 'none'}",
        "",
        str(matter["description"]),
        "",
    ]
    for item in matter.get("items", []):
        lines.extend(
            [
                f"## Saved {item['kind']}",
                "",
                f"Saved item: `{item['id']}`",
                f"Payload SHA-256: `{item['payload_hash']}`",
                "",
                "```json",
                json.dumps(
                    item["payload"], ensure_ascii=False, indent=2, sort_keys=True
                ),
                "```",
                "",
            ]
        )
    notes = matter.get("notes", [])
    if notes:
        lines.extend(["## Notes", ""])
        for note in notes:
            lines.extend([str(note["body"]), ""])
    return "\n".join(lines)
