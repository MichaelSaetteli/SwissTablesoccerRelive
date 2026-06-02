"""Manifest verification for staged uploads.

MVP scope: file count + total bytes. SHA-256 is a future addition tracked
in Issue #15; it would double SD-card I/O so we ship without it and add
once production tells us it matters.

The manifest comes from the client tool, which computed it from the SD
card before uploading. The server walks `staging_path` after the last
chunk and refuses to verify if either count or bytes mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple


class ManifestError(ValueError):
    """Raised when actual staging contents do not match the manifest."""


@dataclass(frozen=True)
class ManifestResult:
    files_seen: int
    bytes_seen: int


# Files with these names are housekeeping and never count towards the
# manifest. .partial means a chunk write was interrupted; the client must
# re-upload it.
IGNORE_NAMES = frozenset({".sts-card.json", ".upload_meta.json"})
IGNORE_SUFFIXES = (".partial",)


def _walk_files(root: Path) -> Iterable[Tuple[Path, int]]:
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        if entry.name in IGNORE_NAMES:
            continue
        if any(entry.name.endswith(s) for s in IGNORE_SUFFIXES):
            continue
        yield entry, entry.stat().st_size


def measure_staging(staging_path: Path) -> ManifestResult:
    """Count files and sum bytes under staging_path. Cheap on tmp filesystems."""
    files_seen = 0
    bytes_seen = 0
    for _path, size in _walk_files(Path(staging_path)):
        files_seen += 1
        bytes_seen += size
    return ManifestResult(files_seen=files_seen, bytes_seen=bytes_seen)


def verify_manifest(
    staging_path: Path,
    *,
    expected_files: int,
    expected_bytes: int,
) -> ManifestResult:
    """Walk staging_path and compare against expectations.

    Raises ManifestError with a human-readable message if either count or
    bytes mismatch. Bytes need an exact match - we do not allow rounding
    because that would mask truncated transfers.
    """
    actual = measure_staging(Path(staging_path))
    problems = []
    if actual.files_seen != expected_files:
        problems.append(
            f"expected {expected_files} files, got {actual.files_seen}"
        )
    if actual.bytes_seen != expected_bytes:
        problems.append(
            f"expected {expected_bytes} bytes, got {actual.bytes_seen}"
        )
    if problems:
        raise ManifestError("; ".join(problems))
    return actual
