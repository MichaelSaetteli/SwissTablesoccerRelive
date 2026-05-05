"""Tests for watcher.folder_watcher.

We do not spin up watchdog observers here; instead we drive the
``QuiescenceDetector`` and ``FolderWatcher.check_and_trigger`` directly
with a fake clock and a stub runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from pipeline.config_loader import load_config
from tests.conftest import make_mp4
from watcher.folder_watcher import FolderWatcher, QuiescenceDetector
from watcher.status import State, StatusWriter, status_path_for


# ---------------------------------------------------------------------------
# Fake clock
# ---------------------------------------------------------------------------

class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# QuiescenceDetector
# ---------------------------------------------------------------------------

def test_quiescence_starts_not_quiet() -> None:
    clock = FakeClock()
    det = QuiescenceDetector(quiet_seconds=10.0, clock=clock)
    assert det.is_quiet() is False
    assert det.has_event is False


def test_quiescence_detects_after_window() -> None:
    clock = FakeClock()
    det = QuiescenceDetector(quiet_seconds=10.0, clock=clock)

    det.bump()
    assert det.has_event is True
    assert det.is_quiet() is False  # no time passed yet

    clock.advance(5.0)
    assert det.is_quiet() is False

    clock.advance(5.0)  # total 10s
    assert det.is_quiet() is True


def test_quiescence_resets_on_new_event() -> None:
    clock = FakeClock()
    det = QuiescenceDetector(quiet_seconds=10.0, clock=clock)

    det.bump()
    clock.advance(8.0)
    det.bump()  # restart the timer
    clock.advance(5.0)
    assert det.is_quiet() is False  # only 5s since last event

    clock.advance(5.0)
    assert det.is_quiet() is True


def test_quiescence_reset_clears_event() -> None:
    clock = FakeClock()
    det = QuiescenceDetector(quiet_seconds=10.0, clock=clock)
    det.bump()
    clock.advance(20.0)
    assert det.is_quiet() is True
    det.reset()
    assert det.has_event is False
    assert det.is_quiet() is False


# ---------------------------------------------------------------------------
# FolderWatcher.check_and_trigger (no real watchdog/observer)
# ---------------------------------------------------------------------------

class StubRunner:
    """Records every call. Mimics pipeline_runner.run_pipeline signature."""

    def __init__(self, raise_exc: Exception | None = None) -> None:
        self.calls: List[str] = []
        self.raise_exc = raise_exc

    def __call__(self, config):
        self.calls.append(config.discipline)
        if self.raise_exc is not None:
            raise self.raise_exc
        return StatusWriter(status_path_for(config), config.discipline)


def _build_watcher(config_path: Path, *, quiet: float, runner: StubRunner) -> tuple:
    cfg = load_config(config_path)
    clock = FakeClock()
    watcher = FolderWatcher(
        cfg, quiet_seconds=quiet, poll_interval=0.01,
        runner=runner, clock=clock,
    )
    return watcher, clock, cfg


def test_check_and_trigger_skips_when_no_events(doppel_config_path: Path) -> None:
    runner = StubRunner()
    watcher, clock, cfg = _build_watcher(doppel_config_path, quiet=10.0, runner=runner)

    cfg.paths.eingang.mkdir(parents=True)
    (cfg.paths.eingang / "ET03").mkdir()  # folder exists but no event

    fired = watcher.check_and_trigger()
    assert fired is False
    assert runner.calls == []


def test_check_and_trigger_fires_after_quiet_window(
    doppel_config_path: Path,
) -> None:
    runner = StubRunner()
    watcher, clock, cfg = _build_watcher(doppel_config_path, quiet=10.0, runner=runner)

    cfg.paths.eingang.mkdir(parents=True)
    (cfg.paths.eingang / "ET03").mkdir()
    make_mp4(cfg.paths.eingang / "ET03", "video.mp4")

    watcher._detector.bump()
    clock.advance(5.0)
    assert watcher.check_and_trigger() is False  # not quiet yet
    assert runner.calls == []

    clock.advance(5.0)  # total 10s
    assert watcher.check_and_trigger() is True
    assert runner.calls == ["Doppel"]


def test_check_and_trigger_skips_when_eingang_empty(
    doppel_config_path: Path,
) -> None:
    """Quiet but no folders -> no run, detector reset to avoid infinite loop."""
    runner = StubRunner()
    watcher, clock, cfg = _build_watcher(doppel_config_path, quiet=5.0, runner=runner)

    cfg.paths.eingang.mkdir(parents=True)  # empty
    watcher._detector.bump()
    clock.advance(10.0)

    assert watcher.check_and_trigger() is False
    assert runner.calls == []
    # Detector must have been reset so we don't keep firing
    assert watcher._detector.has_event is False


def test_check_and_trigger_swallows_run_error(
    doppel_config_path: Path,
) -> None:
    """A pipeline run error must not crash the watcher loop."""
    from watcher.pipeline_runner import PipelineRunError

    runner = StubRunner(raise_exc=PipelineRunError("already running"))
    watcher, clock, cfg = _build_watcher(doppel_config_path, quiet=5.0, runner=runner)

    cfg.paths.eingang.mkdir(parents=True)
    (cfg.paths.eingang / "ET03").mkdir()
    make_mp4(cfg.paths.eingang / "ET03", "v.mp4")

    watcher._detector.bump()
    clock.advance(10.0)

    fired = watcher.check_and_trigger()
    assert fired is False  # raised + swallowed -> not a successful trigger
    assert runner.calls == ["Doppel"]
    # Status file gets a "Run skipped" log entry
    s = watcher._writer.status
    assert any("skipped" in line.lower() for line in s.log_tail)
