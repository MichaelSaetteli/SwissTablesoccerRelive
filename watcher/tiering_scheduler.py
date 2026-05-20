"""Idle-gated tiering scheduler (Dashboard M3 / Block A).

A small background poller that, while the whole system is idle:

  * runs the retention sweep at most once per calendar day, and
  * auto-stages a discipline's work_/output_ dirs to the HDD when that
    discipline has ``tiering.auto_enabled`` set and its YouTube upload has
    just completed (so M2 restart-from-phase still works during the
    processing window, which needs the work_ files on the SSD).

The scheduler is deliberately decoupled from the web + youtube layers:
the actions it performs are injected as callables, so the watcher package
keeps importing neither. ``tick`` holds all the decision logic and is
called directly by tests; the thread loop just calls ``tick`` on an
interval.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import date
from typing import Callable, Dict, Optional

sys.stdout.reconfigure(encoding="utf-8")
log = logging.getLogger(__name__)


# Injected action signatures (all keyed by a PipelineConfig-like object).
IdleFn = Callable[[Dict[str, object]], bool]
SweepFn = Callable[[object], object]
StageFn = Callable[[object], object]
# Returns (upload_state, upload_finished_at) for a config.
UploadInfoFn = Callable[[object], "tuple"]
DayFn = Callable[[], str]
Clock = Callable[[], float]

_UPLOAD_DONE = "done"


class TieringScheduler:
    def __init__(
        self,
        configs: Dict[str, object],
        *,
        idle_fn: IdleFn,
        sweep_fn: SweepFn,
        stage_fn: StageFn,
        upload_info_fn: UploadInfoFn,
        poll_interval: float = 1800.0,
        day_fn: Optional[DayFn] = None,
    ) -> None:
        self._configs = configs
        self._idle_fn = idle_fn
        self._sweep_fn = sweep_fn
        self._stage_fn = stage_fn
        self._upload_info_fn = upload_info_fn
        self.poll_interval = poll_interval
        self._day_fn = day_fn or (lambda: date.today().isoformat())

        self._last_sweep_day: Optional[str] = None
        # Per-discipline last upload finished_at we have already staged for,
        # so a completed upload triggers auto-staging exactly once.
        self._staged_for: Dict[str, Optional[str]] = {}

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self) -> None:
        """One scheduling pass. No-op unless the whole system is idle."""
        if not self._idle_fn(self._configs):
            return

        # ---- Auto-staging: once per completed upload, opt-in per discipline.
        for name, cfg in self._configs.items():
            if not getattr(cfg, "tiering_auto_enabled", False):
                continue
            if getattr(cfg, "tiering_staging_root", None) is None:
                continue
            state, finished_at = self._upload_info_fn(cfg)
            if state != _UPLOAD_DONE:
                continue
            if self._staged_for.get(name) == finished_at:
                continue
            try:
                self._stage_fn(cfg)
            except Exception:                          # pragma: no cover
                log.exception("auto-stage failed for %s", name)
            self._staged_for[name] = finished_at

        # ---- Retention sweep: at most once per calendar day.
        today = self._day_fn()
        if today != self._last_sweep_day:
            for cfg in self._configs.values():
                if getattr(cfg, "tiering_staging_root", None) is None:
                    continue
                try:
                    self._sweep_fn(cfg)
                except Exception:                      # pragma: no cover
                    log.exception("retention sweep failed")
            self._last_sweep_day = today

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="tiering-scheduler", daemon=True,
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
                self.tick()
            except Exception:                          # pragma: no cover
                log.exception("tiering scheduler tick failed")
            self._stop_event.wait(self.poll_interval)
