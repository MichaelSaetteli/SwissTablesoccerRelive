"""Scan mounted drives and classify each card for the new ingest model.

"Klick 1: SD-Karten einlesen" (Issue #15). Each mounted volume is turned
into a ``ScannedCard`` whose table comes from the **volume name** (``E01``
-> ``ET01``), whose discipline comes from the **operator's batch choice**
(overridable per card), and whose tournament is the **server's active
tournament** for that discipline. A pre-written ``.sts-card.json`` marker
is no longer required (this revises the marker-only rule in
``SESSION_HANDOFF.md`` §6a, 2026-06-02).

Internally we build a *synthetic* ``CardMarker`` so the rest of the
pipeline (engine / manager / presentation), which already consumes a
marker, keeps working unchanged - the server contract is untouched.

Re-scanning is additive (``merge_scans``): a card already in the inventory
is never reset by a fresh scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple

from upload_client.card_identity import (
    card_uuid_from,
    discipline_hint_from_label,
    table_from_label,
)
from upload_client.dcim import (
    CreatedFn,
    DcimFolder,
    default_selected_names,
    has_stale_alarm,
    scan_dcim,
)
from upload_client.manifest import CardManifest, build_manifest
from upload_client.marker import CardMarker
from upload_client.mounts import VolumeReader, read_volume_info

# Classification of a scanned card. Only CARD_READY may be uploaded.
CARD_READY = "ready"                  # table + discipline + tournament + video
CARD_EMPTY = "empty"                  # ok, but no video in the selection
CARD_NO_TABLE = "no_table"            # volume name carries no table number
CARD_NO_TOURNAMENT = "no_tournament"  # no active tournament for the discipline

# Legacy statuses kept for import compatibility (presentation references
# them); the label-based scan path no longer produces these.
CARD_NO_MARKER = "no_marker"
CARD_WRONG_TOURNAMENT = "wrong_tournament"


@dataclass(frozen=True)
class ScannedCard:
    """One card found during a scan, classified and ready (or not)."""

    root: Path
    status: str
    marker: Optional[CardMarker] = None       # synthetic, built from the volume
    manifest: Optional[CardManifest] = None
    reason: Optional[str] = None
    label: Optional[str] = None               # volume name, e.g. "E01"
    serial: Optional[str] = None
    dcim_folders: Tuple[DcimFolder, ...] = ()
    selected_subdirs: Tuple[str, ...] = ()    # DCIM names included in upload
    stale_alarm: bool = False                 # folders >3 days apart

    @property
    def key(self) -> str:
        """Stable inventory key (the physical card's id, slot-independent)."""
        if self.marker is not None:
            return f"uuid:{self.marker.card_uuid}"
        return f"path:{self.root}"

    @property
    def uploadable(self) -> bool:
        return self.status == CARD_READY


def scan_card(
    root: Path,
    *,
    discipline: Optional[str],
    active_tournaments: Optional[Dict[str, dict]] = None,
    subdir_selection: Optional[Set[str]] = None,
    volume_reader: Optional[VolumeReader] = None,
    created_fn: Optional[CreatedFn] = None,
    card_uuid_override: Optional[str] = None,
) -> ScannedCard:
    """Classify a single mount root for the chosen *discipline*.

    *discipline* is the operator's choice for this card (batch default or
    per-card override); ``None`` falls back to the volume-name hint (E/D).
    *active_tournaments* maps discipline -> the server's tournament dict
    (``{"id", "name", ...}``). *subdir_selection* (DCIM names) overrides the
    default newest-cluster selection.
    """
    root = Path(root)
    info = read_volume_info(root, reader=volume_reader)
    label, serial = info.label, info.serial
    discipline = discipline or discipline_hint_from_label(label)

    table = table_from_label(label)
    if table is None:
        return ScannedCard(
            root=root, status=CARD_NO_TABLE, label=label, serial=serial,
            reason=(
                f"Datentraegername {label!r} enthaelt keine Tischnummer "
                f"(erwartet z.B. E01). Karte umbenennen."
            ),
        )

    tinfo = (active_tournaments or {}).get(discipline) if discipline else None
    if not tinfo or not tinfo.get("id"):
        return ScannedCard(
            root=root, status=CARD_NO_TOURNAMENT, label=label, serial=serial,
            reason=f"Kein aktives {discipline or '?'}-Turnier auf dem Server.",
        )

    folders = scan_dcim(root, created_fn=created_fn)
    alarm = has_stale_alarm(folders)
    if subdir_selection is not None:
        selected = set(subdir_selection)
    else:
        selected = default_selected_names(folders)

    card_uuid = card_uuid_override or card_uuid_from(
        serial=serial, label=label, root=root,
    )
    marker = CardMarker(
        card_uuid=card_uuid,
        tournament_id=int(tinfo["id"]),
        tournament_name=str(tinfo.get("name", "")),
        discipline=discipline,  # type: ignore[arg-type]  (set above)
        table=table,
    )

    # Restrict the manifest to the selected DCIM sub-folders (if any exist);
    # a card with loose video and no DCIM keeps everything.
    selected_arg = selected if folders else None
    manifest = build_manifest(root, selected_subdirs=selected_arg)
    selected_tuple = tuple(sorted(selected))

    if manifest.is_empty:
        return ScannedCard(
            root=root, status=CARD_EMPTY, marker=marker, manifest=manifest,
            label=label, serial=serial, dcim_folders=tuple(folders),
            selected_subdirs=selected_tuple, stale_alarm=alarm,
            reason="Keine Videodateien in der aktuellen Auswahl.",
        )

    return ScannedCard(
        root=root, status=CARD_READY, marker=marker, manifest=manifest,
        label=label, serial=serial, dcim_folders=tuple(folders),
        selected_subdirs=selected_tuple, stale_alarm=alarm,
    )


def scan_mounts(
    roots: Iterable[Path],
    *,
    discipline: Optional[str],
    active_tournaments: Optional[Dict[str, dict]] = None,
    discipline_overrides: Optional[Dict[str, str]] = None,
    subdir_overrides: Optional[Dict[str, Set[str]]] = None,
    volume_reader: Optional[VolumeReader] = None,
    created_fn: Optional[CreatedFn] = None,
) -> Dict[str, ScannedCard]:
    """Scan many roots, keyed by ``ScannedCard.key`` (deduped).

    Per-card overrides (discipline / DCIM selection) are keyed by the card's
    stable id, so they survive re-scans and slot changes.
    """
    overrides = discipline_overrides or {}
    sub_overrides = subdir_overrides or {}
    out: Dict[str, ScannedCard] = {}
    for root in roots:
        info = read_volume_info(root, reader=volume_reader)
        uuid = card_uuid_from(serial=info.serial, label=info.label, root=root)
        card = scan_card(
            root,
            discipline=overrides.get(uuid, discipline),
            active_tournaments=active_tournaments,
            subdir_selection=sub_overrides.get(uuid),
            volume_reader=volume_reader,
            created_fn=created_fn,
            card_uuid_override=uuid,
        )
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
