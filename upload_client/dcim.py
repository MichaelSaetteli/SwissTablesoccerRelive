"""Analyse the ``DCIM/`` sub-folders of a card and flag stale leftovers.

Operator requirement (2026-06-02): camera clocks are frequently wrong, so
*absolute* dates are meaningless. What matters is the **relative spacing**
of the DCIM sub-folders' dates (the "Aenderungsdatum" / mtime, which is
the reliable recording timestamp - see ``_default_created``):

* All sub-folders within a few days of each other (even several, e.g.
  after a card swap) -> one recording, ingest everything.
* Sub-folders that sit **more than 3 days apart** -> almost certainly an
  old leftover recording mixed in. Raise an alarm and let the operator
  choose which sub-folder(s) to ingest (default: the newest cluster on,
  older clusters off).

Only sub-folders that actually contain video count; loose camera
housekeeping files in ``DCIM/`` (``BACKUP.HST``, ``INDEX.DAT`` …) are
ignored here and never uploaded.

Pure / Qt-free. ``stat_fn`` is injectable so the clustering can be tested
without touching the filesystem clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set

# Default: only ``.mp4`` is treated as video. Extend if cameras ever emit
# other containers (operator can request .mov/.mts).
VIDEO_SUFFIXES = (".mp4",)

# Sub-folder clusters more than this far apart trigger the stale alarm.
DEFAULT_MAX_GAP = timedelta(days=3)

# Returns a folder's date. Default uses st_mtime (the "Aenderungsdatum"
# shown in Explorer): it reflects when the camera last wrote the folder and
# - unlike the creation time - survives copying / re-labelling the card,
# so it is the reliable recording timestamp for the spread check.
CreatedFn = Callable[[Path], datetime]


def _default_created(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


@dataclass(frozen=True)
class DcimFolder:
    """One ``DCIM/<sub>`` folder that contains video, with its date + size."""

    path: Path
    name: str
    created: datetime
    video_count: int
    video_bytes: int


def _is_video(entry: Path, video_suffixes: Sequence[str]) -> bool:
    return entry.is_file() and entry.suffix.lower() in video_suffixes


def scan_dcim(
    card_root: Path,
    *,
    video_suffixes: Sequence[str] = VIDEO_SUFFIXES,
    created_fn: Optional[CreatedFn] = None,
) -> List[DcimFolder]:
    """List the video-bearing ``DCIM/<sub>`` folders, sorted oldest-first.

    Folders without any video file are skipped (a camera may leave empty
    or housekeeping-only folders). Returns ``[]`` if there is no ``DCIM/``.
    """
    created = created_fn or _default_created
    dcim = Path(card_root) / "DCIM"
    if not dcim.is_dir():
        return []

    folders: List[DcimFolder] = []
    for sub in sorted(p for p in dcim.iterdir() if p.is_dir()):
        videos = [f for f in sub.rglob("*") if _is_video(f, video_suffixes)]
        if not videos:
            continue
        folders.append(
            DcimFolder(
                path=sub,
                name=sub.name,
                created=created(sub),
                video_count=len(videos),
                video_bytes=sum(f.stat().st_size for f in videos),
            )
        )
    folders.sort(key=lambda f: (f.created, f.name))
    return folders


def cluster(
    folders: Sequence[DcimFolder], *, max_gap: timedelta = DEFAULT_MAX_GAP,
) -> List[List[DcimFolder]]:
    """Group date-sorted folders, splitting wherever a gap exceeds *max_gap*.

    A new cluster starts when the step from one folder's creation date to
    the next is **strictly greater** than *max_gap* ("more than 3 days").
    """
    ordered = sorted(folders, key=lambda f: (f.created, f.name))
    clusters: List[List[DcimFolder]] = []
    current: List[DcimFolder] = []
    for folder in ordered:
        if current and (folder.created - current[-1].created) > max_gap:
            clusters.append(current)
            current = []
        current.append(folder)
    if current:
        clusters.append(current)
    return clusters


def has_stale_alarm(
    folders: Sequence[DcimFolder], *, max_gap: timedelta = DEFAULT_MAX_GAP,
) -> bool:
    """True if the folders fall into more than one date cluster."""
    return len(cluster(folders, max_gap=max_gap)) > 1


def default_selected_names(
    folders: Sequence[DcimFolder], *, max_gap: timedelta = DEFAULT_MAX_GAP,
) -> Set[str]:
    """Names to pre-select: the newest cluster (older clusters left off).

    With a single cluster everything is selected. With several, only the
    latest cluster is on by default - the operator can still toggle.
    """
    clusters = cluster(folders, max_gap=max_gap)
    if not clusters:
        return set()
    return {f.name for f in clusters[-1]}
