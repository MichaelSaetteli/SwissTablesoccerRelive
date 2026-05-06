"""Universal move helper.

Replaces the Windows v6 Robocopy step with a Linux-friendly equivalent.
For same-volume moves Python's ``shutil.move`` does an atomic rename; for
cross-volume moves it falls back to copy-then-delete. This is fine for the
NAS where eingang/work/output all live under ``/volume1/video-pipeline``.

Performance note: for directory moves where the destination does NOT yet
exist (the common eingang->work case), we take a fast path that does a
single atomic ``os.rename`` of the whole directory instead of N per-file
moves. On a ``ET01/`` folder with 24 mp4s this is 24 syscalls vs 1.

Usage::

    python MoveFiles.py <src> <dst>

If ``<src>`` is a directory and ``<dst>`` exists, the *contents* are
merged into ``<dst>``. If ``<dst>`` does not exist, the directory is
renamed atomically.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Sequence

sys.stdout.reconfigure(encoding="utf-8")


class MoveError(RuntimeError):
    """Raised when a move operation cannot be completed."""


def move_path(src: Path, dst: Path) -> List[Path]:
    """Move ``src`` into ``dst`` and return the list of resulting paths.

    Behaviour:
      * If ``src`` is a file, it is moved to ``dst`` (treating ``dst`` as a
        target file path if its parent exists, or as a target directory if
        ``dst`` itself is an existing directory).
      * If ``src`` is a directory and ``dst`` does NOT exist, the whole
        directory is renamed atomically (fast path: 1 syscall).
      * If ``src`` is a directory and ``dst`` exists, *its contents* are
        merged into ``dst`` (slow path: per-file moves). The empty source
        directory is then removed.
    """
    src = Path(src)
    dst = Path(dst)

    if not src.exists():
        raise MoveError(f"Source does not exist: {src}")

    if src.is_file():
        if dst.is_dir():
            target = dst / src.name
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            target = dst
        shutil.move(str(src), str(target))
        return [target]

    if src.is_dir():
        # Fast path: target does not exist yet -> single atomic rename.
        # Same-volume case ends in microseconds; cross-volume falls back
        # to the per-file loop via the OSError handler below.
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(str(src), str(dst))
            except OSError:
                # Cross-volume rename / EXDEV -> fall through to copy loop.
                pass
            else:
                return sorted(dst.iterdir())

        dst.mkdir(parents=True, exist_ok=True)
        moved: List[Path] = []
        for entry in sorted(src.iterdir()):
            target = dst / entry.name
            shutil.move(str(entry), str(target))
            moved.append(target)
        try:
            src.rmdir()
        except OSError:
            pass
        return moved

    raise MoveError(f"Source is neither file nor directory: {src}")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _main(argv: Sequence[str]) -> int:
    if len(argv) != 3:
        print("Usage: python MoveFiles.py <src> <dst>")
        return 2
    src = Path(argv[1])
    dst = Path(argv[2])
    try:
        moved = move_path(src, dst)
    except (MoveError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Moved {len(moved)} item(s) from {src} -> {dst}")
    for path in moved:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
