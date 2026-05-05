"""Tests for watcher.status."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.config_loader import load_config
from watcher.status import (
    LOG_TAIL_MAX,
    PipelineStatus,
    State,
    StatusWriter,
    read_status,
    status_path_for,
    write_status,
)


def test_status_path_for_uses_config_dir(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    expected = doppel_config_path.parent / "status_doppel.json"
    assert status_path_for(cfg) == expected


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    status = PipelineStatus(
        discipline="Doppel",
        state=State.MERGING,
        folders_detected=["ET03", "ET04"],
        log_tail=["one", "two"],
    )
    write_status(path, status)

    loaded = read_status(path)
    assert loaded is not None
    assert loaded.discipline == "Doppel"
    assert loaded.state == State.MERGING
    assert loaded.folders_detected == ["ET03", "ET04"]
    assert loaded.updated_at is not None  # set by writer


def test_read_status_missing_returns_none(tmp_path: Path) -> None:
    assert read_status(tmp_path / "no.json") is None


def test_write_status_is_atomic(tmp_path: Path) -> None:
    """Temp file must not be left behind after a successful write."""
    path = tmp_path / "status.json"
    write_status(path, PipelineStatus(discipline="Doppel"))
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_status_writer_init_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    writer = StatusWriter(path, "Doppel")
    assert path.is_file()
    assert writer.status.discipline == "Doppel"
    assert writer.status.state == State.IDLE


def test_status_writer_update(tmp_path: Path) -> None:
    writer = StatusWriter(tmp_path / "s.json", "Einzel")
    writer.update(state=State.MOVING, folders_detected=["ET05"])
    assert writer.status.state == State.MOVING
    assert writer.status.folders_detected == ["ET05"]


def test_status_writer_update_unknown_field(tmp_path: Path) -> None:
    writer = StatusWriter(tmp_path / "s.json", "Doppel")
    with pytest.raises(AttributeError):
        writer.update(nonsense="oops")


def test_status_writer_log_tail_caps_length(tmp_path: Path) -> None:
    writer = StatusWriter(tmp_path / "s.json", "Doppel")
    for i in range(LOG_TAIL_MAX + 50):
        writer.append_log(f"line {i}")
    assert len(writer.status.log_tail) == LOG_TAIL_MAX
    # Most recent line is preserved
    assert writer.status.log_tail[-1].endswith(f"line {LOG_TAIL_MAX + 49}")


def test_begin_finish_run_lifecycle(tmp_path: Path) -> None:
    writer = StatusWriter(tmp_path / "s.json", "Doppel")

    writer.begin_run(["ET03", "ET04"])
    assert writer.status.state == State.MOVING
    assert writer.status.started_at is not None
    assert writer.status.finished_at is None

    writer.finish_run(["out1.mp4", "out2.mp4"])
    assert writer.status.state == State.DONE
    assert writer.status.output_files == ["out1.mp4", "out2.mp4"]
    assert writer.status.finished_at is not None
    assert writer.status.error is None


def test_fail_run_sets_error(tmp_path: Path) -> None:
    writer = StatusWriter(tmp_path / "s.json", "Doppel")
    writer.begin_run(["ET03"])
    writer.fail_run("ffmpeg returned 1")

    s = writer.status
    assert s.state == State.ERROR
    assert s.error == "ffmpeg returned 1"
    assert s.finished_at is not None


def test_persisted_json_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    writer = StatusWriter(path, "Einzel")
    writer.update(state=State.RENAMING)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["discipline"] == "Einzel"
    assert data["state"] == State.RENAMING
