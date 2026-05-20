"""Rename MP4 files inside each folder to ``video_001.mp4`` ... ``video_NNN.mp4``.

This is the Linux port of ``3_rename_mp4.py``. It is essential that the
naming order is deterministic (sorted by original filename) so the FFmpeg
concat in the next step produces a stable result.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, List, Tuple

sys.stdout.reconfigure(encoding="utf-8")

VIDEO_PREFIX = "video_"
VIDEO_PADDING = 3  # video_001 ... video_999


def _list_mp4(folder: Path) -> List[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() == ".mp4"
    )


def planned_renames(folder: Path) -> List[Tuple[Path, Path]]:
    """Return the (src, dst) pairs that ``rename_folder`` would perform.

    The list excludes files that are already correctly named, so callers can
    short-circuit and detect a no-op run.
    """
    plan: List[Tuple[Path, Path]] = []
    for index, src in enumerate(_list_mp4(folder), start=1):
        new_name = f"{VIDEO_PREFIX}{index:0{VIDEO_PADDING}d}.mp4"
        dst = folder / new_name
        if src.name != new_name:
            plan.append((src, dst))
    return plan


def rename_folder(folder: Path) -> List[Path]:
    """Rename every MP4 in *folder* to ``video_NNN.mp4`` and return new paths.

    The two-phase rename (via temporary names) guarantees we never overwrite
    an existing target even when filenames partially collide with the target
    scheme (e.g. an input already named ``video_002.mp4`` that needs to
    become ``video_001.mp4``).
    """
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    plan = planned_renames(folder)
    if not plan:
        return _list_mp4(folder)

    # Phase 1: rename every src to a unique temp name to avoid collisions.
    temp_pairs: List[Tuple[Path, Path]] = []
    for index, (src, dst) in enumerate(plan):
        temp = folder / f".__rename_tmp_{index}.mp4"
        src.rename(temp)
        temp_pairs.append((temp, dst))

    # Phase 2: rename temp -> final.
    for temp, dst in temp_pairs:
        temp.rename(dst)

    return _list_mp4(folder)


def rename_root(work_dir: Path) -> List[Path]:
    """Apply ``rename_folder`` to every direct child folder of *work_dir*.

    Returns a flat list of all resulting MP4 paths.
    """
    if not work_dir.is_dir():
        raise NotADirectoryError(f"Work directory does not exist: {work_dir}")

    all_renamed: List[Path] = []
    for entry in sorted(work_dir.iterdir()):
        if entry.is_dir():
            all_renamed.extend(rename_folder(entry))
    return all_renamed


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _main(argv: Iterable[str]) -> int:
    args = list(argv)
    if len(args) != 2:
        print("Usage: python rename_mp4.py <work-dir>")
        return 2
    target = Path(args[1])
    renamed = rename_root(target)
    print(f"Renamed {len(renamed)} mp4 file(s) under {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
