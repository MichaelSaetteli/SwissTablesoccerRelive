"""Read and validate the ``.sts-card.json`` SD-card marker.

The marker is written by the admin CLI (``scripts/prepare_card.py``)
before the tournament and is the **source of truth** for which
tournament / discipline / table a card belongs to. The operator never
types any of this - the tool reads it from the card (Issue #15, §6a:
"Tisch und Disziplin kommen aus dem Marker, nie aus Operator-Eingabe").

Cards without a marker are *not* labelled by the client; they are shown
locked in the UI with an explanation. That is why ``read_marker`` raises
instead of inventing defaults.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Must match scripts/prepare_card.py (marker writer).
MARKER_NAME = ".sts-card.json"
ALLOWED_DISCIPLINES = ("Doppel", "Einzel")
# INV-5 ETxx convention, mirrors upload_staging.paths.TABLE_NAME_RE.
TABLE_NAME_RE = re.compile(r"^ET\d{2}$")


class MarkerError(ValueError):
    """Raised when a marker is missing, malformed or fails validation."""


@dataclass(frozen=True)
class CardMarker:
    """Parsed, validated contents of a ``.sts-card.json`` file."""

    card_uuid: str
    tournament_id: int
    tournament_name: str
    discipline: str
    table: str
    version: int = 1


def marker_path(card_root: Path) -> Path:
    return Path(card_root) / MARKER_NAME


def has_marker(card_root: Path) -> bool:
    """True if the card root carries a marker file (cheap stat)."""
    return marker_path(card_root).is_file()


def read_marker(card_root: Path) -> CardMarker:
    """Read + validate the marker at ``card_root``.

    Raises ``MarkerError`` with a human-readable message if the marker is
    missing, not valid JSON, or has a bad field. The message is meant to
    be safe to show the operator (the UI may surface it on a locked row).
    """
    path = marker_path(card_root)
    if not path.is_file():
        raise MarkerError(
            f"Keine {MARKER_NAME} auf dieser Karte. Die Karte wurde nicht "
            f"vom Admin beschriftet und kann nicht hochgeladen werden."
        )
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise MarkerError(f"{MARKER_NAME} ist nicht lesbar: {exc}") from exc

    if not isinstance(raw, dict):
        raise MarkerError(f"{MARKER_NAME} hat ein unerwartetes Format.")

    return _validate(raw)


def _validate(raw: dict) -> CardMarker:
    missing = [
        key
        for key in ("card_uuid", "tournament_id", "tournament_name",
                    "discipline", "table")
        if key not in raw
    ]
    if missing:
        raise MarkerError(
            f"{MARKER_NAME} fehlen Felder: {', '.join(missing)}."
        )

    card_uuid = str(raw["card_uuid"]).strip()
    if not card_uuid:
        raise MarkerError(f"{MARKER_NAME}: card_uuid ist leer.")

    try:
        tournament_id = int(raw["tournament_id"])
    except (TypeError, ValueError) as exc:
        raise MarkerError(
            f"{MARKER_NAME}: tournament_id ist keine Zahl."
        ) from exc

    discipline = str(raw["discipline"]).strip()
    if discipline not in ALLOWED_DISCIPLINES:
        raise MarkerError(
            f"{MARKER_NAME}: discipline {discipline!r} ist ungueltig "
            f"(erlaubt: {', '.join(ALLOWED_DISCIPLINES)})."
        )

    table = str(raw["table"]).strip().upper()
    if not TABLE_NAME_RE.match(table):
        raise MarkerError(
            f"{MARKER_NAME}: table {table!r} entspricht nicht dem "
            f"ETxx-Format (zweistellig, z.B. ET01)."
        )

    version = int(raw.get("version", 1) or 1)

    return CardMarker(
        card_uuid=card_uuid,
        tournament_id=tournament_id,
        tournament_name=str(raw["tournament_name"]),
        discipline=discipline,
        table=table,
        version=version,
    )
