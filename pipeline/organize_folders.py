"""Split ET-folders that contain more than 24 MP4s into chunks of 24.

A folder ``ET03`` with 48 MP4 files is split into:

    ET03_1/   <- first 24 files (sorted by name)
    ET03_2/   <- next 24 files

Folders with <= ``max_files`` files are left untouched. The original
``ET03`` directory is removed only if it ends up empty after the split.

This is the Linux port of ``2_OrganizeFolders.py``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Iterable, List

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_MAX_FILES = 24


def list_mp4_files(folder: Path) -> List[Path]:
    """Return MP4 files directly inside *folder* (case-insensitive), sorted."""
    return sorted(
        entry for entry in folder.iterdir()
        if entry.is_file() and entry.suffix.lower() == ".mp4"
    )


def chunk(files: List[Path], chunk_size: int) -> List[List[Path]]:
    """Split *files* into consecutive chunks of *chunk_size*."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [files[i : i + chunk_size] for i in range(0, len(files), chunk_size)]


def split_folder(folder: Path, max_files: int = DEFAULT_MAX_FILES) -> List[Path]:
    """Split *folder* into ``<name>_1, <name>_2, ...`` if it holds >max_files MP4s.

    Returns the list of resulting folders. If no split was needed, returns
    ``[folder]`` unchanged.
    """
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    mp4s = list_mp4_files(folder)
    if len(mp4s) <= max_files:
        return [folder]

    groups = chunk(mp4s, max_files)
    parent = folder.parent
    base_name = folder.name
    new_folders: List[Path] = []

    for index, group in enumerate(groups, start=1):
        new_name = f"{base_name}_{index}"
        new_folder = parent / new_name
        new_folder.mkdir(parents=True, exist_ok=False)
        for source in group:
            shutil.move(str(source), str(new_folder / source.name))
        new_folders.append(new_folder)

    # Remove the original folder if empty (it should be, all MP4s moved out)
    try:
        # Only delete if no files remain. Other content (e.g. logs) stays.
        if not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass

    return new_folders


def organize_root(eingang: Path, max_files: int = DEFAULT_MAX_FILES) -> List[Path]:
    """Iterate over every direct child folder of *eingang* and split as needed.

    Returns the union of all resulting folders (split or untouched), sorted.
    """
    if not eingang.is_dir():
        raise NotADirectoryError(f"Eingang folder does not exist: {eingang}")

    results: List[Path] = []
    for entry in sorted(eingang.iterdir()):
        if not entry.is_dir():
            continue
        results.extend(split_folder(entry, max_files=max_files))
    return sorted(results)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _main(argv: Iterable[str]) -> int:
    args = list(argv)
    if len(args) < 2 or len(args) > 3:
        print("Usage: python organize_folders.py <eingang-dir> [max_files]")
        return 2

    eingang = Path(args[1])
    max_files = int(args[2]) if len(args) == 3 else DEFAULT_MAX_FILES

    folders = organize_root(eingang, max_files=max_files)
    print(f"Organized {eingang}: {len(folders)} folder(s) ready for next step")
    for folder in folders:
        print(f"  {folder} ({len(list_mp4_files(folder))} mp4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
