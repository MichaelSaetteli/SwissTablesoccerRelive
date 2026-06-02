"""Presentation logic for the upload GUI - pure, Qt-free, unit-testable.

Issue #15 treats UX as a *safety layer*, so the rules that decide what the
operator sees (state badge, safe-to-remove indicator, whether "Freigeben"
is enabled, the completeness header, friendly error language) live here as
plain functions/dataclasses. The Qt widgets are a thin renderer over this;
keeping the logic out of the widgets means the safety behaviour is tested
without a display.

All strings are German, Swiss-friendly (no "ss" -> "ss", never "ss" with
the sharp s). Code stays English.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from upload_client.card_scanner import (
    CARD_EMPTY,
    CARD_NO_MARKER,
    CARD_NO_TABLE,
    CARD_NO_TOURNAMENT,
    CARD_READY,
    CARD_WRONG_TOURNAMENT,
    ScannedCard,
)
from upload_client.upload_engine import (
    CLIENT_CANCELLED,
    CLIENT_FAILED,
    CLIENT_INTERRUPTED,
    CLIENT_PENDING,
    CLIENT_RELEASED,
    CLIENT_UPLOADING,
    CLIENT_VERIFIED,
    SAFE_TO_REMOVE_STATES,
    CardProgress,
)

# symbol + German label per client state.
_BADGES = {
    CLIENT_PENDING: ("⏳", "wartet"),          # hourglass
    CLIENT_UPLOADING: ("▶", "hochladen"),     # play
    CLIENT_VERIFIED: ("✓", "verifiziert"),    # check
    CLIENT_RELEASED: ("✅", "released"),       # green check
    CLIENT_FAILED: ("⚠", "Fehler"),           # warning
    CLIENT_INTERRUPTED: ("⛔", "unterbrochen"),  # no-entry
    CLIENT_CANCELLED: ("✕", "abgebrochen"),   # x
}

SAFE_TO_REMOVE = "\U0001f50c sicher entfernbar"           # plug
DO_NOT_REMOVE = "⛔ Nicht entfernen - Upload laeuft"

# Cards in these client states cannot be uploaded; show them locked.
_LOCKED_SCAN_REASONS = {
    CARD_NO_TABLE: "Datentraegername ohne Tischnummer (z.B. E01).",
    CARD_NO_TOURNAMENT: "Kein aktives Turnier fuer diese Disziplin.",
    CARD_EMPTY: "Keine Videodateien in der Auswahl.",
    CARD_NO_MARKER: "Karte ohne Marker - vom Admin nicht beschriftet.",
    CARD_WRONG_TOURNAMENT: "Karte gehoert zu einem anderen Turnier.",
}


def _dcim_date_range(card: ScannedCard) -> str:
    """Human date span of a card's DCIM folders, e.g. '22.05.2026'."""
    if not card.dcim_folders:
        return ""
    dates = sorted(f.created for f in card.dcim_folders)
    first, last = dates[0], dates[-1]
    if first.date() == last.date():
        return first.strftime("%d.%m.%Y")
    return f"{first.strftime('%d.%m.%Y')} - {last.strftime('%d.%m.%Y')}"


def badge(state: str) -> str:
    sym, label = _BADGES.get(state, ("?", state))
    return f"{sym} {label}"


def safe_to_remove_text(state: str) -> str:
    return SAFE_TO_REMOVE if state in SAFE_TO_REMOVE_STATES else DO_NOT_REMOVE


def can_release(state: str) -> bool:
    """Only a verified card may be released (defensive button)."""
    return state == CLIENT_VERIFIED


def release_tooltip(state: str) -> str:
    if state == CLIENT_VERIFIED:
        return "Karte freigeben - loest den atomaren Handoff in die Pipeline aus."
    if state == CLIENT_UPLOADING:
        return "Freigabe erst moeglich, wenn der Upload fertig verifiziert ist."
    if state == CLIENT_RELEASED:
        return "Bereits freigegeben - Pipeline laeuft."
    if state == CLIENT_FAILED:
        return "Karte hat einen Fehler. Bitte zuerst erneut hochladen."
    return "Freigabe erst nach erfolgreicher Verifikation moeglich."


def friendly_error(table: str, raw: Optional[str]) -> str:
    """Operator-readable error; the raw detail goes behind a disclosure."""
    return f"Karte {table}: Uebertragungsfehler. Bitte erneut hochladen."


def human_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


