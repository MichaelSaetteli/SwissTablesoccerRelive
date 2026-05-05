"""Tests for pipeline.organize_folders."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.organize_folders import (
    chunk,
    list_mp4_files,
    organize_root,
    split_folder,
)
from tests.conftest import make_mp4


def _make_folder_with_mp4s(parent: Path, name: str, count: int) -> Path:
    folder = parent / name
    folder.mkdir(parents=True)
    for i in range(1, count + 1):
        make_mp4(folder, f"original_{i:03d}.mp4")
    return folder


# ---- chunk ----------------------------------------------------------------

def test_chunk_exact_multiple() -> None:
    files = [Path(f"f{i}") for i in range(48)]
    groups = chunk(files, 24)
    assert len(groups) == 2
    assert all(len(g) == 24 for g in groups)


def test_chunk_with_remainder() -> None:
    files = [Path(f"f{i}") for i in range(50)]
    groups = chunk(files, 24)
    assert [len(g) for g in groups] == [24, 24, 2]


def test_chunk_invalid_size() -> None:
    with pytest.raises(ValueError):
        chunk([Path("a")], 0)


# ---- split_folder ---------------------------------------------------------

def test_split_folder_under_threshold_unchanged(tmp_path: Path) -> None:
    folder = _make_folder_with_mp4s(tmp_path, "ET03", 20)

    result = split_folder(folder, max_files=24)

    assert result == [folder]
    assert folder.is_dir()
    assert len(list_mp4_files(folder)) == 20


def test_split_folder_over_threshold_creates_subfolders(tmp_path: Path) -> None:
    folder = _make_folder_with_mp4s(tmp_path, "ET03", 48)

    result = split_folder(folder, max_files=24)

    names = [p.name for p in result]
    assert names == ["ET03_1", "ET03_2"]
    for sub in result:
        assert len(list_mp4_files(sub)) == 24
    assert not folder.exists()  # original empty -> removed


def test_split_folder_with_remainder(tmp_path: Path) -> None:
    folder = _make_folder_with_mp4s(tmp_path, "ET05", 50)

    result = split_folder(folder, max_files=24)

    counts = [len(list_mp4_files(sub)) for sub in result]
    assert counts == [24, 24, 2]


def test_split_folder_keeps_alphabetical_order(tmp_path: Path) -> None:
    folder = tmp_path / "ET03"
    folder.mkdir()
    for name in ["c.mp4", "a.mp4", "b.mp4"]:
        make_mp4(folder, name)

    result = split_folder(folder, max_files=2)

    # Sorted alphabetically: a, b, c -> first group [a,b], second [c]
    first = [p.name for p in list_mp4_files(result[0])]
    second = [p.name for p in list_mp4_files(result[1])]
    assert first == ["a.mp4", "b.mp4"]
    assert second == ["c.mp4"]


def test_split_folder_not_a_directory(tmp_path: Path) -> None:
    file_path = make_mp4(tmp_path, "not_a_folder.mp4")
    with pytest.raises(NotADirectoryError):
        split_folder(file_path)


# ---- organize_root --------------------------------------------------------

def test_organize_root_handles_mixed_folders(tmp_path: Path) -> None:
    eingang = tmp_path / "eingang"
    eingang.mkdir()
    _make_folder_with_mp4s(eingang, "ET03", 50)  # will split to 3
    _make_folder_with_mp4s(eingang, "ET04", 10)  # untouched
    # A non-folder entry should be ignored
    make_mp4(eingang, "stray.mp4")

    result = organize_root(eingang, max_files=24)
    names = sorted(p.name for p in result)

    assert names == ["ET03_1", "ET03_2", "ET03_3", "ET04"]


def test_organize_root_missing(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        organize_root(tmp_path / "missing")
