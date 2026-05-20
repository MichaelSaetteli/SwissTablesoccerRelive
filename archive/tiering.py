"""SSD->HDD tiering + retention (Dashboard M3 / Block A).

Two operations, built on the verified archiver primitive so we never
delete an SSD file that did not land intact on the HDD:

1. ``stage_to_hdd`` moves one discipline's ``work_*`` (raw input, after
   the merge) or ``output_*`` (finished video, after a confirmed upload)
   into ``staging_root/<prefix-slug>/<role>_<discipline>/``. It reuses
   ``build_archive_plan`` + ``execute_archive(delete_source=True)``, i.e.
   copy -> SHA-256 verify -> only then delete the source. A failed verify
   aborts before any deletion.

2. ``sweep_staging`` is the retention sweeper: it deletes whole staged
   tournament folders once they are older than ``retention_days``. The
   age is taken from the folder's ``archiv_meta.json`` timestamp (written
   by ``execute_archive``), falling back to the directory mtime. The
   *caller* is responsible for only invoking this while the system is
   idle and at most once per day - this function just does the deletion.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from archive.archiver import ArchiveResult, build_archive_plan, execute_archive

# Roles that may be tiered to the HDD. 'work' holds the raw camera input
# after the eingang->work move; 'output' holds the merged videos.
STAGEABLE_ROLES = ("work", "output")


def stage_to_hdd(
    *,
    tournament_name: str,
    discipline: str,
    role: str,
    source_dir: Path,
    staging_root: Path,
    tournament_id: Optional[int] = None,
    delete_source: bool = True,
) -> ArchiveResult:
    """Verified move of *source_dir* into the tournament's staging folder."""
    if role not in STAGEABLE_ROLES:
        raise ValueError(f"role must be one of {STAGEABLE_ROLES}, got {role!r}")
    staging_root = Path(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)
    plan = build_archive_plan(
        tournament_name=tournament_name,
        archive_root=staging_root,
        sources={discipline: {role: Path(source_dir)}},
        tournament_id=tournament_id,
    )
    return execute_archive(plan, delete_source=delete_source)


# ---------------------------------------------------------------------------
# Retention sweeper
# ---------------------------------------------------------------------------

@dataclass
class SweepResult:
    deleted: List[str] = field(default_factory=list)
    bytes_freed: int = 0
    scanned: int = 0


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _iso_to_epoch(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def staged_age_epoch(tournament_dir: Path) -> Optional[float]:
    """Best-effort 'when was this staged' epoch for retention decisions.

    Prefers the archiver's meta timestamp (``ssd_freigegeben_am`` is the
    last action, else ``archiviert_am``); falls back to the directory's
    own mtime when no meta is present.
    """
    meta = tournament_dir / "archiv_meta.json"
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            ts = data.get("ssd_freigegeben_am") or data.get("archiviert_am")
            epoch = _iso_to_epoch(ts)
            if epoch is not None:
                return epoch
        except (OSError, ValueError):
            pass
    try:
        return tournament_dir.stat().st_mtime
    except OSError:
        return None


def sweep_staging(
    staging_root: Path,
    *,
    retention_days: float,
    now: Optional[float] = None,
) -> SweepResult:
    """Delete staged tournament folders older than *retention_days*."""
    staging_root = Path(staging_root)
    result = SweepResult()
    if not staging_root.is_dir():
        return result

    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400.0

    for entry in sorted(staging_root.iterdir()):
        if not entry.is_dir():
            continue
        result.scanned += 1
        age_epoch = staged_age_epoch(entry)
        if age_epoch is None or age_epoch >= cutoff:
            continue
        freed = _dir_size(entry)
        try:
            shutil.rmtree(entry)
        except OSError:
            continue
        result.deleted.append(str(entry))
        result.bytes_freed += freed
    return result
