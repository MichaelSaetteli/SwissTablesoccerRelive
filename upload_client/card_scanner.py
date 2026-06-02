"""Scan mounted drives for prepared SD cards and build the inventory.

This is the "Klick 1: SD-Karten einlesen" step (Issue #15). Given a list
of candidate mount roots, it reads each card's marker, builds its upload
manifest, and classifies it. Re-scanning is **additive** (§6a:
"Doppel-Scan-Robustheit ... keine Duplikate, kein State-Reset"): merging
a fresh scan into an existing inventory never drops or resets cards
already in the inventory - that's what ``merge_scans`` guarantees.

The hardware mount/unmount *event* listening (pyudev/wmi) is a later
Slice-2 block; this module only inspects roots it is handed, so it stays
platform-independent and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

from upload_client.manifest import CardManifest, build_manifest
from upload_client.marker import CardMarker, MarkerError, has_marker, read_marker

# Classification of a scanned card. Only CARD_READY may be uploaded.
CARD_READY = "ready"                      # marker ok, files present
CARD_EMPTY = "empty"                      # marker ok, but no files to upload
CARD_NO_MARKER = "no_marker"             # unlabelled - shown locked
CARD_WRONG_TOURNAMENT = "wrong_tournament"  # marker for another tournament


@dataclass(frozen=True)
class ScannedCard:
    """One card found during a scan, classified and ready (or not)."""

    root: Path
    status: str
    marker: Optional[CardMarker] = None
    manifest: Optional[CardManifest] = None
    reason: Optional[str] = None

    @property
    def key(self) -> str:
        """Stable inventory key.

        Marked cards key on their ``card_uuid`` so the same card found in a
        different slot is one entry. Marker-less cards have no uuid, so they
        key on their mount path (still shown, still locked).
        """
        if self.marker is not None:
            return f"uuid:{self.marker.card_uuid}"
        return f"path:{self.root}"

    @property
    def uploadable(self) -> bool:
        return self.status == CARD_READY


def scan_card(
    root: Path, *, active_tournament_id: Optional[int] = None,
) -> ScannedCard:
    """Classify a single mount root."""
    root = Path(root)
    if not has_marker(root):
        return ScannedCard(
            root=root,
            status=CARD_NO_MARKER,
            reason=(
                "Karte ohne Marker - vom Admin nicht beschriftet. "
                "Kann nicht hochgeladen werden."
            ),
        )

    try:
        marker = read_marker(root)
    except MarkerError as exc:
        return ScannedCard(root=root, status=CARD_NO_MARKER, reason=str(exc))

    if (
        active_tournament_id is not None
        and marker.tournament_id != active_tournament_id
    ):
        return ScannedCard(
            root=root,
            status=CARD_WRONG_TOURNAMENT,
            marker=marker,
            reason=(
                f"Karte gehoert zu Turnier {marker.tournament_id} "
                f"({marker.tournament_name}), aktiv ist Turnier "
                f"{active_tournament_id}. Gesperrt."
            ),
        )

    manifest = build_manifest(root)
    if manifest.is_empty:
        return ScannedCard(
            root=root,
            status=CARD_EMPTY,
            marker=marker,
            manifest=manifest,
            reason="Keine hochladbaren Dateien auf der Karte.",
        )

    return ScannedCard(
        root=root, status=CARD_READY, marker=marker, manifest=manifest,
    )


def scan_mounts(
    roots: Iterable[Path], *, active_tournament_id: Optional[int] = None,
) -> Dict[str, ScannedCard]:
    """Scan many roots, keyed by ``ScannedCard.key`` (deduped)."""
    out: Dict[str, ScannedCard] = {}
    for root in roots:
        card = scan_card(root, active_tournament_id=active_tournament_id)
        out[card.key] = card
    return out


def merge_scans(
    existing: Dict[str, ScannedCard], fresh: Dict[str, ScannedCard],
) -> Dict[str, ScannedCard]:
    """Additively merge a fresh scan into an existing inventory.

    Keys already present in ``existing`` are kept untouched (an in-progress
    card's view is never reset by a re-scan); only genuinely new keys are
    added. Returns a new dict; inputs are not mutated.
    """
    merged: Dict[str, ScannedCard] = dict(existing)
    for key, card in fresh.items():
        if key not in merged:
            merged[key] = card
    return merged
