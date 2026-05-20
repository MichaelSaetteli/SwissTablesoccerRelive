"""Tests for youtube.upload_status."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.config_loader import load_config
from youtube.upload_status import (
    LOG_TAIL_MAX,
    UploadState,
    UploadStatus,
    UploadStatusWriter,
    read_upload_status,
    upload_status_path_for,
    write_upload_status,
)


def test_upload_status_path_for_uses_config_dir(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    expected = doppel_config_path.parent / "upload_status_doppel.json"
    assert upload_status_path_for(cfg) == expected


def test_round_trip_persists_all_fields(tmp_path: Path) -> None:
    path = tmp_path / "u.json"
    status = UploadStatus(
        discipline="Einzel",
        state=UploadState.UPLOADING,
        total_files=5,
        completed_files=2,
        current_file="2026 STS2 T03 Bern Einzel.mp4",
        current_progress_percent=42.5,
        uploaded_video_ids=["abc", "def"],
        playlist_id="PLxxxx",
        quota_hint="hint",
    )
    write_upload_status(path, status)

    loaded = read_upload_status(path)
    assert loaded is not None
    assert loaded.completed_files == 2
    assert loaded.current_progress_percent == 42.5
    assert loaded.uploaded_video_ids == ["abc", "def"]
    assert loaded.updated_at is not None  # set by writer


def test_writer_initializes_idle(tmp_path: Path) -> None:
    writer = UploadStatusWriter(tmp_path / "u.json", "Doppel")
    assert writer.status.state == UploadState.IDLE
    assert writer.status.total_files == 0
    assert (tmp_path / "u.json").is_file()


def test_writer_full_lifecycle(tmp_path: Path) -> None:
    writer = UploadStatusWriter(tmp_path / "u.json", "Doppel")

    writer.begin(total_files=2, quota_hint="3200 von 10000")
    s = writer.status
    assert s.state == UploadState.PREPARING
    assert s.total_files == 2
    assert s.completed_files == 0
    assert s.quota_hint == "3200 von 10000"

    writer.begin_file("video1.mp4")
    assert writer.status.state == UploadState.UPLOADING
    assert writer.status.current_file == "video1.mp4"

    writer.update_progress(40.0)
    assert writer.status.current_progress_percent == 40.0

    writer.finish_file("video_id_1")
    assert writer.status.completed_files == 1
    assert writer.status.uploaded_video_ids == ["video_id_1"]
    assert writer.status.current_progress_percent == 100.0

    writer.begin_file("video2.mp4")
    writer.finish_file("video_id_2")
    assert writer.status.completed_files == 2

    writer.finish()
    assert writer.status.state == UploadState.DONE
    assert writer.status.finished_at is not None
    assert writer.status.error is None


def test_writer_fail_path(tmp_path: Path) -> None:
    writer = UploadStatusWriter(tmp_path / "u.json", "Einzel")
    writer.begin(total_files=1, quota_hint="")
    writer.fail("HttpError 401")
    assert writer.status.state == UploadState.ERROR
    assert writer.status.error == "HttpError 401"


def test_writer_log_tail_caps_length(tmp_path: Path) -> None:
    writer = UploadStatusWriter(tmp_path / "u.json", "Doppel")
    for i in range(LOG_TAIL_MAX + 50):
        writer.append_log(f"line {i}")
    assert len(writer.status.log_tail) == LOG_TAIL_MAX
    assert writer.status.log_tail[-1].endswith(f"line {LOG_TAIL_MAX + 49}")


def test_writer_unknown_field_raises(tmp_path: Path) -> None:
    writer = UploadStatusWriter(tmp_path / "u.json", "Doppel")
    with pytest.raises(AttributeError):
        writer.update(nonsense=1)


def test_persisted_json_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "u.json"
    UploadStatusWriter(path, "Doppel").update(state=UploadState.PREPARING)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["discipline"] == "Doppel"
    assert data["state"] == UploadState.PREPARING
