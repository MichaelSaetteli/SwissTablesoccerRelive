"""Tests for web.services.compute_upload_throughput (M3 / B1)."""

from __future__ import annotations

import pytest

from web.services import compute_upload_throughput
from youtube.upload_status import UploadState, UploadStatus


def _status(**kw) -> UploadStatus:
    base = dict(discipline="Doppel")
    base.update(kw)
    return UploadStatus(**base)


def test_no_files_yields_no_speed():
    s = _status(state=UploadState.IDLE, total_files=0)
    t = compute_upload_throughput(s, total_bytes=0)
    assert t["mbit_s"] is None
    assert t["eta_seconds"] is None
    assert t["percent"] == 0.0


def test_uploading_half_done():
    # 1 GB total, 1 of 2 files done, 60 s elapsed -> 500 MB / 60 s.
    s = _status(
        state=UploadState.UPLOADING,
        total_files=2, completed_files=1, current_progress_percent=0.0,
        started_at="2026-05-20T10:00:00+00:00",
        updated_at="2026-05-20T10:01:00+00:00",
    )
    t = compute_upload_throughput(s, total_bytes=1_000_000_000)
    assert t["uploaded_bytes"] == 500_000_000
    assert t["percent"] == 50.0
    # 500 MB / 60 s * 8 / 1e6 = 66.67 Mbit/s
    assert t["mbit_s"] == pytest.approx(66.67, abs=0.1)
    # remaining 500 MB at same rate -> ~60 s
    assert t["eta_seconds"] == pytest.approx(60.0, abs=0.5)


def test_partial_current_file_counts():
    # 1 of 2 done + current file at 50% -> 75% overall.
    s = _status(
        state=UploadState.UPLOADING,
        total_files=2, completed_files=1, current_progress_percent=50.0,
        started_at="2026-05-20T10:00:00+00:00",
        updated_at="2026-05-20T10:01:00+00:00",
    )
    t = compute_upload_throughput(s, total_bytes=1_000_000_000)
    assert t["percent"] == 75.0
    assert t["uploaded_bytes"] == 750_000_000


def test_done_reports_average_and_zero_eta():
    s = _status(
        state=UploadState.DONE,
        total_files=2, completed_files=2, current_progress_percent=0.0,
        started_at="2026-05-20T10:00:00+00:00",
        updated_at="2026-05-20T10:02:00+00:00",
    )
    t = compute_upload_throughput(s, total_bytes=1_000_000_000)
    assert t["percent"] == 100.0
    assert t["state"] == UploadState.DONE
    assert t["mbit_s"] == pytest.approx(66.67, abs=0.1)
    assert t["eta_seconds"] == pytest.approx(0.0, abs=0.01)


def test_missing_timestamps_yield_no_speed():
    s = _status(
        state=UploadState.UPLOADING,
        total_files=2, completed_files=1, started_at=None, updated_at=None,
    )
    t = compute_upload_throughput(s, total_bytes=1_000_000_000)
    assert t["mbit_s"] is None
