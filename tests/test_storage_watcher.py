"""Tests for watcher.storage_watcher."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from watcher.storage_watcher import (
    DEFAULT_CRITICAL_GB,
    DEFAULT_WARN_GB,
    StorageWatcher,
    sample_volume,
    sample_volumes,
)


class FakeUsage(NamedTuple):
    total: int
    used: int
    free: int


GB = 1024 ** 3


@pytest.fixture
def patch_disk_usage(monkeypatch):
    """Inject ``shutil.disk_usage`` responses keyed by path."""
    responses = {}

    def fake_disk_usage(path):
        if str(path) not in responses:
            raise OSError(f"unmocked path: {path}")
        return responses[str(path)]

    monkeypatch.setattr("watcher.storage_watcher.shutil.disk_usage", fake_disk_usage)
    return responses


def test_sample_volume_returns_none_for_missing_dir(tmp_path: Path) -> None:
    assert sample_volume(tmp_path / "missing") is None


def test_sample_volume_status_thresholds(tmp_path: Path, patch_disk_usage) -> None:
    patch_disk_usage[str(tmp_path)] = FakeUsage(
        total=1000 * GB, used=500 * GB, free=600 * GB,
    )
    s = sample_volume(tmp_path, warn_gb=500, critical_gb=200)
    assert s.status == "ok"

    patch_disk_usage[str(tmp_path)] = FakeUsage(
        total=1000 * GB, used=600 * GB, free=400 * GB,
    )
    s = sample_volume(tmp_path, warn_gb=500, critical_gb=200)
    assert s.status == "warn"

    patch_disk_usage[str(tmp_path)] = FakeUsage(
        total=1000 * GB, used=900 * GB, free=100 * GB,
    )
    s = sample_volume(tmp_path, warn_gb=500, critical_gb=200)
    assert s.status == "critical"


def test_sample_volumes_aggregates_overall_status(tmp_path: Path, patch_disk_usage) -> None:
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    patch_disk_usage[str(a)] = FakeUsage(total=10 * GB, used=2 * GB, free=8 * GB)
    patch_disk_usage[str(b)] = FakeUsage(total=10 * GB, used=8 * GB, free=2 * GB)

    snap = sample_volumes([a, b], warn_gb=5, critical_gb=1)
    assert len(snap.volumes) == 2
    assert snap.overall_status == "warn"


def test_sample_volumes_critical_dominates(tmp_path: Path, patch_disk_usage) -> None:
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    patch_disk_usage[str(a)] = FakeUsage(total=10 * GB, used=9 * GB, free=1 * GB)
    patch_disk_usage[str(b)] = FakeUsage(total=10 * GB, used=2 * GB, free=8 * GB)

    snap = sample_volumes([a, b], warn_gb=5, critical_gb=2)
    assert snap.overall_status == "critical"


def test_storage_watcher_caches_snapshot(tmp_path: Path, patch_disk_usage) -> None:
    a = tmp_path / "a"; a.mkdir()
    patch_disk_usage[str(a)] = FakeUsage(total=GB, used=0, free=GB)

    w = StorageWatcher([a], poll_interval=999.0)
    s = w.refresh_now()
    assert s is w.snapshot()
    assert len(w.snapshot().volumes) == 1


def test_storage_watcher_skips_missing_paths(tmp_path: Path, patch_disk_usage) -> None:
    exists = tmp_path / "exists"; exists.mkdir()
    patch_disk_usage[str(exists)] = FakeUsage(total=GB, used=0, free=GB)
    w = StorageWatcher([exists, tmp_path / "ghost"])
    snap = w.refresh_now()
    assert len(snap.volumes) == 1
