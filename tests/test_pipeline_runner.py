"""Tests for watcher.pipeline_runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

import pytest

from pipeline.config_loader import load_config
from tests.conftest import make_mp4
from watcher.pipeline_runner import (
    PipelineRunError,
    detect_folders,
    run_pipeline,
)
from watcher.status import State, status_path_for


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeFFmpegRunner:
    """Records invocations and writes a stub output file."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: List[List[str]] = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        output = Path(cmd[-1])
        if self.returncode == 0:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"\x00")
        return subprocess.CompletedProcess(
            args=cmd, returncode=self.returncode,
            stdout="", stderr=self.stderr,
        )


def _populate_eingang(eingang: Path, folders_with_counts: dict) -> None:
    eingang.mkdir(parents=True, exist_ok=True)
    for name, count in folders_with_counts.items():
        folder = eingang / name
        folder.mkdir()
        for i in range(1, count + 1):
            make_mp4(folder, f"src_{i:03d}.mp4")


# ---------------------------------------------------------------------------
# detect_folders
# ---------------------------------------------------------------------------

def test_detect_folders_returns_sorted_dirs_only(tmp_path: Path) -> None:
    eingang = tmp_path / "eingang"
    eingang.mkdir()
    (eingang / "ET05").mkdir()
    (eingang / "ET03").mkdir()
    make_mp4(eingang, "stray.mp4")

    folders = detect_folders(eingang)
    assert [f.name for f in folders] == ["ET03", "ET05"]


def test_detect_folders_missing_dir(tmp_path: Path) -> None:
    assert detect_folders(tmp_path / "missing") == []


# ---------------------------------------------------------------------------
# Full happy-path run
# ---------------------------------------------------------------------------

def test_run_pipeline_happy_path(monkeypatch, doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    _populate_eingang(cfg.paths.eingang, {"ET03": 5, "ET04": 3})

    runner = FakeFFmpegRunner(returncode=0)
    # Patch the default ffmpeg invocation
    from pipeline import merge_ffmpeg
    monkeypatch.setattr(merge_ffmpeg, "_default_runner", runner)

    writer = run_pipeline(cfg)
    s = writer.status

    assert s.state == State.DONE
    assert s.error is None
    assert sorted(s.folders_detected) == ["ET03", "ET04"]
    assert sorted(s.folders_processed) == ["ET03", "ET04"]
    assert len(s.output_files) == 2
    # Output files must follow the briefing schema
    # Non-split folder (5 mp4s) -> no auto Part suffix.
    assert any("T03 Seetal Doppel.mp4" in name for name in s.output_files)
    assert any("T04 Seetal Doppel.mp4" in name for name in s.output_files)
    # Eingang must be empty after the move step
    assert detect_folders(cfg.paths.eingang) == []
    # Status file was persisted
    assert status_path_for(cfg).is_file()


def test_run_pipeline_no_folders_keeps_idle(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.paths.eingang.mkdir(parents=True)

    writer = run_pipeline(cfg)
    s = writer.status

    assert s.state == State.IDLE
    assert s.folders_detected == []
    assert s.error is None


def test_run_pipeline_disabled_raises(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.enabled = False
    with pytest.raises(PipelineRunError):
        run_pipeline(cfg)


def test_run_pipeline_ffmpeg_failure_marks_error(
    monkeypatch, doppel_config_path: Path,
) -> None:
    cfg = load_config(doppel_config_path)
    _populate_eingang(cfg.paths.eingang, {"ET03": 2})

    failing = FakeFFmpegRunner(returncode=1, stderr="boom")
    from pipeline import merge_ffmpeg
    monkeypatch.setattr(merge_ffmpeg, "_default_runner", failing)

    writer = run_pipeline(cfg)
    s = writer.status

    assert s.state == State.ERROR
    assert s.error is not None
    assert "ET03" in s.error or "1 of 1" in s.error


def test_run_pipeline_splits_oversize_folder(
    monkeypatch, doppel_config_path: Path,
) -> None:
    cfg = load_config(doppel_config_path)
    # 50 mp4s in a single ET-folder -> must split into 3 (24, 24, 2)
    _populate_eingang(cfg.paths.eingang, {"ET03": 50})

    runner = FakeFFmpegRunner(returncode=0)
    from pipeline import merge_ffmpeg
    monkeypatch.setattr(merge_ffmpeg, "_default_runner", runner)

    writer = run_pipeline(cfg)
    s = writer.status

    assert s.state == State.DONE
    assert sorted(s.folders_processed) == ["ET03_1", "ET03_2", "ET03_3"]
    assert len(s.output_files) == 3
    # Each merge must have a video_*.mp4 list - one ffmpeg call per resulting folder
    assert len(runner.calls) == 3


def test_concurrent_run_for_same_discipline_is_rejected(
    monkeypatch, doppel_config_path: Path,
) -> None:
    """A second invocation while the lock is held must raise immediately."""
    cfg = load_config(doppel_config_path)
    _populate_eingang(cfg.paths.eingang, {"ET03": 1})

    from watcher import pipeline_runner
    lock = pipeline_runner._lock_for(cfg)
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(PipelineRunError):
            run_pipeline(cfg)
    finally:
        lock.release()
