"""Staging/final path derivation - enforces INV-1 (hot path on SSD).

The staging root is a sibling of eingang under the same volume. We refuse
to compute paths if the resolved root lands on HDD; this is the same
guard ``pipeline.config_loader`` applies for eingang/work/output.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

# Mirrors pipeline.config_loader.FORBIDDEN_HOT_PATH_PREFIXES so an HDD path
# can never sneak in via the staging side-door.
FORBIDDEN_PREFIXES: Tuple[str, ...] = ("/volume2/", "/volume3/")

# Tablename pattern: 'ET01' .. 'ET99' (matches parse_folder_name in
# pipeline.config_loader). Mixed-case is intentionally rejected; the
# admin CLI normalises to upper.
TABLE_NAME_RE = re.compile(r"^ET\d{2}$")


class StagingPathError(ValueError):
    """Raised when a path violates INV-1 or the ETxx convention."""


def validate_staging_root(staging_root: Path) -> None:
    """Reject staging roots on HDD volumes (INV-1).

    Logs etc. on HDD are fine; the staging dir holds raw uploads that get
    handed to the watcher, so it MUST live on the same SSD volume as the
    eingang it feeds into.
    """
    raw = str(staging_root)
    for forbidden in FORBIDDEN_PREFIXES:
        if raw.startswith(forbidden):
            raise StagingPathError(
                f"staging root {raw!r} starts with HDD prefix {forbidden!r}. "
                f"Hot-path staging must live on SSD (/volume1/SDD/...). "
                f"See docs/INVARIANTS.md INV-1."
            )


def _validate_table_name(table_name: str) -> None:
    if not TABLE_NAME_RE.match(table_name):
        raise StagingPathError(
            f"table_name {table_name!r} does not match 'ET<NN>' pattern "
            f"(two-digit, uppercase). See INV-5 in docs/INVARIANTS.md."
        )


def staging_path_for(
    staging_root: Path, card_uuid: str,
) -> Path:
    """Return the per-card staging directory (absolute Path).

    Uses the card_uuid as the leaf so two concurrent uploads for the same
    table cannot collide before release.
    """
    validate_staging_root(Path(staging_root))
    if not card_uuid:
        raise StagingPathError("card_uuid must be non-empty")
    # Sanitise: only allow URL-safe characters in the uuid path component.
    if any(c in card_uuid for c in ("/", "\\", "..", "\x00")):
        raise StagingPathError(
            f"card_uuid {card_uuid!r} contains illegal characters"
        )
    return Path(staging_root) / card_uuid


def final_path_for(eingang_root: Path, table_name: str) -> Path:
    """Return the eingang_<disc>/ETxx target the staging dir releases into.

    The eingang_root must also live on SSD; the caller already enforces
    this via `pipeline.config_loader` so we do not re-check here.
    """
    _validate_table_name(table_name)
    return Path(eingang_root) / table_name
