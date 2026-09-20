"""Crash-released ownership for local work; lock files are not the locks."""

from __future__ import annotations

import atexit
import os
import threading
import uuid
from pathlib import Path


class LockBusy(RuntimeError):
    pass


class FileLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise LockBusy("This lease already holds its workspace lock.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        handle = os.fdopen(fd, "r+b")
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise LockBusy(
                f"Workspace operation already owns {self.path.name}."
            ) from exc
        self._handle = handle

    @property
    def held(self) -> bool:
        return self._handle is not None

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


_OWNERS: dict[tuple[int, str], tuple[str, FileLease]] = {}
_OWNER_LOCK = threading.Lock()


def workspace_root(engine) -> Path:
    database = engine.url.database
    if not database or database == ":memory:":
        raise ValueError("Durable ownership requires a file-backed workspace.")
    return Path(database).resolve().parent


def process_owner(engine) -> str:
    root = workspace_root(engine)
    key = (os.getpid(), str(root))
    with _OWNER_LOCK:
        if key not in _OWNERS:
            token = uuid.uuid4().hex
            lease = FileLease(root / "runtime" / "owners" / f"{token}.lock")
            lease.acquire()
            _OWNERS[key] = (f"proc:{token}", lease)
            atexit.register(lease.release)
        return _OWNERS[key][0]


def owner_alive(engine, owner: str | None) -> bool | None:
    """None denotes a legacy row without recoverable process ownership."""
    if not owner or not owner.startswith("proc:"):
        return None
    token = owner.split(":", 2)[1]
    try:
        if uuid.UUID(token).hex != token:
            return None
    except ValueError:
        return None
    lease = FileLease(workspace_root(engine) / "runtime" / "owners" / f"{token}.lock")
    try:
        lease.acquire()
    except LockBusy:
        return True
    else:
        lease.release()
        return False