@dataclass(frozen=True)
class CardRow:
    """Everything the GUI needs to render one card row."""

    key: str
    table: str
    discipline: str
    state: str
    badge: str
    detail: str
    safe_to_remove: str
    can_release: bool
    release_tooltip: str
    is_error: bool
    error_detail: Optional[str] = None
    stale_alarm: bool = False        # DCIM folders >3 days apart
    date_range: str = ""             # human date span of the DCIM folders


def row_for_progress(p: CardProgress) -> CardRow:
    """Row for a card that has an upload record (in flight or done)."""
    if p.state == CLIENT_FAILED:
        detail = friendly_error(p.table, p.error)
    elif p.state == CLIENT_RELEASED:
        detail = "Pipeline laeuft"
    else:
        detail = (
            f"{p.sent_files}/{p.expected_files} Dateien"
            f" · {human_bytes(p.expected_bytes)}"
        )
    return CardRow(
        key=p.card_uuid,
        table=p.table,
        discipline=p.discipline,
        state=p.state,
        badge=badge(p.state),
        detail=detail,
        safe_to_remove=safe_to_remove_text(p.state),
        can_release=can_release(p.state),
        release_tooltip=release_tooltip(p.state),
        is_error=p.state == CLIENT_FAILED,
        error_detail=p.error if p.state == CLIENT_FAILED else None,
    )


def row_for_scanned(c: ScannedCard) -> CardRow:
    """Row for a freshly scanned card that has not started uploading."""
    table = c.marker.table if c.marker else "?"
    discipline = c.marker.discipline if c.marker else "?"
    if c.status == CARD_READY and c.manifest is not None:
        state = CLIENT_PENDING
        detail = (
            f"0/{c.manifest.total_files} Dateien"
            f" · {human_bytes(c.manifest.total_bytes)}"
        )
        is_error = False
    else:
        # Locked card: show why, never let it look uploadable.
        state = CLIENT_FAILED if c.status != CARD_READY else CLIENT_PENDING
        detail = c.reason or _LOCKED_SCAN_REASONS.get(c.status, "Gesperrt.")
        is_error = True
    # Key on the bare card_uuid (same as row_for_progress) so a card keeps
    # its row identity when it transitions from scanned to uploading.
    key = c.marker.card_uuid if c.marker is not None else c.key
    return CardRow(
        key=key,
        table=table,
        discipline=discipline,
        state=state,
        badge=badge(state) if c.status == CARD_READY else "\U0001f512 gesperrt",
        detail=detail,
        safe_to_remove=SAFE_TO_REMOVE,  # not uploading -> safe to pull
        can_release=False,
        release_tooltip="Karte muss erst hochgeladen und verifiziert werden.",
        is_error=is_error,
        error_detail=c.reason if is_error else None,
        stale_alarm=c.stale_alarm,
        date_range=_dcim_date_range(c),
    )


@dataclass(frozen=True)
class DisciplineSummary:
    """Header counts for one discipline (the '18/30 verifiziert' line)."""

    discipline: str
    expected: int
    verified: int
    released: int
    uploading: int
    waiting: int
    failed: int

    @property
    def done(self) -> int:
        """Cards that passed verification (verified + already released)."""
        return self.verified + self.released

    def header_text(self) -> str:
        parts = [
            f"{self.discipline} - {self.done} / {self.expected} Karten verifiziert",
            f"{self.uploading} hochladen",
            f"{self.waiting} wartend",
        ]
        if self.failed:
            parts.append(f"{self.failed} Fehler")
        return " · ".join(parts)

    @property
    def all_done(self) -> bool:
        """True when every expected card has been released (success state)."""
        return self.expected > 0 and self.released >= self.expected


def summarise(
    discipline: str, states: Iterable[str], *, expected: int,
) -> DisciplineSummary:
    counts = {s: 0 for s in (
        CLIENT_UPLOADING, CLIENT_VERIFIED, CLIENT_RELEASED,
        CLIENT_FAILED, CLIENT_PENDING, CLIENT_INTERRUPTED, CLIENT_CANCELLED,
    )}
    for s in states:
        counts[s] = counts.get(s, 0) + 1
    return DisciplineSummary(
        discipline=discipline,
        expected=expected,
        verified=counts[CLIENT_VERIFIED],
        released=counts[CLIENT_RELEASED],
        uploading=counts[CLIENT_UPLOADING],
        waiting=counts[CLIENT_PENDING] + counts[CLIENT_INTERRUPTED],
        failed=counts[CLIENT_FAILED],
    )
