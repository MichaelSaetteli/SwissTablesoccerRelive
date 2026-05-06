"""Regression tests for the post-Schritt-5 optimisations.

Covers:
  * fsync-only-on-terminal-states (JsonStatusFile.update durable flag)
  * per-discipline lock in upload_batch
  * FFmpeg stderr persisted to <logs>/ffmpeg_*.log
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import List

import pytest

from pipeline.config_loader import load_config
from pipeline.merge_ffmpeg import merge_folder
from pipeline.status_file import JsonStatusFile
from tests.conftest import make_mp4
from watcher.status import State, StatusWriter, status_path_for


# ---------------------------------------------------------------------------
# Shared JsonStatusFile base
# ---------------------------------------------------------------------------

def test_json_status_file_durable_flag_passes_through(monkeypatch, tmp_path: Path) -> None:
    """update(durable=True) must call os.fsync; durable=False must not."""
    fsync_calls: List[int] = []
    real_fsync = __import__("os").fsync

    def spy_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr("pipeline.status_file.os.fsync", spy_fsync)

    writer = StatusWriter(tmp_path / "s.json", "Doppel")
    n_init = len(fsync_calls)
    assert n_init >= 1  # initial write is durable

    writer.update(state=State.MERGING)        # durable=False default
    writer.update(state=State.RENAMING)
    assert len(fsync_calls) == n_init         # no extra fsyncs

    writer.update(state=State.DETECTING, durable=True)
    assert len(fsync_calls) == n_init + 1     # one new fsync


def test_status_writer_terminal_methods_are_durable(monkeypatch, tmp_path: Path) -> None:
    fsync_calls: List[int] = []
    real_fsync = __import__("os").fsync
    monkeypatch.setattr(
        "pipeline.status_file.os.fsync",
        lambda fd: (fsync_calls.append(fd), real_fsync(fd))[1],
    )

    writer = StatusWriter(tmp_path / "s.json", "Doppel")
    n0 = len(fsync_calls)

    writer.begin_run(["ET01"])
    n1 = len(fsync_calls)
    writer.finish_run(["out.mp4"])
    n2 = len(fsync_calls)
    writer.fail_run("boom")
    n3 = len(fsync_calls)

    assert n1 > n0  # begin -> fsync
    assert n2 > n1  # finish -> fsync
    assert n3 > n2  # fail -> fsync


# ---------------------------------------------------------------------------
# Upload batch lock
# ---------------------------------------------------------------------------

def test_upload_batch_rejects_concurrent_run(doppel_config_path: Path) -> None:
    from youtube.youtube_uploader import UploadError, _lock_for, upload_batch

    cfg = load_config(doppel_config_path)
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    make_mp4(cfg.paths.output, "2026 STS2 T01 Seetal Doppel.mp4")

    lock = _lock_for(cfg)
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(UploadError, match="already running"):
            upload_batch(object(), cfg)
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# FFmpeg stderr -> logs/
# ---------------------------------------------------------------------------

def test_merge_folder_writes_ffmpeg_log(tmp_path: Path,
                                        doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.paths.logs.mkdir(parents=True, exist_ok=True)
    folder = tmp_path / "ET03"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4")

    def fake_runner(cmd):
        # Write a tiny stub output file as the real ffmpeg would.
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"\x00")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr="frames muxed=42\n",
        )

    result = merge_folder(folder, cfg, runner=fake_runner)
    assert result.success is True
    assert result.log_path is not None
    assert result.log_path.is_file()
    content = result.log_path.read_text(encoding="utf-8")
    assert "returncode=0" in content
    assert "frames muxed=42" in content


def test_merge_folder_log_is_per_invocation(tmp_path: Path,
                                            doppel_config_path: Path) -> None:
    """Two merges of the same folder name produce two distinct log files."""
    cfg = load_config(doppel_config_path)
    cfg.paths.logs.mkdir(parents=True, exist_ok=True)

    folder = tmp_path / "ET05"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4")

    def fake_runner(cmd):
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"\x00")
        return subprocess.CompletedProcess(args=cmd, returncode=0,
                                           stdout="", stderr="")

    r1 = merge_folder(folder, cfg, runner=fake_runner)
    r2 = merge_folder(folder, cfg, runner=fake_runner)
    assert r1.log_path != r2.log_path
    assert r1.log_path.parent == cfg.paths.logs
