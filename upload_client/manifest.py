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
from typing import List, Tuple

from upload_client.marker import MARKER_NAME

# Mirror upload_staging.manifest.IGNORE_* exactly. ``.partial`` marks an
# interrupted chunk write; ``.upload_meta.json`` and the card marker are
# housekeeping and never count towards the manifest.
IGNORE_NAMES = frozenset({MARKER_NAME, ".upload_meta.json"})
IGNORE_SUFFIXES = (".partial",)


@dataclass(frozen=True)
class FileEntry:
    """One file to upload."""

    path: Path           # absolute source path on the card
    relative_name: str   # flat POSIX name sent to the server
    size: int            # bytes


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


def build_manifest(card_root: Path, *, flatten: bool = True) -> CardManifest:
    """Walk ``card_root`` and build the upload manifest.

    With ``flatten=True`` (default) every file gets a flat, order-preserving
    ``relative_name``. With ``flatten=False`` the POSIX path relative to the
    card root is kept (useful for tests / debugging).
    """
    root = Path(card_root)
    raw: List[Tuple[Path, str, int]] = []
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        if _is_ignored(entry):
            continue
        rel = entry.relative_to(root).as_posix()
        raw.append((entry, rel, entry.stat().st_size))

    rels = [rel for _path, rel, _size in raw]
    names = _flat_names(rels) if flatten else rels
    files = [
        FileEntry(path=path, relative_name=name, size=size)
        for (path, _rel, size), name in zip(raw, names)
    ]
    return CardManifest(root=root, files=tuple(files))
