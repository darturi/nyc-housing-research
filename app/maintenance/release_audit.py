from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

FORBIDDEN_DIRECTORY_NAMES = {
    ".bootstrap",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".uv-cache",
    ".venv",
    "artifacts",
    "backups",
    "exports",
    "logs",
}
FORBIDDEN_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "launch-secret.json",
    "settings.json",
}
FORBIDDEN_SUFFIXES = {".db", ".key", ".log", ".pem", ".sqlite", ".sqlite3"}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "OpenAI-style secret": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "private key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "credential-bearing database URL": re.compile(
        rb"(?:postgres(?:ql)?|mysql|mariadb)://[^\s:/]+:[^\s/@]+@",
        re.IGNORECASE,
    ),
}
PRIVATE_PATH_PATTERNS = {
    "macOS/Linux user path": re.compile(rb"/(?:Users|home)/[^/\s]+/"),
    "Windows user path": re.compile(rb"[A-Za-z]:\\Users\\[^\\\s]+\\"),
}


@dataclass(frozen=True)
class ArchiveAudit:
    path: str
    sha256: str
    file_count: int
    violations: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class _Member:
    name: str
    data: bytes | None
    is_link: bool = False


def _normalized_name(raw_name: str) -> PurePosixPath:
    name = raw_name.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("archive member has an absolute or parent-traversal path")
    return path


def _zip_members(path: Path) -> Iterator[_Member]:
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            unix_mode = info.external_attr >> 16
            is_link = (unix_mode & 0o170000) == 0o120000
            yield _Member(info.filename, archive.read(info), is_link=is_link)


def _tar_members(path: Path) -> Iterator[_Member]:
    with tarfile.open(path, mode="r:gz") as archive:
        for info in archive.getmembers():
            if info.isdir():
                continue
            if info.issym() or info.islnk():
                yield _Member(info.name, None, is_link=True)
                continue
            if not info.isfile():
                yield _Member(info.name, None)
                continue
            stream = archive.extractfile(info)
            yield _Member(info.name, stream.read() if stream is not None else b"")


def _members(path: Path) -> Iterable[_Member]:
    if path.suffix == ".whl" or zipfile.is_zipfile(path):
        return _zip_members(path)
    if path.name.endswith(".tar.gz"):
        return _tar_members(path)
    raise ValueError("expected a .whl or .tar.gz distribution")


def _is_test_fixture(path: PurePosixPath) -> bool:
    return "tests" in {part.casefold() for part in path.parts}


def _content_violations(path: PurePosixPath, data: bytes) -> list[str]:
    if path.suffix.casefold() not in TEXT_SUFFIXES or _is_test_fixture(path):
        return []
    findings: list[str] = []
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(data):
            findings.append(f"{path}: contains {label}")
    for label, pattern in PRIVATE_PATH_PATTERNS.items():
        if pattern.search(data):
            findings.append(f"{path}: contains {label}")
    return findings


def audit_archive(path: Path) -> ArchiveAudit:
    path = path.resolve()
    violations: list[str] = []
    file_count = 0
    try:
        members = _members(path)
        for member in members:
            file_count += 1
            try:
                normalized = _normalized_name(member.name)
            except ValueError as exc:
                violations.append(f"{member.name}: {exc}")
                continue
            if member.is_link:
                violations.append(f"{normalized}: archive links are not allowed")
                continue
            folded_parts = {part.casefold() for part in normalized.parts}
            forbidden_dirs = sorted(folded_parts & FORBIDDEN_DIRECTORY_NAMES)
            if forbidden_dirs:
                violations.append(
                    f"{normalized}: forbidden runtime directory {forbidden_dirs[0]}"
                )
            filename = normalized.name.casefold()
            if filename in FORBIDDEN_FILE_NAMES:
                violations.append(f"{normalized}: forbidden runtime file")
            if normalized.suffix.casefold() in FORBIDDEN_SUFFIXES:
                violations.append(f"{normalized}: forbidden runtime file suffix")
            if member.data is not None:
                violations.extend(_content_violations(normalized, member.data))
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
        violations.append(str(exc))
    return ArchiveAudit(
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        file_count=file_count,
        violations=tuple(sorted(set(violations))),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit built distributions for private/runtime content."
    )
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args(argv)
    reports = [audit_archive(path) for path in args.archives]
    print(
        json.dumps(
            {
                "ok": all(report.ok for report in reports),
                "archives": [
                    {**asdict(report), "ok": report.ok} for report in reports
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if all(report.ok for report in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
