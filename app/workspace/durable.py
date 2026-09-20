"""Flush files and atomic metadata updates before publishing recovery state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def sync_file(path: Path) -> None:
    # A writable handle also supports Windows' underlying flush operation.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def sync_directory(path: Path) -> None:
    # Windows does not support opening directory handles through os.open.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json_atomic(path: Path, payload: object) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
