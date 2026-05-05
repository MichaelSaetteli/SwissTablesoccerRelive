"""Tests for pipeline.MoveFiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.MoveFiles import MoveError, move_path
from tests.conftest import make_mp4


def test_move_single_file_into_existing_dir(tmp_path: Path) -> None:
    src_file = make_mp4(tmp_path / "src", "video.mp4")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    moved = move_path(src_file, dst_dir)

    assert moved == [dst_dir / "video.mp4"]
    assert not src_file.exists()
    assert (dst_dir / "video.mp4").is_file()


def test_move_single_file_to_target_path(tmp_path: Path) -> None:
    src_file = make_mp4(tmp_path / "src", "video.mp4")
    target = tmp_path / "dst" / "renamed.mp4"

    moved = move_path(src_file, target)

    assert moved == [target]
    assert target.is_file()
    assert not src_file.exists()


def test_move_directory_contents(tmp_path: Path) -> None:
    src_dir = tmp_path / "ET03"
    for i in range(3):
        make_mp4(src_dir, f"video_{i}.mp4")
    dst_dir = tmp_path / "work" / "ET03"

    moved = move_path(src_dir, dst_dir)

    assert len(moved) == 3
    assert all(p.parent == dst_dir for p in moved)
    assert not src_dir.exists()  # empty source removed
    for i in range(3):
        assert (dst_dir / f"video_{i}.mp4").is_file()


def test_move_directory_creates_destination(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    make_mp4(src_dir, "a.mp4")
    dst_dir = tmp_path / "fresh" / "nested" / "dst"

    move_path(src_dir, dst_dir)

    assert dst_dir.is_dir()
    assert (dst_dir / "a.mp4").is_file()


def test_move_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(MoveError):
        move_path(tmp_path / "nope", tmp_path / "dst")
