"""Tests for web.services.get_processing_estimate_for (M3 / B2 wiring)."""

from __future__ import annotations

import json

import pytest

from db import db_path_for, open_db
from db.runs import finish_run, record_phase, start_run
from db.schema import _reset_connections_for_tests
from db.tournaments import create_tournament
from pipeline.config_loader import load_config
from web import services


@pytest.fixture(autouse=True)
def _isolated():
    _reset_connections_for_tests()
    services._reset_backlog_cache_for_tests()
    yield
    _reset_connections_for_tests()
    services._reset_backlog_cache_for_tests()


def _make_config(tmp_path):
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
    }
    p = tmp_path / "config_doppel.json"
    p.write_text(json.dumps(cfg))
    return load_config(p)


def _seed_completed_run(conn, *, input_bytes, merge_s):
    t = create_tournament(conn, "T", date="2026-05-20")
    run = start_run(
        conn, tournament_id=t.id, discipline="Doppel",
        folder_name="ET01", input_bytes=input_bytes,
    )
    record_phase(
        conn, run_id=run.id, phase="merge",
        started_at="2026-05-20T10:00:00+00:00", duration_s=merge_s,
    )
    finish_run(conn, run_id=run.id, state="done", output_bytes=input_bytes)


def _write_mp4(dir_path, name, size):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_bytes(b"\0" * size)


def test_empty_eingang_reports_zero(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.paths.eingang.mkdir(parents=True, exist_ok=True)
    est = services.get_processing_estimate_for(cfg)
    assert est["input_bytes"] == 0


def test_backlog_without_history_is_calibrating(tmp_path):
    cfg = _make_config(tmp_path)
    _write_mp4(cfg.paths.eingang / "ET01", "a.mp4", 1000)
    est = services.get_processing_estimate_for(cfg)
    assert est["input_bytes"] == 1000
    assert est["calibrating"] is True
    assert est["total_seconds"] is None


def test_estimate_scales_with_backlog(tmp_path):
    cfg = _make_config(tmp_path)
    conn = open_db(db_path_for(cfg.source_path.parent))
    # 1000 bytes merged in 100 s -> 10 B/s; 2000 bytes -> ~200 s.
    _seed_completed_run(conn, input_bytes=1000, merge_s=100.0)
    _write_mp4(cfg.paths.eingang / "ET01", "a.mp4", 2000)

    est = services.get_processing_estimate_for(cfg)
    assert est["input_bytes"] == 2000
    assert est["calibrating"] is False
    assert est["total_seconds"] == pytest.approx(200.0, rel=1e-6)


def test_backlog_is_cached_within_ttl(tmp_path):
    cfg = _make_config(tmp_path)
    _write_mp4(cfg.paths.eingang / "ET01", "a.mp4", 1000)
    first = services.get_processing_estimate_for(cfg)
    assert first["input_bytes"] == 1000

    # Adding more footage must not change the result until the TTL expires.
    _write_mp4(cfg.paths.eingang / "ET01", "b.mp4", 5000)
    second = services.get_processing_estimate_for(cfg)
    assert second["input_bytes"] == 1000
