"""Tests for the tiering service layer (web.services / M3 Block A)."""

from __future__ import annotations

import json

import pytest

from db.schema import _reset_connections_for_tests
from pipeline.config_loader import load_config
from watcher.status import State, StatusWriter, status_path_for
from web import services


@pytest.fixture(autouse=True)
def _isolated():
    _reset_connections_for_tests()
    services._reset_tiering_status_for_tests()
    yield
    _reset_connections_for_tests()
    services._reset_tiering_status_for_tests()


def _make_config(tmp_path, *, staging_root=None, retention_days=7):
    tiering = {}
    if staging_root is not None:
        tiering["staging_root"] = str(staging_root)
        tiering["retention_days"] = retention_days
    cfg = {
        "discipline": "Doppel",
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / "eingang"),
            "work":    str(tmp_path / "work"),
            "output":  str(tmp_path / "output"),
            "logs":    str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026", "sts_nummer": "STS2",
            "turniername": "T", "disziplin": "Doppel", "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
        "tiering": tiering,
    }
    p = tmp_path / "config_doppel.json"
    p.write_text(json.dumps(cfg))
    return load_config(p)


def _write(path, name, data):
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_bytes(data)


def test_busy_when_pipeline_merging(tmp_path):
    cfg = _make_config(tmp_path)
    writer = StatusWriter(status_path_for(cfg), cfg.discipline)
    writer.update(state=State.MERGING)
    assert services.is_discipline_busy(cfg) is True
    assert services.is_system_idle({"Doppel": cfg}) is False


def test_idle_when_pipeline_idle(tmp_path):
    cfg = _make_config(tmp_path)
    assert services.is_discipline_busy(cfg) is False
    assert services.is_system_idle({"Doppel": cfg}) is True


def test_tier_refuses_without_staging_root(tmp_path):
    cfg = _make_config(tmp_path, staging_root=None)
    out = services.tier_discipline(cfg)
    assert out["ok"] is False
    assert "staging_root" in out["error"]


def test_tier_moves_work_and_output(tmp_path):
    staging = tmp_path / "hdd" / "staging"
    cfg = _make_config(tmp_path, staging_root=staging)
    _write(cfg.paths.work / "ET01", "video_001.mp4", b"A" * 4000)
    _write(cfg.paths.output, "final.mp4", b"B" * 6000)

    out = services.tier_discipline(cfg)
    assert out["ok"] is True
    assert out["bytes_freed"] == 10000
    # sources emptied, files now on the HDD staging side
    assert not (cfg.paths.work / "ET01" / "video_001.mp4").exists()
    assert list(staging.rglob("video_001.mp4"))
    assert list(staging.rglob("final.mp4"))


def test_tier_refuses_when_busy(tmp_path):
    staging = tmp_path / "hdd" / "staging"
    cfg = _make_config(tmp_path, staging_root=staging)
    _write(cfg.paths.output, "final.mp4", b"B" * 100)
    writer = StatusWriter(status_path_for(cfg), cfg.discipline)
    writer.update(state=State.MERGING)

    out = services.tier_discipline(cfg)
    assert out["ok"] is False
    assert "beschaeftigt" in out["error"]


def test_retention_sweep_runs(tmp_path):
    staging = tmp_path / "hdd" / "staging"
    cfg = _make_config(tmp_path, staging_root=staging, retention_days=7)
    staging.mkdir(parents=True)
    out = services.run_retention_sweep_for(cfg)
    assert out["ok"] is True
    assert out["deleted"] == []
    assert out["scanned"] == 0
