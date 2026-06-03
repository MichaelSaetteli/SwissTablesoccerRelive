"""Parallel upload coordination over the (synchronous) UploadEngine.

The engine uploads one card synchronously; this manager runs several cards
at once in a thread pool (Issue #15: variable reader parallelism, 1-24
slots, "Tool uploadet parallel") and exposes a thread-safe snapshot the GUI
polls. It is deliberately Qt-free so the orchestration is unit-tested
without a display; the Qt layer just calls these methods and renders
``snapshot()`` on a timer.

Scanning is additive: ``add_scanned`` only adds keys it has not seen, so a
re-scan never resets a card already being uploaded (§6a Doppel-Scan).
Calling ``start_all`` again is safe and idempotent: the engine no-ops a
released/verified card, resumes an interrupted one, and reopens a failed
one, so it doubles as a "retry / resume everything" action.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from upload_client.card_scanner import CARD_READY, ScannedCard
from upload_client.presentation import (
    CardRow,
    DisciplineSummary,
    row_for_progress,
    row_for_scanned,
    summarise,
)
from upload_client.upload_engine import (
    CLIENT_PENDING,
    CLIENT_UPLOADING,
    CLIENT_VERIFIED,
    CardProgress,
    UploadEngine,
    UploadInterrupted,
)


@dataclass(frozen=True)
class ManagerSnapshot:
    """What the GUI renders: one row per card + per-discipline headers."""

    rows: List[CardRow]
    summaries: List[DisciplineSummary]

    @property
    def all_done(self) -> bool:
        return bool(self.summaries) and all(s.all_done for s in self.summaries)


class UploadManager:
    def __init__(
        self,
        engine: UploadEngine,
        *,
        max_parallel: int = 4,
        expected: Optional[Dict[str, int]] = None,
    ) -> None:
        self._engine = engine
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, max_parallel), thread_name_prefix="sts-upload",
        )
        self._lock = threading.RLock()
        self._tracked: Dict[str, ScannedCard] = {}      # key -> scanned card
        self._futures: Dict[str, Future] = {}
        self._expected: Dict[str, int] = dict(expected or {})

    # -- configuration -----------------------------------------------------

    def set_expected(self, expected: Dict[str, int]) -> None:
        """Expected card count per discipline (drives the X/Y header)."""
        with self._lock:
            self._expected = dict(expected)

    def add_scanned(self, cards: Dict[str, ScannedCard]) -> None:
        """Additively register scanned cards (never resets known ones)."""
        with self._lock:
            for key, card in cards.items():
                self._tracked.setdefault(key, card)

    def update_pending(self, cards: Dict[str, ScannedCard]) -> None:
        """Re-apply a scan, replacing cards that have NOT started uploading.

        Used when the operator corrects a card's discipline or DCIM
        selection before upload: the card's view (discipline / table /
        manifest) must update. A card already uploading/verified/released is
        left untouched, so a correction can never disturb in-flight work.
        """
        with self._lock:
            for key, card in cards.items():
                existing = self._tracked.get(key)
                if existing is None:
                    self._tracked[key] = card
                    continue
                uuid = existing.card_uuid or (
                    existing.marker.card_uuid if existing.marker else None
                )
                if uuid is not None:
                    p = self._engine.load(uuid)
                    if p is not None and p.state != CLIENT_PENDING:
                        continue  # in-flight or done -> keep as is
                self._tracked[key] = card

    # -- actions -----------------------------------------------------------

    def start_all(self, *, auto_release: bool) -> None:
        """Submit every uploadable card. Idempotent / safe to re-call."""
        with self._lock:
            for key, card in self._tracked.items():
                if card.status == CARD_READY:
                    self._submit(key, card, auto_release)

    def start_card(self, key: str, *, auto_release: bool) -> None:
        with self._lock:
            card = self._tracked.get(key)
            if card is not None and card.status == CARD_READY:
                self._submit(key, card, auto_release)

    def _submit(self, key: str, card: ScannedCard, auto_release: bool) -> None:
        existing = self._futures.get(key)
        if existing is not None and not existing.done():
            return  # already in flight - never upload one card twice at once
        fut = self._pool.submit(
            self._run, card.marker, card.manifest, auto_release,
        )
        self._futures[key] = fut

    def _run(self, marker, manifest, auto_release: bool) -> Optional[CardProgress]:
        try:
            return self._engine.upload(
                marker, manifest, auto_release=auto_release,
            )
        except UploadInterrupted:
            # State is already persisted as 'interrupted'; the UI shows it
            # and the operator (or a re-scan + start) can resume.
            return None

    def release_verified(self) -> List[CardProgress]:
        """Batch-release every currently verified card (§6a one-click)."""
        with self._lock:
            uuids = [
                c.marker.card_uuid
                for c in self._tracked.values()
                if c.marker is not None and self._state_of(c) == CLIENT_VERIFIED
            ]
        return self._engine.release(uuids) if uuids else []

    def release(self, card_uuids: List[str]) -> List[CardProgress]:
        return self._engine.release(card_uuids)

    def cancel(self, card_uuid: str) -> Optional[CardProgress]:
        return self._engine.cancel(card_uuid)

    def reset(self) -> None:
        """Clear all local state: tracked cards, futures and resume records.

        Backs the GUI "Zuruecksetzen / Neu beginnen" button - the in-app
        replacement for manually deleting ``state.json``. In-flight uploads
        are not force-killed (the pool keeps running any already-submitted
        work), but a card that is not currently uploading is simply
        forgotten; the next scan re-discovers whatever is still plugged in
        from a clean slate.
        """
        with self._lock:
            self._tracked.clear()
            self._futures.clear()
        self._engine.reset_local_state()

    def handle_removed(self, roots) -> List[str]:
        """A card was physically removed: flip any uploading one to interrupted.

        Guards against false alarms: a heavy read can make a removable drive
        miss a single mount poll without actually being unplugged. Before
        interrupting we re-check that the card's root is *really* gone; if it
        still exists, the "removed" event was spurious and is ignored. A
        genuinely pulled card (root no longer exists) is still caught - and a
        real read failure surfaces via the upload thread anyway.

        Returns the table names that were actually interrupted.
        """
        rootset = {str(Path(r)) for r in roots}
        affected: List[str] = []
        with self._lock:
            cards = list(self._tracked.values())
        for card in cards:
            if card.marker is None or card.root is None:
                continue
            if str(card.root) not in rootset:
                continue
            try:
                if Path(card.root).exists():
                    continue  # false alarm - card is still mounted
            except OSError:
                pass  # cannot stat -> treat as gone
            current = self._engine.load(card.marker.card_uuid)
            if current is not None and current.state == CLIENT_UPLOADING:
                self._engine.mark_interrupted(card.marker.card_uuid)
                affected.append(card.marker.table)
        return affected

    # -- read model --------------------------------------------------------

    def _state_of(self, card: ScannedCard) -> str:
        if card.marker is not None:
            p = self._engine.load(card.marker.card_uuid)
            if p is not None:
                return p.state
        return CLIENT_PENDING

    def _row_for(self, card: ScannedCard) -> CardRow:
        if card.marker is not None:
            p = self._engine.load(card.marker.card_uuid)
            if p is not None:
                return row_for_progress(p)
        return row_for_scanned(card)

    def snapshot(self) -> ManagerSnapshot:
        with self._lock:
            tracked = list(self._tracked.values())
            expected = dict(self._expected)

        rows: List[CardRow] = []
        states: Dict[str, List[str]] = {}
        for card in tracked:
            row = self._row_for(card)
            rows.append(row)
            if card.status == CARD_READY and card.marker is not None:
                states.setdefault(card.marker.discipline, []).append(row.state)

        summaries = [
            summarise(disc, st, expected=expected.get(disc, len(st)))
            for disc, st in sorted(states.items())
        ]
        # Stable row order: by discipline then table, locked/odd ones last.
        rows.sort(key=lambda r: (r.discipline, r.table, r.key))
        return ManagerSnapshot(rows=rows, summaries=summaries)

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
