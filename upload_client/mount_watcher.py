"""Detect SD cards being inserted / removed (Qt-free, testable).

Issue #15 §6a hardware-state awareness: a card pulled mid-upload must show
a clear warning and the card must go to ``interrupted``; re-inserting it
resumes rather than restarts.

Implementation is poll-based: ``poll()`` lists the currently mounted card
roots and diffs them against the previous set. Polling needs no extra
platform dependency and is trivially testable by injecting ``list_roots``.
The list source is injectable so an event-driven backend (pyudev on Linux,
wmi on Windows) can replace it later without touching the GUI - it would
just feed the same inserted/removed diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Sequence

from upload_client.mounts import discover_card_roots


@dataclass(frozen=True)
class MountChange:
    inserted: List[Path]
    removed: List[Path]

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.removed)


class MountWatcher:
    """Poll the mount table and report inserted / removed card roots."""

    def __init__(
        self, list_roots: Callable[[], Sequence[Path]] = discover_card_roots,
    ) -> None:
        self._list = list_roots
        self._present: set = set()

    def poll(self) -> MountChange:
        """Diff the current mount set against the last poll.

        The first poll reports everything currently plugged in as
        ``inserted`` (``_present`` starts empty), so the GUI auto-scans what
        is already in the reader at startup.
        """
        current = set(Path(r) for r in self._list())
        inserted = sorted(current - self._present)
        removed = sorted(self._present - current)
        self._present = current
        return MountChange(inserted=inserted, removed=removed)

    @property
    def present(self) -> List[Path]:
        return sorted(self._present)
