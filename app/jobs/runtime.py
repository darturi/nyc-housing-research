from __future__ import annotations

import threading
from dataclasses import dataclass, field
from time import monotonic


class OperationCancelled(RuntimeError):
    pass


class OperationDeadlineExceeded(TimeoutError):
    pass


@dataclass
class CancellationSignal:
    _event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OperationCancelled("The operation was cancelled.")


@dataclass(frozen=True)
class Deadline:
    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> Deadline:
        return cls(expires_at=monotonic() + max(0.0, seconds))

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - monotonic())

    def raise_if_expired(self) -> None:
        if self.remaining_seconds <= 0:
            raise OperationDeadlineExceeded("The operation deadline was exceeded.")
