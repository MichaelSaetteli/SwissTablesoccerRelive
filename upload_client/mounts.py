"""Discover candidate SD-card mount roots (Qt-free).

MVP heuristic: enumerate currently-mounted removable-media roots per
platform and let ``card_scanner`` classify them (a root without a marker
is still surfaced, locked, per §6a). This is the manual "Einlesen" path.

Event-driven mount/unmount monitoring (pyudev on Linux, wmi on Windows)
plus the "card pulled mid-upload -> interrupted" hardware awareness is a
separate later Slice-2 block; keeping discovery here and injectable means
that block can replace this function without touching the GUI.
"""

from __future__ import annotations

import string
import sys
from pathlib import Path
from typing import List

# POSIX locations where removable volumes are typically mounted.
_POSIX_MOUNT_PARENTS = ("/media", "/run/media", "/Volumes")
# macOS system volume we never treat as a card.
_MACOS_SYSTEM_VOLUMES = {"Macintosh HD", "Macintosh HD - Data", "Recovery"}


def discover_card_roots() -> List[Path]:
    if sys.platform.startswith("win"):
        return _windows_roots()
    return _posix_roots()


def _windows_roots() -> List[Path]:
    roots: List[Path] = []
    # Skip A:/B: (legacy floppy) and C: (system); enumerate the rest.
    for letter in string.ascii_uppercase[3:]:
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots.append(drive)
    return roots


def _posix_roots() -> List[Path]:
    roots: List[Path] = []
    for parent in _POSIX_MOUNT_PARENTS:
        base = Path(parent)
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            # /run/media/<user>/<volume> is one level deeper.
            if parent == "/run/media":
                for vol in sorted(child.iterdir()):
                    if vol.is_dir():
                        roots.append(vol)
                continue
            if child.name in _MACOS_SYSTEM_VOLUMES:
                continue
            roots.append(child)
    return roots
