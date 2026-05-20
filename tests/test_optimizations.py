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


# ---------------------------------------------------------------------------
# A: atomic folder move fast path
# ---------------------------------------------------------------------------

def test_move_path_uses_single_rename_when_target_missing(monkeypatch, tmp_path: Path) -> None:
    """Fast path: one os.rename instead of N per-file moves."""
    import os
    from pipeline.MoveFiles import move_path

    src = tmp_path / "ET01"
    src.mkdir()
    for i in range(5):
        make_mp4(src, f"video_{i:03d}.mp4")
    dst = tmp_path / "work" / "ET01"  # does NOT exist yet

    rename_calls: List[str] = []
    real_rename = os.rename

    def spy_rename(a, b):
        rename_calls.append(f"{a} -> {b}")
        return real_rename(a, b)

    monkeypatch.setattr("pipeline.MoveFiles.os.rename", spy_rename)

    moved = move_path(src, dst)
    assert len(moved) == 5
    # Only ONE rename, the whole-folder move - not 5.
    assert len(rename_calls) == 1
    assert str(dst) in rename_calls[0]
    assert not src.exists()


def test_move_path_falls_back_to_per_file_when_target_exists(tmp_path: Path) -> None:
    """When dst already exists we cannot atomic-rename - merge instead."""
    from pipeline.MoveFiles import move_path

    src = tmp_path / "ET01"
    src.mkdir()
    make_mp4(src, "video_001.mp4")
    dst = tmp_path / "work" / "ET01"
    dst.mkdir(parents=True)
    make_mp4(dst, "existing.mp4")  # pre-populated

    moved = move_path(src, dst)
    names = sorted(p.name for p in dst.iterdir())
    assert names == ["existing.mp4", "video_001.mp4"]
    assert moved == [dst / "video_001.mp4"]
    assert not src.exists()


# ---------------------------------------------------------------------------
# B: atomic output write (.partial -> rename)
# ---------------------------------------------------------------------------

def test_merge_folder_failure_does_not_leave_partial(tmp_path: Path,
                                                     doppel_config_path: Path) -> None:
    """Failed ffmpeg run must not leave a corrupt half-written output."""
    cfg = load_config(doppel_config_path)
    folder = tmp_path / "ET05"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4")

    def failing_runner(cmd):
        # Simulate ffmpeg writing partial bytes then crashing.
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"\x00\x00\x00")  # corrupt partial
        return subprocess.CompletedProcess(args=cmd, returncode=1,
                                           stdout="", stderr="boom")

    result = merge_folder(folder, cfg, runner=failing_runner)
    assert result.success is False
    # Final name must NOT exist - we never serve the corrupt bytes.
    assert not result.output.exists()
    # Partial must also have been cleaned up.
    assert not list(cfg.paths.output.glob(".*.partial"))


# ---------------------------------------------------------------------------
# C: disk-space pre-flight
# ---------------------------------------------------------------------------

def test_check_disk_space_raises_when_too_tight(monkeypatch, tmp_path: Path) -> None:
    from collections import namedtuple

    from watcher.pipeline_runner import PipelineRunError, check_disk_space

    folder = tmp_path / "ET01"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4", b"x" * 1024)  # 1 KB
    output = tmp_path / "out"

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "watcher.pipeline_runner.shutil.disk_usage",
        lambda _: Usage(total=10_000, used=9_999, free=100),
    )
    with pytest.raises(PipelineRunError, match="Not enough free space"):
        check_disk_space([folder], output)


def test_check_disk_space_passes_when_room(monkeypatch, tmp_path: Path) -> None:
    from collections import namedtuple

    from watcher.pipeline_runner import check_disk_space

    folder = tmp_path / "ET01"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4", b"x" * 1024)
    output = tmp_path / "out"

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "watcher.pipeline_runner.shutil.disk_usage",
        lambda _: Usage(total=10**9, used=0, free=10**9),
    )
    # No exception means pass.
    check_disk_space([folder], output)


def test_run_pipeline_aborts_when_disk_full(monkeypatch, doppel_config_path: Path) -> None:
    """End-to-end: a tight volume must abort BEFORE the move step."""
    from collections import namedtuple

    from watcher.pipeline_runner import PipelineRunError, run_pipeline

    cfg = load_config(doppel_config_path)
    cfg.paths.eingang.mkdir(parents=True, exist_ok=True)
    folder = cfg.paths.eingang / "ET01"
    folder.mkdir()
    make_mp4(folder, "src.mp4", b"x" * 4096)

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "watcher.pipeline_runner.shutil.disk_usage",
        lambda _: Usage(total=10**6, used=10**6 - 100, free=100),
    )

    with pytest.raises(PipelineRunError, match="Not enough free space"):
        run_pipeline(cfg)

    # Crucially: input must STILL be in eingang. We aborted before moving.
    assert folder.is_dir()
    assert (folder / "src.mp4").is_file()
