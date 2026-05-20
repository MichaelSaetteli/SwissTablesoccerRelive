"""Pipeline-status persistence (``status_doppel.json`` / ``status_einzel.json``).

Each discipline owns one status file managed by ``StatusWriter``, a thin
subclass of ``pipeline.status_file.JsonStatusFile``. Terminal transitions
(begin / finish / fail) flush durably; progress updates do not.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.config_loader import PipelineConfig
from pipeline.status_file import JsonStatusFile, now_iso

sys.stdout.reconfigure(encoding="utf-8")


# Re-exported so existing imports keep working.
LOG_TAIL_MAX = JsonStatusFile.LOG_TAIL_MAX


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
    """Snapshot of one discipline pipeline."""
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
# Path helper
# ---------------------------------------------------------------------------

def status_path_for(config: PipelineConfig) -> Path:
    if config.source_path is None:
        raise ValueError("config.source_path is None - cannot derive status path")
    return config.source_path.parent / f"status_{config.discipline.lower()}.json"


# ---------------------------------------------------------------------------
# Standalone read/write helpers (used by tests + Web read-only views)
# ---------------------------------------------------------------------------

def write_status(path: Path, status: PipelineStatus) -> None:
    """Atomically persist *status*. Always durable (used by tests)."""
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


def read_status(path: Path) -> Optional[PipelineStatus]:
    import json

    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return PipelineStatus(**data)


# ---------------------------------------------------------------------------
# Writer (thread-safe)
# ---------------------------------------------------------------------------

class StatusWriter(JsonStatusFile[PipelineStatus]):
    """Thread-safe ``PipelineStatus`` persistence with semantic transitions."""

    def __init__(self, path: Path, discipline: str) -> None:
        super().__init__(path, lambda: PipelineStatus(discipline=discipline))

    @property
    def status(self) -> PipelineStatus:
        return self.state

    # ---- transitions (durable) ------------------------------------------

    def begin_run(self, folders: List[str]) -> None:
        self.update(
            durable=True,
            state=State.MOVING,
            folders_detected=list(folders),
            folders_processed=[],
            output_files=[],
            started_at=now_iso(),
            finished_at=None,
            error=None,
        )

    def finish_run(self, output_files: List[str]) -> None:
        self.update(
            durable=True,
            state=State.DONE,
            output_files=list(output_files),
            finished_at=now_iso(),
            error=None,
        )

    def fail_run(self, error: str) -> None:
        self.update(
            durable=True,
            state=State.ERROR,
            error=error,
            finished_at=now_iso(),
        )
