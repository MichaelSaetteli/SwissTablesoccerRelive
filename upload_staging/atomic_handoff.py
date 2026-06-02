"""Atomic staging -> eingang handoff.

When the operator releases a verified card, we move the entire staging
directory into eingang_<disc>/<table>. Same-volume = single `os.rename`
syscall = the watcher either sees the whole folder or none of it. There
is no half-state where the pipeline could trigger on an incomplete
ETxx/.

The release is also idempotent in the safe direction: if the final path
already exists (e.g. operator clicked Freigeben twice), we refuse with a
clear error instead of overwriting.
"""

from __future__ import annotations

import os
from pathlib import Path


class HandoffError(RuntimeError):
    """Raised when the atomic rename cannot be completed."""


def atomic_release(staging_path: Path, final_path: Path) -> Path:
    """Move ``staging_path`` to ``final_path`` via ``os.rename``.

    Preconditions:
      * staging_path exists and is a directory
      * final_path does not exist (refuse to overwrite)
      * both paths are on the same filesystem (= same volume on Synology)

    On EXDEV (cross-device) we raise HandoffError instead of falling back
    to copy+delete, because a slow copy would defeat the purpose of an
    atomic handoff.
    """
    staging_path = Path(staging_path)
    final_path = Path(final_path)

    if not staging_path.is_dir():
        raise HandoffError(
            f"staging path does not exist or is not a directory: {staging_path}"
        )
    if final_path.exists():
        raise HandoffError(
            f"final path already exists: {final_path}. "
            f"Refusing to overwrite - investigate before retrying."
        )

    final_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.rename(str(staging_path), str(final_path))
    except OSError as exc:
        # EXDEV (cross-device) is the only "expected" failure here; we
        # never want to silently copy 24 GB across volumes.
        raise HandoffError(
            f"could not atomically rename {staging_path} -> {final_path}: {exc}. "
            f"Are both paths on the same volume? See INV-1."
        ) from exc

    return final_path
