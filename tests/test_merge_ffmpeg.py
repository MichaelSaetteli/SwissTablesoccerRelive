"""Tests for pipeline.merge_ffmpeg.

FFmpeg itself is mocked: we replace ``runner`` with a fake that records
the invoked argv and reports a configurable return code, so the tests run
without ffmpeg being installed and without writing real video data.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

import pytest

from pipeline.config_loader import load_config
from pipeline.merge_ffmpeg import (
    MergeError,
    build_ffmpeg_command,
    list_video_files,
    merge_all,
    merge_folder,
    write_concat_list,
)
from tests.conftest import make_mp4


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeRunner:
    """Records every argv passed to it. Configurable returncode + stderr."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str]) -> "subprocess.CompletedProcess[str]":
        self.calls.append(list(cmd))
        # Touch the output file so file existence checks could pass downstream.
        # Output is the last positional argument to ffmpeg in our argv.
        output = Path(cmd[-1])
        if self.returncode == 0:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"\x00")
        return subprocess.CompletedProcess(
            args=cmd, returncode=self.returncode, stdout="", stderr=self.stderr,
        )


# ---------------------------------------------------------------------------
# list_video_files
# ---------------------------------------------------------------------------

def test_list_video_files_filters_to_video_prefix(tmp_path: Path) -> None:
    folder = tmp_path / "ET03"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4")
    make_mp4(folder, "video_002.mp4")
    make_mp4(folder, "skipme.mp4")
    (folder / "notes.txt").write_text("x", encoding="utf-8")

    files = list_video_files(folder)
    names = [f.name for f in files]
    assert names == ["video_001.mp4", "video_002.mp4"]


# ---------------------------------------------------------------------------
# write_concat_list
# ---------------------------------------------------------------------------

def test_write_concat_list_produces_ffmpeg_format(tmp_path: Path) -> None:
    folder = tmp_path / "ET03"
    folder.mkdir()
    a = make_mp4(folder, "video_001.mp4")
    b = make_mp4(folder, "video_002.mp4")

    list_path = write_concat_list(folder, [a, b])

    content = list_path.read_text(encoding="utf-8").splitlines()
    assert content[0] == f"file '{a.resolve()}'"
    assert content[1] == f"file '{b.resolve()}'"
    assert list_path.name == "concat_list.txt"


def test_write_concat_list_escapes_single_quote(tmp_path: Path) -> None:
    folder = tmp_path / "weird's name"
    folder.mkdir()
    file_with_quote = make_mp4(folder, "video_001.mp4")

    list_path = write_concat_list(folder, [file_with_quote])
    content = list_path.read_text(encoding="utf-8")

    # FFmpeg escape: ' -> '\''
    assert "weird'\\''s name" in content


# ---------------------------------------------------------------------------
# build_ffmpeg_command
# ---------------------------------------------------------------------------

def test_build_ffmpeg_command_uses_stream_copy(tmp_path: Path) -> None:
    cmd = build_ffmpeg_command(tmp_path / "list.txt", tmp_path / "out.mp4")
    assert cmd[0] == "ffmpeg"
    assert "-c" in cmd
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert "-f" in cmd
    assert cmd[cmd.index("-f") + 1] == "concat"
    assert "-safe" in cmd
    assert cmd[cmd.index("-safe") + 1] == "0"


# ---------------------------------------------------------------------------
# merge_folder
# ---------------------------------------------------------------------------

def test_merge_folder_calls_runner_with_expected_argv(
    tmp_path: Path, doppel_config_path: Path,
) -> None:
    cfg = load_config(doppel_config_path)
    folder = tmp_path / "ET03_1"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4")
    make_mp4(folder, "video_002.mp4")

    runner = FakeRunner(returncode=0)
    result = merge_folder(folder, cfg, runner=runner)

    assert result.success is True
    assert result.returncode == 0
    expected_name = "2026 STS2 T03 Seetal Doppel Part 1.mp4"
    assert result.output.name == expected_name
    assert result.output.parent == cfg.paths.output

    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[-1] == str(result.output)
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"


def test_merge_folder_raises_on_empty(tmp_path: Path,
                                      doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    empty = tmp_path / "ET03_1"
    empty.mkdir()

    with pytest.raises(MergeError):
        merge_folder(empty, cfg, runner=FakeRunner())


def test_merge_folder_failure_reports_stderr(tmp_path: Path,
                                             doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    folder = tmp_path / "ET05"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4")

    runner = FakeRunner(returncode=1, stderr="ffmpeg exploded")
    result = merge_folder(folder, cfg, runner=runner)

    assert result.success is False
    assert result.returncode == 1
    assert "exploded" in result.stderr


# ---------------------------------------------------------------------------
# merge_all (parallel)
# ---------------------------------------------------------------------------

def test_merge_all_processes_each_folder(tmp_path: Path,
                                         doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    folders = []
    for name in ["ET03_1", "ET03_2", "ET04"]:
        folder = tmp_path / name
        folder.mkdir()
        make_mp4(folder, "video_001.mp4")
        folders.append(folder)

    runner = FakeRunner(returncode=0)
    results = merge_all(folders, cfg, runner=runner)

    assert len(results) == 3
    assert all(r.success for r in results)
    output_names = sorted(r.output.name for r in results)
    assert output_names == [
        "2026 STS2 T03 Seetal Doppel Part 1.mp4",
        "2026 STS2 T03 Seetal Doppel Part 2.mp4",
        "2026 STS2 T04 Seetal Doppel.mp4",
    ]
    assert len(runner.calls) == 3


def test_merge_all_empty_input_returns_empty(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    assert merge_all([], cfg, runner=FakeRunner()) == []


def test_merge_all_partial_failure(tmp_path: Path,
                                   doppel_config_path: Path) -> None:
    """If FFmpeg fails for one folder, the others are still processed and
    the failure is captured in the result list (no exception bubbles up)."""
    cfg = load_config(doppel_config_path)

    good = tmp_path / "ET03"
    good.mkdir()
    make_mp4(good, "video_001.mp4")
    bad = tmp_path / "ET04"
    bad.mkdir()
    make_mp4(bad, "video_001.mp4")

    class FlakyRunner:
        def __init__(self) -> None:
            self.calls: List[List[str]] = []

        def __call__(self, cmd):
            self.calls.append(cmd)
            # Output filename for ET04 contains "T04"
            failing = "T04" in cmd[-1]
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1 if failing else 0,
                stdout="",
                stderr="boom" if failing else "",
            )

    runner = FlakyRunner()
    results = merge_all([good, bad], cfg, runner=runner)

    by_folder = {r.folder.name: r for r in results}
    assert by_folder["ET03"].success is True
    assert by_folder["ET04"].success is False
    assert "boom" in by_folder["ET04"].stderr
