"""Tests for pipeline.rename_mp4."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.rename_mp4 import (
    planned_renames,
    rename_folder,
    rename_root,
)
from tests.conftest import make_mp4


def test_rename_folder_basic(tmp_path: Path) -> None:
    folder = tmp_path / "ET03"
    folder.mkdir()
    for name in ["GH010001.mp4", "GH010002.mp4", "GH010003.mp4"]:
        make_mp4(folder, name)

    rename_folder(folder)
    names = sorted(p.name for p in folder.iterdir() if p.is_file())

    assert names == ["video_001.mp4", "video_002.mp4", "video_003.mp4"]


def test_rename_folder_handles_collisions(tmp_path: Path) -> None:
    """If an input is already named ``video_002.mp4`` we still rename safely."""
    folder = tmp_path / "ET03"
    folder.mkdir()
    # Note alphabetical order: video_002 sorts before video_009
    make_mp4(folder, "video_002.mp4", b"second")
    make_mp4(folder, "video_009.mp4", b"first-but-sorts-second")
    make_mp4(folder, "alpha.mp4", b"sorts-first")

    rename_folder(folder)
    names = sorted(p.name for p in folder.iterdir() if p.is_file())

    assert names == ["video_001.mp4", "video_002.mp4", "video_003.mp4"]
    # Confirm content order: alpha.mp4 was first alphabetically -> video_001
    assert (folder / "video_001.mp4").read_bytes() == b"sorts-first"
    assert (folder / "video_002.mp4").read_bytes() == b"second"
    assert (folder / "video_003.mp4").read_bytes() == b"first-but-sorts-second"


def test_rename_folder_idempotent(tmp_path: Path) -> None:
    folder = tmp_path / "ET03"
    folder.mkdir()
    make_mp4(folder, "video_001.mp4")
    make_mp4(folder, "video_002.mp4")

    plan = planned_renames(folder)
    assert plan == []

    # Second call must not break anything.
    rename_folder(folder)
    names = sorted(p.name for p in folder.iterdir() if p.is_file())
    assert names == ["video_001.mp4", "video_002.mp4"]


def test_rename_folder_ignores_non_mp4(tmp_path: Path) -> None:
    folder = tmp_path / "ET03"
    folder.mkdir()
    make_mp4(folder, "a.mp4")
    (folder / "notes.txt").write_text("ignore me", encoding="utf-8")

    rename_folder(folder)
    names = sorted(p.name for p in folder.iterdir() if p.is_file())

    assert names == ["notes.txt", "video_001.mp4"]


def test_rename_folder_not_a_directory(tmp_path: Path) -> None:
    file_path = make_mp4(tmp_path, "x.mp4")
    with pytest.raises(NotADirectoryError):
        rename_folder(file_path)


def test_rename_root_processes_each_subfolder(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    for sub in ["ET03_1", "ET04"]:
        sub_path = work / sub
        sub_path.mkdir()
        make_mp4(sub_path, "z.mp4")
        make_mp4(sub_path, "a.mp4")

    rename_root(work)

    for sub in ["ET03_1", "ET04"]:
        names = sorted(p.name for p in (work / sub).iterdir() if p.is_file())
        assert names == ["video_001.mp4", "video_002.mp4"]
