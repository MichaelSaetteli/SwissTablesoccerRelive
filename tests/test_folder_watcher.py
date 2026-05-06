"""Tests for watcher.folder_watcher (event-driven rev).

We never spin up a real watchdog observer here. The two seams under
test are:

  * ``check_and_trigger`` - the public method that decides whether to
    invoke the runner, given the current eingang contents.
  * ``_on_event`` - the watchdog event hook, exercised through a
    ``FakeTimer`` so we can verify scheduling/rescheduling without
    actually waiting for ``quiet_seconds`` to pass.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, List

from pipeline.config_loader import load_config
from tests.conftest import make_mp4
from watcher.folder_watcher import FolderWatcher
from watcher.status import StatusWriter, status_path_for


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeTimer:
    """Drop-in for ``threading.Timer`` that does not actually wait.

    Tests call ``fire()`` to simulate the timer expiring, so the assert
    can run synchronously without sleeping ``quiet_seconds``.
    """

    instances: List["FakeTimer"] = []

    def __init__(self, seconds: float, fn: Callable[[], None]) -> None:
        self.seconds = seconds
        self.fn = fn
        self.cancelled = False
        self.started = False
        self.daemon = True
        FakeTimer.instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.fn()


def _fake_factory(seconds: float, fn: Callable[[], None]) -> FakeTimer:
    return FakeTimer(seconds, fn)


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


def _build_watcher(config_path: Path, *, runner: StubRunner) -> tuple:
    cfg = load_config(config_path)
    FakeTimer.instances = []
    watcher = FolderWatcher(
        cfg,
        quiet_seconds=10.0,
        runner=runner,
        timer_factory=_fake_factory,
    )
    return watcher, cfg


# ---------------------------------------------------------------------------
# check_and_trigger
# ---------------------------------------------------------------------------

def test_check_and_trigger_skips_when_eingang_empty(
    doppel_config_path: Path,
) -> None:
    runner = StubRunner()
    watcher, cfg = _build_watcher(doppel_config_path, runner=runner)
    cfg.paths.eingang.mkdir(parents=True)

    assert watcher.check_and_trigger() is False
    assert runner.calls == []


def test_check_and_trigger_fires_when_folders_present(
    doppel_config_path: Path,
) -> None:
    runner = StubRunner()
    watcher, cfg = _build_watcher(doppel_config_path, runner=runner)
    cfg.paths.eingang.mkdir(parents=True)
    (cfg.paths.eingang / "ET03").mkdir()
    make_mp4(cfg.paths.eingang / "ET03", "video.mp4")

    assert watcher.check_and_trigger() is True
    assert runner.calls == ["Doppel"]


def test_check_and_trigger_swallows_run_error(doppel_config_path: Path) -> None:
    """Run errors must not crash the watcher loop."""
    from watcher.pipeline_runner import PipelineRunError

    runner = StubRunner(raise_exc=PipelineRunError("already running"))
    watcher, cfg = _build_watcher(doppel_config_path, runner=runner)
    cfg.paths.eingang.mkdir(parents=True)
    (cfg.paths.eingang / "ET03").mkdir()
    make_mp4(cfg.paths.eingang / "ET03", "v.mp4")

    fired = watcher.check_and_trigger()
    assert fired is False  # raised + swallowed
    assert runner.calls == ["Doppel"]
    assert any("skipped" in line.lower() for line in watcher._writer.status.log_tail)


# ---------------------------------------------------------------------------
# _on_event scheduling
# ---------------------------------------------------------------------------

def test_on_event_schedules_timer(doppel_config_path: Path) -> None:
    runner = StubRunner()
    watcher, cfg = _build_watcher(doppel_config_path, runner=runner)
    cfg.paths.eingang.mkdir(parents=True)

    watcher._on_event()
    timers = FakeTimer.instances
    assert len(timers) == 1
    assert timers[0].seconds == 10.0
    assert timers[0].started is True
    assert timers[0].cancelled is False


def test_on_event_cancels_pending_timer_and_reschedules(
    doppel_config_path: Path,
) -> None:
    """A second event must cancel the in-flight timer and start a new one."""
    runner = StubRunner()
    watcher, cfg = _build_watcher(doppel_config_path, runner=runner)
    cfg.paths.eingang.mkdir(parents=True)

    watcher._on_event()
    watcher._on_event()
    watcher._on_event()

    assert len(FakeTimer.instances) == 3
    # The first two were cancelled, the third is the live one.
    assert FakeTimer.instances[0].cancelled is True
    assert FakeTimer.instances[1].cancelled is True
    assert FakeTimer.instances[2].cancelled is False


def test_timer_fire_triggers_pipeline(doppel_config_path: Path) -> None:
    """When the timer expires it must (eventually) invoke the runner."""
    runner = StubRunner()
    watcher, cfg = _build_watcher(doppel_config_path, runner=runner)
    cfg.paths.eingang.mkdir(parents=True)
    (cfg.paths.eingang / "ET03").mkdir()
    make_mp4(cfg.paths.eingang / "ET03", "v.mp4")

    watcher._on_event()
    # FakeTimer.fire() calls _fire_deferred_check, which spawns a daemon
    # thread. Wait briefly for it to complete.
    FakeTimer.instances[-1].fire()
    for _ in range(40):
        if runner.calls:
            break
        threading.Event().wait(0.05)
    assert runner.calls == ["Doppel"]


def test_stop_cancels_pending_timer(doppel_config_path: Path) -> None:
    runner = StubRunner()
    watcher, cfg = _build_watcher(doppel_config_path, runner=runner)
    cfg.paths.eingang.mkdir(parents=True)

    watcher._on_event()
    pending = FakeTimer.instances[-1]
    assert pending.cancelled is False

    # stop() with no observer set is fine (we never started watchdog).
    watcher.stop()
    assert pending.cancelled is True
    # Subsequent events are ignored.
    watcher._on_event()
    assert len(FakeTimer.instances) == 1
