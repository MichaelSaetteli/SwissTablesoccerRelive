"""Tests for watcher.tiering_scheduler (idle-gated tick logic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from watcher.tiering_scheduler import TieringScheduler


@dataclass
class FakeConfig:
    discipline: str
    tiering_auto_enabled: bool = False
    tiering_staging_root: Optional[str] = "/hdd/staging"


class Harness:
    """Records sweep/stage calls and lets the test drive idle + upload state."""

    def __init__(self, idle=True):
        self.idle = idle
        self.upload = {}                  # discipline -> (state, finished_at)
        self.swept: List[str] = []
        self.staged: List[str] = []

    def idle_fn(self, configs) -> bool:
        return self.idle

    def sweep_fn(self, cfg):
        self.swept.append(cfg.discipline)

    def stage_fn(self, cfg):
        self.staged.append(cfg.discipline)

    def upload_info_fn(self, cfg) -> Tuple[str, Optional[str]]:
        return self.upload.get(cfg.discipline, ("idle", None))


def _scheduler(harness, configs, *, day="2026-05-20"):
    return TieringScheduler(
        configs,
        idle_fn=harness.idle_fn,
        sweep_fn=harness.sweep_fn,
        stage_fn=harness.stage_fn,
        upload_info_fn=harness.upload_info_fn,
        day_fn=lambda: day,
    )


def test_no_action_when_busy():
    h = Harness(idle=False)
    cfg = FakeConfig("Doppel", tiering_auto_enabled=True)
    h.upload["Doppel"] = ("done", "t1")
    s = _scheduler(h, {"Doppel": cfg})
    s.tick()
    assert h.swept == []
    assert h.staged == []


def test_daily_sweep_runs_once_per_day():
    h = Harness(idle=True)
    cfg = FakeConfig("Doppel")
    s = _scheduler(h, {"Doppel": cfg}, day="2026-05-20")
    s.tick()
    s.tick()  # same day -> no second sweep
    assert h.swept == ["Doppel"]


def test_sweep_runs_again_on_new_day():
    h = Harness(idle=True)
    cfg = FakeConfig("Doppel")
    s = TieringScheduler(
        {"Doppel": cfg},
        idle_fn=h.idle_fn, sweep_fn=h.sweep_fn, stage_fn=h.stage_fn,
        upload_info_fn=h.upload_info_fn,
        day_fn=lambda: Harness._day,  # mutated below
    )
    Harness._day = "2026-05-20"
    s.tick()
    Harness._day = "2026-05-21"
    s.tick()
    assert h.swept == ["Doppel", "Doppel"]


def test_autostage_only_when_enabled():
    h = Harness(idle=True)
    off = FakeConfig("Doppel", tiering_auto_enabled=False)
    h.upload["Doppel"] = ("done", "t1")
    s = _scheduler(h, {"Doppel": off})
    s.tick()
    assert h.staged == []


def test_autostage_fires_once_per_completed_upload():
    h = Harness(idle=True)
    cfg = FakeConfig("Einzel", tiering_auto_enabled=True)
    h.upload["Einzel"] = ("done", "finish-1")
    s = _scheduler(h, {"Einzel": cfg})

    s.tick()
    s.tick()  # same upload completion -> no re-stage
    assert h.staged == ["Einzel"]

    # a new upload completion (different finished_at) triggers staging again
    h.upload["Einzel"] = ("done", "finish-2")
    s.tick()
    assert h.staged == ["Einzel", "Einzel"]


def test_autostage_skips_when_upload_not_done():
    h = Harness(idle=True)
    cfg = FakeConfig("Doppel", tiering_auto_enabled=True)
    h.upload["Doppel"] = ("uploading", None)
    s = _scheduler(h, {"Doppel": cfg})
    s.tick()
    assert h.staged == []


def test_autostage_skips_without_staging_root():
    h = Harness(idle=True)
    cfg = FakeConfig("Doppel", tiering_auto_enabled=True, tiering_staging_root=None)
    h.upload["Doppel"] = ("done", "t1")
    s = _scheduler(h, {"Doppel": cfg})
    s.tick()
    assert h.staged == []
