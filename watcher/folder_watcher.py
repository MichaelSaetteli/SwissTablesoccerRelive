"""Watchdog-based folder watcher with event-driven quiescence detection.

Algorithm (rev 2 - was a 1-Hz polling loop, is now timer-driven):

  1. Watchdog reports any file/dir create/modify/move event under
     ``config.paths.eingang`` -> ``_on_event`` cancels the pending
     deferred check (if any) and schedules a fresh ``threading.Timer``
     for ``quiet_seconds``.
  2. When the timer fires, ``_fire_deferred_check`` runs in its own
     daemon thread (so the timer thread does not block) and calls
     ``check_and_trigger``.
  3. ``check_and_trigger`` is also the public test seam: tests can
     drive it directly without spinning up watchdog.

Compared to the previous polling-based design this watcher consumes
zero CPU when no events arrive and reacts immediately when the eingang
goes quiet - no up-to-1s polling latency.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from pipeline.config_loader import PipelineConfig

from .pipeline_runner import (
    PipelineRunError,
    detect_folders,
    run_pipeline,
)
from .status import State, StatusWriter, status_path_for

sys.stdout.reconfigure(encoding="utf-8")

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Watchdog handler (only imported here to keep test module import-light)
# ---------------------------------------------------------------------------

def _build_handler(on_event: Callable[[], None]):
    from watchdog.events import FileSystemEventHandler  # local import

    class EingangHandler(FileSystemEventHandler):
        def on_any_event(self, event):  # noqa: D401 - watchdog API
            on_event()

    return EingangHandler()


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------

TimerFactory = Callable[[float, Callable[[], None]], "threading.Timer"]


def _default_timer_factory(seconds: float, fn: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(seconds, fn)
    timer.daemon = True
    return timer


class FolderWatcher:
    """Watches one discipline's eingang and triggers the pipeline."""

    def __init__(
        self,
        config: PipelineConfig,
        quiet_seconds: float = 10.0,
        runner: Callable[[PipelineConfig], StatusWriter] = run_pipeline,
        timer_factory: TimerFactory = _default_timer_factory,
    ) -> None:
        self.config = config
        self.quiet_seconds = quiet_seconds
        self._runner = runner
        self._timer_factory = timer_factory
        self._observer = None
        self._writer = StatusWriter(
            status_path_for(config), config.discipline,
        )

        self._timer_lock = threading.Lock()
        self._pending_timer: Optional[threading.Timer] = None
        self._stopped = False

    # --- public API -------------------------------------------------------

    def start(self) -> None:
        """Start watchdog observation. No polling thread is needed."""
        from watchdog.observers import Observer

        eingang = self.config.paths.eingang
        eingang.mkdir(parents=True, exist_ok=True)

        handler = _build_handler(self._on_event)
        self._observer = Observer()
        self._observer.schedule(handler, str(eingang), recursive=True)
        self._observer.start()
        self._writer.append_log(
            f"Watcher started on {eingang} (quiet={self.quiet_seconds}s)"
        )

    def stop(self) -> None:
        with self._timer_lock:
            self._stopped = True
            if self._pending_timer is not None:
                self._pending_timer.cancel()
                self._pending_timer = None

        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
        self._writer.append_log("Watcher stopped")

    # --- event-driven scheduling -----------------------------------------

    def _on_event(self) -> None:
        """Watchdog event hook: (re)schedule a deferred quiescence check."""
        with self._timer_lock:
            if self._stopped:
                return
            if self._pending_timer is not None:
                self._pending_timer.cancel()
            self._pending_timer = self._timer_factory(
                self.quiet_seconds, self._fire_deferred_check
            )
            self._pending_timer.start()

    def _fire_deferred_check(self) -> None:
        """Timer callback: hand off to a daemon thread so the run does not
        block the timer's own thread (it gets joined on watcher.stop())."""
        with self._timer_lock:
            self._pending_timer = None
            if self._stopped:
                return
        threading.Thread(
            target=self.check_and_trigger,
            name=f"watcher-{self.config.discipline}-check",
            daemon=True,
        ).start()

    # --- core trigger logic (test seam) ----------------------------------

    def check_and_trigger(self) -> bool:
        """Run one trigger evaluation. Returns True iff the pipeline started.

        Public on purpose: tests bypass the watchdog observer + timer and
        call this directly after seeding the eingang.
        """
        folders = detect_folders(self.config.paths.eingang)
        if not folders:
            return False
        self._writer.update(state=State.DETECTING)
        try:
            self._runner(self.config)
            return True
        except PipelineRunError as exc:
            log.warning("pipeline run skipped: %s", exc)
            self._writer.append_log(f"Run skipped: {exc}")
            return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv) -> int:
    import argparse
    import logging as logging_mod
    import time

    from pipeline.config_loader import load_config

    parser = argparse.ArgumentParser(
        description="Folder watcher + pipeline trigger"
    )
    parser.add_argument("config", help="Path to config_doppel.json or config_einzel.json")
    parser.add_argument("--quiet-seconds", type=float, default=10.0)
    args = parser.parse_args(argv[1:])

    logging_mod.basicConfig(
        level=logging_mod.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    watcher = FolderWatcher(config, quiet_seconds=args.quiet_seconds)

    print(f"Watching {config.paths.eingang} for {config.discipline}")
    watcher.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        watcher.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
