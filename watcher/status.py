"""Pipeline-status persistence (``status_doppel.json`` / ``status_einzel.json``).

Each discipline owns one status file. The file is written atomically
(temp file + ``os.replace``) so the Web-Interface (Step 3) can read it
concurrently without ever seeing a half-written JSON document.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.config_loader import PipelineConfig

sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class State:
    """All possible values for ``PipelineStatus.state``."""
    IDLE = "idle"
    DETECTING = "detecting"
    MOVING = "moving"
    ORGANIZING = "organizing"
    RENAMING = "renaming"
    MERGING = "merging"
    DONE = "done"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class PipelineStatus:
    """Snapshot of one discipline pipeline. Serialised to JSON on every change."""
    discipline: str
    state: str = State.IDLE
    folders_detected: List[str] = field(default_factory=list)
    folders_processed: List[str] = field(default_factory=list)
    output_files: List[str] = field(default_factory=list)
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    log_tail: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

LOG_TAIL_MAX = 200  # keep last N lines for the Web UI


def status_path_for(config: PipelineConfig) -> Path:
    """Return the status file path for *config*'s discipline.

    Sits next to the config file so all per-discipline state is grouped.
    """
    if config.source_path is None:
        raise ValueError("config.source_path is None - cannot derive status path")
    return config.source_path.parent / f"status_{config.discipline.lower()}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_status(path: Path, status: PipelineStatus) -> None:
    """Atomically persist *status* to *path* (temp file + replace)."""
    status.updated_at = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(status.to_dict(), fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_status(path: Path) -> Optional[PipelineStatus]:
    """Load status from *path*, or ``None`` if the file does not exist yet."""
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return PipelineStatus(**data)


# ---------------------------------------------------------------------------
# Mutator (thread-safe per instance)
# ---------------------------------------------------------------------------

class StatusWriter:
    """Thread-safe mutator that owns a status file for one discipline.

    Multiple threads may call ``update`` concurrently; the lock guarantees
    that no two writers race on the temp-file rename.
    """

    def __init__(self, path: Path, discipline: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        existing = read_status(path)
        self._status = existing or PipelineStatus(discipline=discipline)
        if existing is None:
            write_status(self.path, self._status)

    @property
    def status(self) -> PipelineStatus:
        with self._lock:
            # Return a shallow copy so callers cannot mutate without going
            # through ``update``.
            return PipelineStatus(**self._status.to_dict())

    def update(self, **fields: Any) -> PipelineStatus:
        """Apply *fields* and persist."""
        with self._lock:
            for key, value in fields.items():
                if not hasattr(self._status, key):
                    raise AttributeError(
                        f"PipelineStatus has no field {key!r}"
                    )
                setattr(self._status, key, value)
            write_status(self.path, self._status)
            return PipelineStatus(**self._status.to_dict())

    def append_log(self, line: str) -> None:
        """Add *line* to the rolling log tail and persist."""
        with self._lock:
            self._status.log_tail.append(f"{_now_iso()} {line}")
            if len(self._status.log_tail) > LOG_TAIL_MAX:
                self._status.log_tail = self._status.log_tail[-LOG_TAIL_MAX:]
            write_status(self.path, self._status)

    def begin_run(self, folders: List[str]) -> None:
        with self._lock:
            self._status.state = State.MOVING
            self._status.folders_detected = list(folders)
            self._status.folders_processed = []
            self._status.output_files = []
            self._status.started_at = _now_iso()
            self._status.finished_at = None
            self._status.error = None
            write_status(self.path, self._status)

    def finish_run(self, output_files: List[str]) -> None:
        with self._lock:
            self._status.state = State.DONE
            self._status.output_files = list(output_files)
            self._status.finished_at = _now_iso()
            self._status.error = None
            write_status(self.path, self._status)

    def fail_run(self, error: str) -> None:
        with self._lock:
            self._status.state = State.ERROR
            self._status.error = error
            self._status.finished_at = _now_iso()
            write_status(self.path, self._status)
