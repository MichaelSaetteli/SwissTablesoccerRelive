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
pipeline expects them flat in ``ETxx/``. The client owns the flattening
(Issue #15: "Tool extrahiert flach Client-seitig", closes Lücke A) by
choosing a flat ``relative_name`` per file. Basename collisions across
sub-folders (Panasonic restarts numbering per card) are de-collided with
a deterministic ``_2``/``_3`` suffix. The mapping is a pure function of
the (sorted) card contents, so a re-scan during resume yields identical
names and never re-sends a file under a new name.

Splitting one card across two recording sessions into ``ETxx_1`` /
``ETxx_2`` is an open question in Issue #15 and is intentionally NOT
decided here - we flatten into a single table folder.
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


def _flatten_name(original_name: str, used: set) -> str:
    """Return a unique flat name, de-colliding deterministically."""
    if original_name not in used:
        return original_name
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    i = 2
    while f"{stem}_{i}{suffix}" in used:
        i += 1
    return f"{stem}_{i}{suffix}"


def build_manifest(card_root: Path, *, flatten: bool = True) -> CardManifest:
    """Walk ``card_root`` and build the upload manifest.

    With ``flatten=True`` (default) every file gets a flat ``relative_name``
    (basename, de-collided). With ``flatten=False`` the POSIX path relative
    to the card root is kept (useful for tests / debugging).
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

    files: List[FileEntry] = []
    used: set = set()
    for path, rel, size in raw:
        if flatten:
            name = _flatten_name(Path(rel).name, used)
        else:
            name = rel
        used.add(name)
        files.append(FileEntry(path=path, relative_name=name, size=size))

    return CardManifest(root=root, files=tuple(files))
