"""Universal move helper.

Replaces the Windows v6 Robocopy step with a Linux-friendly equivalent.
For same-volume moves Python's ``shutil.move`` does an atomic rename; for
cross-volume moves it falls back to copy-then-delete. This is fine for the
NAS where eingang/work/output all live under ``/volume1/video-pipeline``.

Usage::

    python MoveFiles.py <src> <dst>

If ``<src>`` is a directory, its *contents* are moved into ``<dst>`` (the
``<dst>`` directory is created if missing). If ``<src>`` is a file, it is
moved into ``<dst>`` (which must be an existing directory or a target file
path).
"""

from __future__ import annotations

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
      * If ``src`` is a directory, *its contents* are moved into ``dst``
        (created if missing). The empty source directory is then removed.
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
        dst.mkdir(parents=True, exist_ok=True)
        moved: List[Path] = []
        for entry in sorted(src.iterdir()):
            target = dst / entry.name
            shutil.move(str(entry), str(target))
            moved.append(target)
        # Remove the now-empty source directory (only if we own it and it's empty)
        try:
            src.rmdir()
        except OSError:
            # Non-empty (e.g. dotfiles slipped in) -> leave alone, do not crash
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
