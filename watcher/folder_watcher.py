"""Watchdog-based folder watcher with quiescence detection.

Detection algorithm:

  1. Watchdog reports any file/dir create/modify/move event under
     ``config.paths.eingang`` -> ``QuiescenceDetector.bump()``.
  2. A polling thread checks every ``poll_interval`` seconds whether the
     eingang has been quiet for at least ``quiet_seconds`` AND contains
     at least one folder. If so it triggers ``run_pipeline``.
  3. While the pipeline is running, further events still bump the
     detector but the trigger is suppressed because the per-discipline
     run lock is held by ``pipeline_runner``.

The detector is intentionally a plain Python class with no watchdog
dependency so it can be unit-tested with a fake clock.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
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
# Quiescence detector (testable in isolation)
# ---------------------------------------------------------------------------

Clock = Callable[[], float]


@dataclass
class QuiescenceDetector:
    """Tracks the time of the last event; ``is_quiet`` answers the trigger.

    Parameters
    ----------
    quiet_seconds:
        How long the eingang must be silent before we consider the upload
        complete. Briefing recommends "X seconds" - default 10.
    clock:
        Time source. Tests inject a fake.
    """
    quiet_seconds: float = 10.0
    clock: Clock = time.monotonic
    _last_event: Optional[float] = None

    def bump(self) -> None:
        self._last_event = self.clock()

    def reset(self) -> None:
        self._last_event = None

    @property
    def has_event(self) -> bool:
        return self._last_event is not None

    def is_quiet(self) -> bool:
        if self._last_event is None:
            return False
        return (self.clock() - self._last_event) >= self.quiet_seconds


# ---------------------------------------------------------------------------
# Watchdog handler (only imported here to keep test module import-light)
# ---------------------------------------------------------------------------

def _build_handler(detector: QuiescenceDetector):
    from watchdog.events import FileSystemEventHandler  # local import

    class EingangHandler(FileSystemEventHandler):
        def on_any_event(self, event):  # noqa: D401 - watchdog API
            detector.bump()

    return EingangHandler()


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------

class FolderWatcher:
    """Watches one discipline's eingang and triggers the pipeline."""

    def __init__(
        self,
        config: PipelineConfig,
        quiet_seconds: float = 10.0,
        poll_interval: float = 1.0,
        runner: Callable[[PipelineConfig], StatusWriter] = run_pipeline,
        clock: Clock = time.monotonic,
    ) -> None:
        self.config = config
        self.quiet_seconds = quiet_seconds
        self.poll_interval = poll_interval
        self._runner = runner
        self._detector = QuiescenceDetector(
            quiet_seconds=quiet_seconds, clock=clock
        )
        self._stop_event = threading.Event()
        self._observer = None
        self._poll_thread: Optional[threading.Thread] = None
        self._writer = StatusWriter(
            status_path_for(config), config.discipline
        )

    # --- public API -------------------------------------------------------

    def start(self) -> None:
        """Start watching the eingang folder and the polling thread."""
        from watchdog.observers import Observer

        eingang = self.config.paths.eingang
        eingang.mkdir(parents=True, exist_ok=True)

        handler = _build_handler(self._detector)
        self._observer = Observer()
        self._observer.schedule(handler, str(eingang), recursive=True)
        self._observer.start()

        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name=f"watcher-{self.config.discipline}-poll",
            daemon=True,
        )
        self._poll_thread.start()
        self._writer.append_log(
            f"Watcher started on {eingang} "
            f"(quiet={self.quiet_seconds}s, poll={self.poll_interval}s)"
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
        self._writer.append_log("Watcher stopped")

    # --- core poll logic (testable without watchdog) ---------------------

    def check_and_trigger(self) -> bool:
        """Run one poll iteration. Returns True iff the pipeline was started.

        Public on purpose: tests call this directly without spinning up
        the polling thread or watchdog observer.
        """
        if not self._detector.is_quiet():
            return False
        # Re-confirm there really is content (events can fire on deletes too).
        folders = detect_folders(self.config.paths.eingang)
        if not folders:
            self._detector.reset()
            return False
        self._writer.update(state=State.DETECTING)
        self._detector.reset()
        try:
            self._runner(self.config)
            return True
        except PipelineRunError as exc:
            log.warning("pipeline run skipped: %s", exc)
            self._writer.append_log(f"Run skipped: {exc}")
            return False

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_and_trigger()
            except Exception:  # pragma: no cover - defensive
                log.exception("poll iteration failed")
            self._stop_event.wait(self.poll_interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv) -> int:
    import argparse

    from pipeline.config_loader import load_config

    parser = argparse.ArgumentParser(
        description="Folder watcher + pipeline trigger"
    )
    parser.add_argument("config", help="Path to config_doppel.json or config_einzel.json")
    parser.add_argument("--quiet-seconds", type=float, default=10.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args(argv[1:])

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    watcher = FolderWatcher(
        config,
        quiet_seconds=args.quiet_seconds,
        poll_interval=args.poll_interval,
    )

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
