"""Free-space-on-volumes watcher (Modul 6.2 im Dashboard-Spec).

A small background poller that records per-volume free/total bytes
every ``poll_interval`` seconds. The latest snapshot is exposed via
``StorageWatcher.snapshot()`` so the Web-UI can render the storage
panel without hitting ``shutil.disk_usage`` on every request.

Three thresholds (operator-configurable):
  * ``warn_gb``    - the panel highlights the volume in orange
  * ``critical_gb``- the panel shows a red banner + log entry every poll
  * ``stop_gb``    - if set, future pipeline runs refuse to start
                     (the pipeline_runner already pre-flights disk_usage,
                     this just gives a louder warning earlier)
"""

from __future__ import annotations

import logging
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pipeline.status_file import now_iso

sys.stdout.reconfigure(encoding="utf-8")
log = logging.getLogger(__name__)


@dataclass
class VolumeSnapshot:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    pct_used: float
    status: str           # 'ok' | 'warn' | 'critical'
    updated_at: str


@dataclass
class StorageSnapshot:
    volumes: List[VolumeSnapshot] = field(default_factory=list)
    overall_status: str = "ok"

    def to_dict(self) -> Dict:
        return {
            "volumes": [asdict(v) for v in self.volumes],
            "overall_status": self.overall_status,
        }


# ---------------------------------------------------------------------------
# Pure-function sampling (testable without a thread)
# ---------------------------------------------------------------------------

DEFAULT_WARN_GB = 500.0
DEFAULT_CRITICAL_GB = 200.0


def sample_volume(
    path: Path,
    *,
    warn_gb: float = DEFAULT_WARN_GB,
    critical_gb: float = DEFAULT_CRITICAL_GB,
) -> Optional[VolumeSnapshot]:
    """Return a single VolumeSnapshot for *path*, or None if missing."""
    if not Path(path).exists():
        return None
    try:
        usage = shutil.disk_usage(str(path))
    except OSError as exc:
        log.warning("disk_usage failed for %s: %s", path, exc)
        return None

    free_gb = usage.free / 1024 ** 3
    pct = (usage.used / usage.total * 100.0) if usage.total else 0.0

    if free_gb <= critical_gb:
        status = "critical"
    elif free_gb <= warn_gb:
        status = "warn"
    else:
        status = "ok"

    return VolumeSnapshot(
        path=str(path),
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        pct_used=round(pct, 1),
        status=status,
        updated_at=now_iso(),
    )


def sample_volumes(
    paths: List[Path],
    *,
    warn_gb: float = DEFAULT_WARN_GB,
    critical_gb: float = DEFAULT_CRITICAL_GB,
) -> StorageSnapshot:
    snapshots: List[VolumeSnapshot] = []
    for p in paths:
        s = sample_volume(p, warn_gb=warn_gb, critical_gb=critical_gb)
        if s is not None:
            snapshots.append(s)
    overall = "ok"
    for s in snapshots:
        if s.status == "critical":
            overall = "critical"
            break
        if s.status == "warn":
            overall = "warn"
    return StorageSnapshot(volumes=snapshots, overall_status=overall)


# ---------------------------------------------------------------------------
# Background watcher
# ---------------------------------------------------------------------------

Clock = Callable[[], float]


class StorageWatcher:
    """Periodically refreshes a StorageSnapshot in the background."""

    def __init__(
        self,
        paths: List[Path],
        *,
        poll_interval: float = 60.0,
        warn_gb: float = DEFAULT_WARN_GB,
        critical_gb: float = DEFAULT_CRITICAL_GB,
    ) -> None:
        self.paths = list(paths)
        self.poll_interval = poll_interval
        self.warn_gb = warn_gb
        self.critical_gb = critical_gb
        self._snapshot = StorageSnapshot()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def refresh_now(self) -> StorageSnapshot:
        """Take a fresh sample synchronously; tests call this directly."""
        snap = sample_volumes(
            self.paths, warn_gb=self.warn_gb, critical_gb=self.critical_gb,
        )
        with self._lock:
            self._snapshot = snap
        return snap

    def snapshot(self) -> StorageSnapshot:
        """Return the most recent cached sample (may be slightly stale)."""
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.refresh_now()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="storage-watcher", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.refresh_now()
            except Exception:                       # pragma: no cover
                log.exception("storage poll failed")
            self._stop_event.wait(self.poll_interval)
