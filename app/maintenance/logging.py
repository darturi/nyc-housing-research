from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path


class LocalDiagnosticLog:
    """Best-effort metadata-only JSONL diagnostics for the local workspace."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._lock = threading.Lock()

    def record(self, event: str, **fields: object) -> None:
        now = datetime.now(UTC)
        payload = {
            "timestamp": now.isoformat(),
            "event": _safe_value(event),
            **{key: _safe_value(value) for key, value in fields.items()},
        }
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            path = self._directory / f"app-{now.date().isoformat()}.log"
            line = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
            with self._lock, path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            if os.name != "nt":
                path.chmod(0o600)
        except OSError:
            # Diagnostics must never make the local application unavailable.
            return


def _safe_value(value: object) -> str | int | bool | None:
    if value is None or isinstance(value, (int, bool)):
        return value
    text = str(value).replace("\r", " ").replace("\n", " ")
    return "".join(character for character in text if character.isprintable())[:200]
