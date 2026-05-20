"""Generic atomic JSON status file with thread-safe mutators.

Both ``watcher.status.StatusWriter`` and ``youtube.upload_status.UploadStatusWriter``
boil down to the same pattern:

  * a dataclass describing the state
  * an instance lock to serialise mutations
  * atomic write (temp file + rename) so concurrent readers never see
    a half-written document
  * a rolling ``log_tail`` capped at ``LOG_TAIL_MAX`` entries
  * the file is read once on construction and rewritten on every change

Sharing a base also lets us tune persistence in one place: ``durable=True``
fsyncs the file (used at terminal transitions like begin / done / error)
while ``durable=False`` (the default for progress updates) skips the
fsync. On HDD-backed Synology storage a 50-video upload would otherwise
trigger 500+ fsyncs - none of which carry information we cannot recompute.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generic, Optional, TypeVar

sys.stdout.reconfigure(encoding="utf-8")


T = TypeVar("T")


def now_iso() -> str:
    """ISO-8601 timestamp with timezone, second precision."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class JsonStatusFile(Generic[T]):
    """Atomic JSON-on-disk persistence for a dataclass-shaped state.

    Subclasses pass a ``factory`` (zero-arg callable returning a default
    dataclass instance) so the file can be initialised when missing.
    All mutator methods are safe to call from multiple threads.
    """

    LOG_TAIL_MAX = 200

    def __init__(self, path: Path, factory: Callable[[], T]) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._factory = factory
        existing = self._read()
        self._state: T = existing if existing is not None else factory()
        if existing is None:
            self._persist(durable=True)

    # ---- internal helpers -----------------------------------------------

    def _read(self) -> Optional[T]:
        if not self.path.is_file():
            return None
        with self.path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        cls = type(self._factory())
        return cls(**data)

    def _persist(self, *, durable: bool) -> None:
        if hasattr(self._state, "updated_at"):
            self._state.updated_at = now_iso()  # type: ignore[attr-defined]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(asdict(self._state), fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            if durable:
                os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def _snapshot(self) -> T:
        cls = type(self._state)
        return cls(**asdict(self._state))

    # ---- public API ------------------------------------------------------

    @property
    def state(self) -> T:
        """Return an immutable copy of the current state."""
        with self._lock:
            return self._snapshot()

    def update(self, *, durable: bool = False, **fields: Any) -> T:
        """Apply *fields* atomically and persist.

        ``durable=True`` fsyncs the file (use for state transitions like
        begin / finish / fail). The default is False so progress updates
        do not block on HDD latency.
        """
        with self._lock:
            for key, value in fields.items():
                if not hasattr(self._state, key):
                    raise AttributeError(
                        f"{type(self._state).__name__} has no field {key!r}"
                    )
                setattr(self._state, key, value)
            self._persist(durable=durable)
            return self._snapshot()

    def append_log(self, line: str, *, durable: bool = False) -> None:
        """Append *line* to the rolling ``log_tail`` field and persist."""
        with self._lock:
            tail = list(getattr(self._state, "log_tail"))
            tail.append(f"{now_iso()} {line}")
            if len(tail) > self.LOG_TAIL_MAX:
                tail = tail[-self.LOG_TAIL_MAX:]
            self._state.log_tail = tail  # type: ignore[attr-defined]
            self._persist(durable=durable)
