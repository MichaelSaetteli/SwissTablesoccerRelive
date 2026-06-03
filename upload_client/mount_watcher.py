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

Removals are *debounced*: on real Windows hardware a heavy upload read
makes the drive root briefly unstattable, so a single poll can wrongly see
the card as gone (``Path("E:\\\\").exists()`` momentarily returns False).
A card is only reported ``removed`` after it has been absent for several
*consecutive* polls; a transient miss that re-appears next poll resets the
counter and never fires. A genuinely pulled card stays absent and is still
caught within a couple of seconds. Insertions are reported immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence

from upload_client.mounts import discover_card_roots

# A card must be missing this many consecutive polls before it counts as
# removed. At the GUI's ~1.5 s poll this is ~4.5 s of continuous absence -
# long enough to ride out a heavy-read stat glitch, short enough that a
# real pull is noticed quickly.
_DEFAULT_REMOVAL_CONFIRMATIONS = 3


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
        self,
        list_roots: Callable[[], Sequence[Path]] = discover_card_roots,
        *,
        removal_confirmations: int = _DEFAULT_REMOVAL_CONFIRMATIONS,
    ) -> None:
        self._list = list_roots
        self._present: set = set()
        self._missing: Dict[Path, int] = {}  # root -> consecutive missing polls
        self._confirm = max(1, removal_confirmations)

    def poll(self) -> MountChange:
        """Diff the current mount set against the last poll.

        The first poll reports everything currently plugged in as
        ``inserted`` (``_present`` starts empty), so the GUI auto-scans what
        is already in the reader at startup. Removals are debounced (see the
        module docstring) so a heavy-read stat glitch is not mistaken for a
        pulled card.
        """
        current = set(Path(r) for r in self._list())

        inserted = sorted(current - self._present)
        for root in inserted:
            self._present.add(root)
            self._missing.pop(root, None)

        # A root seen this poll is healthy: clear any pending miss count.
        for root in current:
            self._missing.pop(root, None)

        removed: List[Path] = []
        for root in list(self._present):
            if root in current:
                continue
            self._missing[root] = self._missing.get(root, 0) + 1
            if self._missing[root] >= self._confirm:
                self._present.discard(root)
                self._missing.pop(root, None)
                removed.append(root)

        return MountChange(inserted=inserted, removed=sorted(removed))

    @property
    def present(self) -> List[Path]:
        return sorted(self._present)
