"""Atomic JSON status persistence for the YouTube upload phase.

Mirrors ``watcher.status`` so the Web-Interface polls the upload state
the same way it polls the pipeline state. Lives next to
``status_<discipline>.json`` in the config dir.
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

class UploadState:
    IDLE = "idle"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    DONE = "done"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class UploadStatus:
    """Snapshot of one discipline's YouTube upload run."""
    discipline: str
    state: str = UploadState.IDLE
    total_files: int = 0
    completed_files: int = 0
    current_file: str = ""
    current_progress_percent: float = 0.0
    uploaded_video_ids: List[str] = field(default_factory=list)
    playlist_id: str = ""
    quota_hint: str = ""
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

LOG_TAIL_MAX = 200


def upload_status_path_for(config: PipelineConfig) -> Path:
    if config.source_path is None:
        raise ValueError(
            "config.source_path is None - cannot derive upload status path"
        )
    return (
        config.source_path.parent
        / f"upload_status_{config.discipline.lower()}.json"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_upload_status(path: Path, status: UploadStatus) -> None:
    status.updated_at = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(status.to_dict(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_upload_status(path: Path) -> Optional[UploadStatus]:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return UploadStatus(**data)


# ---------------------------------------------------------------------------
# Mutator
# ---------------------------------------------------------------------------

class UploadStatusWriter:
    """Thread-safe writer mirroring watcher.status.StatusWriter."""

    def __init__(self, path: Path, discipline: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        existing = read_upload_status(path)
        self._status = existing or UploadStatus(discipline=discipline)
        if existing is None:
            write_upload_status(self.path, self._status)

    @property
    def status(self) -> UploadStatus:
        with self._lock:
            return UploadStatus(**self._status.to_dict())

    def update(self, **fields: Any) -> UploadStatus:
        with self._lock:
            for key, value in fields.items():
                if not hasattr(self._status, key):
                    raise AttributeError(
                        f"UploadStatus has no field {key!r}"
                    )
                setattr(self._status, key, value)
            write_upload_status(self.path, self._status)
            return UploadStatus(**self._status.to_dict())

    def append_log(self, line: str) -> None:
        with self._lock:
            self._status.log_tail.append(f"{_now_iso()} {line}")
            if len(self._status.log_tail) > LOG_TAIL_MAX:
                self._status.log_tail = self._status.log_tail[-LOG_TAIL_MAX:]
            write_upload_status(self.path, self._status)

    def begin(self, total_files: int, quota_hint: str) -> None:
        with self._lock:
            self._status.state = UploadState.PREPARING
            self._status.total_files = total_files
            self._status.completed_files = 0
            self._status.current_file = ""
            self._status.current_progress_percent = 0.0
            self._status.uploaded_video_ids = []
            self._status.playlist_id = ""
            self._status.quota_hint = quota_hint
            self._status.started_at = _now_iso()
            self._status.finished_at = None
            self._status.error = None
            write_upload_status(self.path, self._status)

    def begin_file(self, file: str) -> None:
        with self._lock:
            self._status.state = UploadState.UPLOADING
            self._status.current_file = file
            self._status.current_progress_percent = 0.0
            write_upload_status(self.path, self._status)

    def update_progress(self, percent: float) -> None:
        with self._lock:
            self._status.current_progress_percent = max(0.0, min(100.0, percent))
            write_upload_status(self.path, self._status)

    def finish_file(self, video_id: str) -> None:
        with self._lock:
            self._status.completed_files += 1
            self._status.current_progress_percent = 100.0
            self._status.uploaded_video_ids.append(video_id)
            write_upload_status(self.path, self._status)

    def finish(self) -> None:
        with self._lock:
            self._status.state = UploadState.DONE
            self._status.current_file = ""
            self._status.current_progress_percent = 0.0
            self._status.finished_at = _now_iso()
            self._status.error = None
            write_upload_status(self.path, self._status)

    def fail(self, error: str) -> None:
        with self._lock:
            self._status.state = UploadState.ERROR
            self._status.error = error
            self._status.finished_at = _now_iso()
            write_upload_status(self.path, self._status)
