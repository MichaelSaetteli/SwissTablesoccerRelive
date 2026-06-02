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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# POSIX locations where removable volumes are typically mounted.
_POSIX_MOUNT_PARENTS = ("/media", "/run/media", "/Volumes")
# macOS system volume we never treat as a card.
_MACOS_SYSTEM_VOLUMES = {"Macintosh HD", "Macintosh HD - Data", "Recovery"}


def discover_card_roots() -> List[Path]:
    if sys.platform.startswith("win"):
        return _windows_roots()
    return _posix_roots()


_DRIVE_REMOVABLE = 2  # Win32 DRIVE_REMOVABLE


def _windows_roots() -> List[Path]:
    roots: List[Path] = []
    # Skip A:/B: (legacy floppy) and C: (system); enumerate the rest.
    for letter in string.ascii_uppercase[3:]:
        drive = Path(f"{letter}:\\")
        if drive.exists() and _is_card_drive(drive):
            roots.append(drive)
    return roots


def _is_card_drive(drive: Path) -> bool:
    """Keep removable media (and any drive with a DCIM/), hide internal disks.

    Operators plug cards into a reader (removable). Internal/attached fixed
    disks (e.g. a `HDD12TB` data volume) would otherwise show up as locked
    rows. A drive that carries a ``DCIM/`` is always kept - that covers card
    readers which report as 'fixed'. Fails open (keeps the drive) so a real
    card is never hidden by a probing error.
    """
    try:
        if (drive / "DCIM").is_dir():
            return True
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(  # type: ignore[attr-defined]
            str(drive)
        ) == _DRIVE_REMOVABLE
    except Exception:  # noqa: BLE001 - never hide a card on a probe error
        return True


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


# ---------------------------------------------------------------------------
# Volume identity (name + serial) - drives table + card-id derivation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeInfo:
    """Identity of a mounted volume, independent of its drive letter."""

    root: Path
    label: Optional[str]    # volume name, e.g. "E01"
    serial: Optional[str]   # volume serial number (hex), if readable


# Reads (label, serial) for a root. Injectable so the derivation logic is
# unit-testable without a real removable volume.
VolumeReader = Callable[[Path], Tuple[Optional[str], Optional[str]]]


def read_volume_info(root: Path, *, reader: Optional[VolumeReader] = None) -> VolumeInfo:
    """Return the volume name + serial for *root* (best-effort)."""
    read = reader or _default_volume_reader
    try:
        label, serial = read(Path(root))
    except OSError:
        label, serial = None, None
    return VolumeInfo(root=Path(root), label=label or None, serial=serial or None)


def _default_volume_reader(root: Path) -> Tuple[Optional[str], Optional[str]]:
    if sys.platform.startswith("win"):
        return _windows_volume(root)
    return _posix_volume(root)


def _windows_volume(root: Path) -> Tuple[Optional[str], Optional[str]]:
    """Read the volume name + serial via the Win32 ``GetVolumeInformationW``."""
    import ctypes
    from ctypes import wintypes

    drive = f"{str(root).rstrip(chr(92))[:2]}\\"  # e.g. "E:\\"
    name_buf = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
        ctypes.c_wchar_p(drive),
        name_buf, ctypes.sizeof(name_buf),
        ctypes.byref(serial),
        None, None, None, 0,
    )
    if not ok:
        return None, None
    label = name_buf.value or None
    return label, f"{serial.value:08X}"


def _posix_volume(root: Path) -> Tuple[Optional[str], Optional[str]]:
    """POSIX fallback: the mount directory name is the volume name.

    Linux/macOS mount removable media under ``/media/<user>/<LABEL>`` etc.,
    so the directory name *is* the volume label. No portable serial here.
    """
    name = Path(root).name
    return (name or None), None
