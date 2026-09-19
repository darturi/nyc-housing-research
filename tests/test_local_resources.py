import time

import pytest
from fastapi.testclient import TestClient

from app.corpus.resources import ResourceMetadata, ResourceService
from app.corpus.service import CorpusValidationError
from app.local_app import create_local_app
from app.retrieval.local import LocalSearch, LocalSearchFilters
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext
from app.workspace.paths import resolve_workspace_paths


def _storage(tmp_path):
    paths = resolve_workspace_paths(tmp_path / "workspace", environment={})
    return LocalStorage.open(paths, initialize=True)


def test_user_text_resource_is_searchable_and_traceable(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        service = ResourceService(storage)
        result = service.add(
            b"A lease rider requires thirty days written notice.\nSecond paragraph.",
            filename="lease.md",
            content_type="text/markdown",
            metadata=ResourceMetadata(title="Apartment lease rider"),
        )

        assert result.status == "added"
        assert result.chunk_count == 1
        resource = service.get(result.resource_id)
        assert resource["active"] is True
        assert resource["model_use_allowed"] is False
        assert resource["provenance"]["original_basename"] == "lease.md"

        search = LocalSearch(storage).search(
            "thirty days written notice",
            filters=LocalSearchFilters(source_slug=result.slug),
        )
        assert search.results[0].source_slug == result.slug
        assert search.results[0].citation is None
        assert "lease rider" in search.results[0].text.lower()
    finally:
        storage.close()


def test_resource_remove_and_restore_publish_new_generations(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        service = ResourceService(storage)
        added = service.add(
            b"Tenant notebook entry about a broken boiler.",
            filename="notes.txt",
            metadata=ResourceMetadata(title="Tenant notes"),
        )
        removed = service.remove(added.resource_id)
        assert removed.status == "removed"
        assert service.get(added.resource_id)["active"] is False
        assert LocalSearch(storage).search("broken boiler").results == ()

        restored = service.restore(added.resource_id)
        assert restored.status == "restored"
        assert restored.generation_id != removed.generation_id
        assert LocalSearch(storage).search("broken boiler").results
    finally:
        storage.close()


def test_resource_replacement_preserves_previous_generation(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        service = ResourceService(storage)
        first = service.add(
            b"Original inspection memorandum.",
            filename="memo.txt",
            metadata=ResourceMetadata(title="Inspection memo"),
        )
        second = service.replace_file(
            first.resource_id,
            b"Revised inspection memorandum with mold findings.",
            filename="memo.txt",
            expected_version_id=first.version_id,
        )

        assert second.status == "replaced"
        assert second.version_id != first.version_id
        assert LocalSearch(storage).search("mold findings").results
        rolled_back = service._corpus.rollback()
        assert rolled_back == first.generation_id
        assert LocalSearch(storage).search("mold findings").results == ()
        assert LocalSearch(storage).search("Original inspection").results
    finally:
        storage.close()


def test_resource_mutation_receipt_makes_retried_replace_idempotent(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        service = ResourceService(storage)
        first = service.add(
            b"Original resource body.",
            filename="memo.txt",
            metadata=ResourceMetadata(title="Memo"),
        )
        operation_id = "resource-replace-retry-fixture"
        replaced = service.replace_file(
            first.resource_id,
            b"Replacement resource body.",
            filename="memo.txt",
            expected_version_id=first.version_id,
            operation_id=operation_id,
        )
        retried = service.replace_file(
            first.resource_id,
            b"Replacement resource body.",
            filename="memo.txt",
            expected_version_id=first.version_id,
            operation_id=operation_id,
        )
        assert retried == replaced
        assert len(service.get(first.resource_id)["versions"]) == 2
    finally:
        storage.close()


def test_duplicate_upload_returns_existing_resource(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        service = ResourceService(storage)
        first = service.add(
            b"The same uploaded text.",
            filename="one.txt",
            metadata=ResourceMetadata(title="First title"),
        )
        duplicate = service.add(
            b"The same uploaded text.",
            filename="two.txt",
            metadata=ResourceMetadata(title="Second title"),
        )
        assert duplicate.status == "duplicate"
        assert duplicate.resource_id == first.resource_id
        assert len(service.list()) == 1
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        (b"not supported", "notes.docx", "Unsupported resource type"),
        (b"\xff\xfe", "notes.txt", "UTF-8"),
        (b"%PDF-not-a-real-document", "scan.pdf", "malformed"),
    ],
)
def test_resource_parser_rejects_unsupported_or_invalid_files(
    tmp_path, content, filename, message
) -> None:
    storage = _storage(tmp_path)
    try:
        with pytest.raises(CorpusValidationError, match=message):
            ResourceService(storage).add(
                content,
                filename=filename,
                metadata=ResourceMetadata(title="Invalid resource"),
            )
    finally:
        storage.close()


def test_resource_upload_api_runs_as_job_and_respects_search_scope(tmp_path) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "api-workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    storage.close()
    app = create_local_app(context)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session = client.post(
            "/api/v1/session/exchange",
            headers={"Origin": "http://127.0.0.1"},
            json={"launch_token": app.state.launch_token},
        )
        headers = {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": session.json()["csrf_token"],
        }
        created = client.post(
            "/api/v1/resources",
            headers=headers,
            files={
                "file": (
                    "tenant-notes.md",
                    b"Unique radiator log entry.",
                    "text/markdown",
                )
            },
            data={"title": "Tenant notes", "category": "case notes"},
        )
        assert created.status_code == 202
        job_id = created.json()["id"]
        for _ in range(200):
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            if job["state"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        assert job["state"] == "succeeded"

        resources = client.get("/api/v1/resources").json()["resources"]
        assert resources[0]["name"] == "Tenant notes"
        official = client.post(
            "/api/v1/search",
            headers=headers,
            json={"query": "radiator log", "scope": "core"},
        )
        mine = client.post(
            "/api/v1/search",
            headers=headers,
            json={"query": "radiator log", "scope": "user"},
        )
        assert official.json()["results"] == []
        assert mine.json()["results"][0]["origin"] == "user"
        assert mine.json()["results"][0]["locator"]["paragraph_start"] == 1

        resource_id = resources[0]["id"]
        removed = client.post(
            f"/api/v1/resources/{resource_id}/remove", headers=headers, json={}
        )
        assert removed.status_code == 200
        assert removed.json()["result"]["status"] == "removed"
        restored = client.post(
            f"/api/v1/resources/{resource_id}/restore", headers=headers, json={}
        )
        assert restored.status_code == 200
        assert restored.json()["result"]["status"] == "restored"
