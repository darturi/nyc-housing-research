from __future__ import annotations

import io
import tarfile
import zipfile

from app.maintenance.release_audit import audit_archive


def test_release_audit_accepts_clean_wheel(tmp_path) -> None:
    wheel = tmp_path / "example-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("app/main.py", "print('safe')\n")
        archive.writestr("example.dist-info/METADATA", "Name: example\n")

    report = audit_archive(wheel)

    assert report.ok is True
    assert report.file_count == 2
    assert len(report.sha256) == 64


def test_release_audit_rejects_runtime_files_secrets_and_private_paths(
    tmp_path,
) -> None:
    wheel = tmp_path / "unsafe-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "app/main.py", "TOKEN = 'sk-proj-this-is-not-safe-123456789'\n"
        )
        archive.writestr("artifacts/download.bin", b"source")
        archive.writestr("state.sqlite3", b"SQLite format 3")
        archive.writestr("README.md", "/Users/private-person/project\n")

    report = audit_archive(wheel)

    assert report.ok is False
    assert any("OpenAI-style secret" in item for item in report.violations)
    assert any("forbidden runtime directory" in item for item in report.violations)
    assert any("forbidden runtime file suffix" in item for item in report.violations)
    assert any("macOS/Linux user path" in item for item in report.violations)


def test_release_audit_allows_deliberate_fake_secrets_in_sdist_tests(tmp_path) -> None:
    sdist = tmp_path / "example-0.1.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        payload = b"credential = 'sk-fixture-this-is-deliberately-fake'\n"
        member = tarfile.TarInfo("example-0.1.0/tests/test_credentials.py")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    report = audit_archive(sdist)

    assert report.ok is True


def test_release_audit_rejects_traversal_and_links(tmp_path) -> None:
    wheel = tmp_path / "unsafe-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("../escape.txt", "bad")
        link = zipfile.ZipInfo("app/link")
        link.external_attr = (0o120777 << 16) | 0xA000
        archive.writestr(link, "target")

    report = audit_archive(wheel)

    assert report.ok is False
    assert any("parent-traversal" in item for item in report.violations)
    assert any("archive links are not allowed" in item for item in report.violations)
