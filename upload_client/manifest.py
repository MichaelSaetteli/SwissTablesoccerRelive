"""Compute the upload manifest for an SD card (file count + total bytes).

MVP verification is **file count + total bytes only** - no SHA-256 (it
would double SD-card I/O; Issue #15 §6a defers it). The server re-walks
the staging dir after the last chunk and compares against the
``expected_files`` / ``expected_bytes`` we send at ``start``, so the
ignore rules here MUST match ``upload_staging.manifest`` exactly or a
correct upload would be rejected. ``tests/test_client_manifest.py``
asserts that parity to catch drift.

DCIM flattening
---------------
Cameras drop ``.MP4`` files into nested ``DCIM/<sub>/`` folders; the
pipeline expects them flat in ``ETxx/`` and merges them in lexical
filename order (``pipeline/rename_mp4.py`` sorts by name, non-recursive).
The client owns the flattening (Issue #15: "Tool extrahiert flach
Client-seitig", closes Lücke A) by choosing a flat ``relative_name`` per
file.

Operator decision (2026-06-02): all ``.MP4`` files of a table - including
those spread across several sub-folders / recording sessions - are merged
into the one ``ETxx`` folder; there is no ``ETxx_1`` / ``ETxx_2`` split.
To keep the merge chronological we preserve the natural walk order (sorted
by relative path = sub-folder, then filename): if the bare basenames are
already unique and in that order we keep them; otherwise every file gets a
zero-padded index prefix so the lexical sort still equals the walk order.
The mapping is a pure function of the (sorted) card contents, so a re-scan
during resume yields identical names and never re-sends a file under a new
name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from upload_client.marker import MARKER_NAME

# Mirror upload_staging.manifest.IGNORE_* exactly. ``.partial`` marks an
# interrupted chunk write; ``.upload_meta.json`` and the card marker are
# housekeeping and never count towards the manifest.
IGNORE_NAMES = frozenset({MARKER_NAME, ".upload_meta.json"})
IGNORE_SUFFIXES = (".partial",)


# Only these are uploaded. Cameras drop housekeeping files (BACKUP.HST,
# INDEX.DAT ...) into DCIM/; whitelisting video keeps them out of the
# upload AND out of the manifest the server verifies against.
VIDEO_SUFFIXES = (".mp4",)


@dataclass(frozen=True)
class FileEntry:
    """One file to upload."""

    path: Path           # absolute source path on the card
    relative_name: str   # flat POSIX name sent to the server
    size: int            # bytes
    mtime: float = 0.0   # source mtime (epoch secs), for date display


@dataclass(frozen=True)
class CardManifest:
    """The set of files an SD card will upload, plus the totals."""

    root: Path
    files: Tuple[FileEntry, ...]

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def is_empty(self) -> bool:
        return not self.files


def _is_ignored(entry: Path) -> bool:
    if entry.name in IGNORE_NAMES:
        return True
    return any(entry.name.endswith(s) for s in IGNORE_SUFFIXES)


def _dcim_subdir(rel_posix: str) -> Optional[str]:
    """Return the ``DCIM/<sub>`` folder name a relative path lives in, else None."""
    parts = rel_posix.split("/")
    if len(parts) >= 2 and parts[0].upper() == "DCIM":
        return parts[1]
    return None


def _flat_names(rels: List[str]) -> List[str]:
    """Map walk-ordered relative paths to flat, order-preserving names.

    ``rels`` is already in walk order (sorted by relative path). If the bare
    basenames are unique AND already lexically sorted (the common single
    sub-folder case), keep them unchanged. Otherwise prefix every file with
    a zero-padded index so the lexical sort of the flat names still equals
    the walk order and names stay unique.
    """
    basenames = [Path(r).name for r in rels]
    unique = len(set(basenames)) == len(basenames)
    in_order = basenames == sorted(basenames)
    if unique and in_order:
        return basenames
    width = max(3, len(str(len(rels))))
    return [f"{i + 1:0{width}d}_{name}" for i, name in enumerate(basenames)]


def build_manifest(
    card_root: Path,
    *,
    flatten: bool = True,
    video_only: bool = True,
    selected_subdirs: Optional[Set[str]] = None,
) -> CardManifest:
    """Walk ``card_root`` and build the upload manifest.

    * ``video_only`` (default) keeps only ``VIDEO_SUFFIXES`` files, so camera
      housekeeping never gets uploaded or counted.
    * ``selected_subdirs`` (set of ``DCIM/<sub>`` names): when given, files
      living under a ``DCIM`` sub-folder are included only if that sub-folder
      is selected. Files outside ``DCIM`` are always kept. ``None`` = all.
    * ``flatten`` (default) gives each file a flat, order-preserving
      ``relative_name``; ``flatten=False`` keeps the POSIX relative path.
    """
    root = Path(card_root)
    raw: List[Tuple[Path, str, int, float]] = []
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        if _is_ignored(entry):
            continue
        if video_only and entry.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        rel = entry.relative_to(root).as_posix()
        if selected_subdirs is not None:
            sub = _dcim_subdir(rel)
            if sub is not None and sub not in selected_subdirs:
                continue
        stat = entry.stat()
        raw.append((entry, rel, stat.st_size, stat.st_mtime))

    rels = [rel for _path, rel, _size, _mt in raw]
    names = _flat_names(rels) if flatten else rels
    files = [
        FileEntry(path=path, relative_name=name, size=size, mtime=mtime)
        for (path, _rel, size, mtime), name in zip(raw, names)
    ]
    return CardManifest(root=root, files=tuple(files))
