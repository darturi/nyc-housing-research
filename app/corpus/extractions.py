from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import insert, select

from app.corpus.resource_parsers import inspect_pdf
from app.corpus.resources import ResourceService
from app.corpus.service import CorpusValidationError
from app.storage.database import LocalStorage
from app.storage.schema import extraction_runs


class ExtractionService:
    """Inspect extraction coverage and report unavailable optional runtimes honestly."""

    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage
        self._resources = ResourceService(storage)

    def run(
        self,
        resource_id: str,
        *,
        operation: str,
        pages: list[int] | None = None,
        languages: list[str] | None = None,
    ) -> dict[str, object]:
        if operation not in {"inspect", "ocr"}:
            raise CorpusValidationError("Extraction operation must be inspect or ocr.")
        resource = self._resources.get(resource_id)
        version_id = resource["active_version_id"] or resource["latest_version_id"]
        if not version_id:
            raise CorpusValidationError("Resource has no retained version to inspect.")
        provenance = self._resources.version(resource_id, version_id)["provenance"]
        if provenance.get("media_type") != "application/pdf":
            raise CorpusValidationError(
                "OCR inspection is available only for PDF resources."
            )
        report = inspect_pdf(
            self._resources.original_path(resource_id, version_id).read_bytes()
        )
        status = "inspected"
        if operation == "ocr":
            selected = pages or [
                int(item["physical_page"])
                for item in report["pages"]
                if item["ocr_candidate"]
            ]
            if len(selected) > 100 or any(page < 1 for page in selected):
                raise CorpusValidationError(
                    "Select between 0 and 100 valid physical pages for OCR."
                )
            report |= {
                "selected_pages": selected,
                "recognition_languages": languages or ["en"],
                "status": "ocr_unavailable",
                "reason_code": "reviewed_runtime_not_bundled",
                "message": (
                    "Inspection completed, but OCR was not run because this build has "
                    "no reviewed, pinned local engine and language package."
                ),
            }
            status = "ocr_unavailable"
        else:
            report["status"] = status
        run_id = str(uuid.uuid4())
        created_at = datetime.now(UTC)
        with self._storage.state_engine.begin() as connection:
            connection.execute(
                insert(extraction_runs).values(
                    id=run_id,
                    resource_id=resource_id,
                    source_version_id=version_id,
                    operation=operation,
                    status=status,
                    report_json=json.dumps(report, sort_keys=True),
                    created_at=created_at,
                )
            )
        return {
            "id": run_id,
            "resource_id": resource_id,
            "source_version_id": version_id,
            "operation": operation,
            "status": status,
            "report": report,
            "created_at": created_at.isoformat(),
        }

    def get(self, resource_id: str, run_id: str) -> dict[str, object]:
        with self._storage.state_engine.connect() as connection:
            row = (
                connection.execute(
                    select(extraction_runs).where(
                        extraction_runs.c.id == run_id,
                        extraction_runs.c.resource_id == resource_id,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise CorpusValidationError("Extraction run not found.")
        return {
            "id": row["id"],
            "resource_id": row["resource_id"],
            "source_version_id": row["source_version_id"],
            "operation": row["operation"],
            "status": row["status"],
            "report": json.loads(row["report_json"]),
            "created_at": row["created_at"].isoformat(),
        }
