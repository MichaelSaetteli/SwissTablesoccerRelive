"""Atomic JSON status persistence for the YouTube upload phase.

Same pattern as ``watcher.status``, just a different dataclass. See
``pipeline.status_file`` for the shared base.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.config_loader import PipelineConfig
from pipeline.status_file import JsonStatusFile, now_iso

sys.stdout.reconfigure(encoding="utf-8")


LOG_TAIL_MAX = JsonStatusFile.LOG_TAIL_MAX


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
# Path helper
# ---------------------------------------------------------------------------

def upload_status_path_for(config: PipelineConfig) -> Path:
    if config.source_path is None:
        raise ValueError(
            "config.source_path is None - cannot derive upload status path"
        )
    return (
        config.source_path.parent
        / f"upload_status_{config.discipline.lower()}.json"
    )


# ---------------------------------------------------------------------------
# Standalone read/write helpers (used by tests + Web read-only views)
# ---------------------------------------------------------------------------

def write_upload_status(path: Path, status: UploadStatus) -> None:
    import json
    import os

    status.updated_at = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(status.to_dict(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_upload_status(path: Path) -> Optional[UploadStatus]:
    import json

    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return UploadStatus(**data)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class UploadStatusWriter(JsonStatusFile[UploadStatus]):
    """Thread-safe ``UploadStatus`` persistence with semantic transitions."""

    def __init__(self, path: Path, discipline: str) -> None:
        super().__init__(path, lambda: UploadStatus(discipline=discipline))

    @property
    def status(self) -> UploadStatus:
        return self.state

    # ---- transitions (durable) ------------------------------------------

    def begin(self, total_files: int, quota_hint: str) -> None:
        self.update(
            durable=True,
            state=UploadState.PREPARING,
            total_files=total_files,
            completed_files=0,
            current_file="",
            current_progress_percent=0.0,
            uploaded_video_ids=[],
            playlist_id="",
            quota_hint=quota_hint,
            started_at=now_iso(),
            finished_at=None,
            error=None,
        )

    def begin_file(self, file: str) -> None:
        self.update(
            durable=False,  # progress milestone, not terminal
            state=UploadState.UPLOADING,
            current_file=file,
            current_progress_percent=0.0,
        )

    def finish(self) -> None:
        self.update(
            durable=True,
            state=UploadState.DONE,
            current_file="",
            current_progress_percent=0.0,
            finished_at=now_iso(),
            error=None,
        )

    def fail(self, error: str) -> None:
        self.update(
            durable=True,
            state=UploadState.ERROR,
            error=error,
            finished_at=now_iso(),
        )

    # ---- progress (non-durable) -----------------------------------------

    def update_progress(self, percent: float) -> None:
        self.update(
            durable=False,
            current_progress_percent=max(0.0, min(100.0, percent)),
        )

    def finish_file(self, video_id: str) -> None:
        with self._lock:
            self._state.completed_files += 1
            self._state.current_progress_percent = 100.0
            self._state.uploaded_video_ids = list(
                self._state.uploaded_video_ids
            ) + [video_id]
            self._persist(durable=False)
