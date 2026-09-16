import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.corpus.bundle import CanonicalBundleService
from app.corpus.service import CorpusService, CorpusValidationError, SourceArtifact
from app.retrieval.local import LocalSearch
from app.storage.database import LocalStorage
from app.workspace.paths import resolve_workspace_paths


def _workspace(path: Path) -> LocalStorage:
    paths = resolve_workspace_paths(data_dir=path, environment={})
    return LocalStorage.open(paths, initialize=True)


def _install_fixture(storage: LocalStorage) -> str:
    return CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Grounds for summary proceedings",
                source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )


def test_bundle_round_trip_preserves_searchable_evidence(tmp_path) -> None:
    source = _workspace(tmp_path / "source")
    target = _workspace(tmp_path / "target")
    try:
        generation_id = _install_fixture(source)
        destination = tmp_path / "portable.nychousing"
        exported = CanonicalBundleService(source).export(destination)
        imported = CanonicalBundleService(target).import_bundle(
            destination, allow_partial=True
        )

        assert imported == exported
        assert imported.generation_id == generation_id
        result = LocalSearch(target).search("RPAPL section 711")
        assert result.results[0].citation == "RPAPL § 711"
    finally:
        source.close()
        target.close()


def test_bundle_does_not_include_private_state(tmp_path) -> None:
    storage = _workspace(tmp_path / "source")
    try:
        _install_fixture(storage)
        destination = tmp_path / "portable.nychousing"
        CanonicalBundleService(storage).export(destination)
        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
            records = archive.read("records.json")
        assert "state.sqlite3" not in names
        assert b"usage_events" not in records
        assert b"local_sessions" not in records
        assert str(tmp_path).encode() not in records
    finally:
        storage.close()


def test_corrupt_bundle_fails_without_replacing_active_generation(tmp_path) -> None:
    storage = _workspace(tmp_path / "workspace")
    try:
        generation_id = _install_fixture(storage)
        valid = tmp_path / "valid.nychousing"
        corrupt = tmp_path / "corrupt.nychousing"
        CanonicalBundleService(storage).export(valid)
        with zipfile.ZipFile(valid) as source, zipfile.ZipFile(corrupt, "w") as target:
            for name in source.namelist():
                content = source.read(name)
                if name == "records.json":
                    content += b"tampered"
                target.writestr(name, content)

        with pytest.raises(CorpusValidationError, match="verification"):
            CanonicalBundleService(storage).import_bundle(corrupt, allow_partial=True)
        assert CorpusService(storage).status().active_generation_id == generation_id
    finally:
        storage.close()


def test_bundle_rejects_unsafe_members(tmp_path) -> None:
    bundle = tmp_path / "unsafe.nychousing"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("../escape", b"bad")
        archive.writestr("manifest.json", b"{}")
        archive.writestr("records.json", b"{}")
    storage = _workspace(tmp_path / "workspace")
    try:
        with pytest.raises(CorpusValidationError, match="unsafe path"):
            CanonicalBundleService(storage).inspect(bundle)
    finally:
        storage.close()
