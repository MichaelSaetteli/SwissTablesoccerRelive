"""Derive a card's table / discipline-hint / stable id from its volume.

The new ingest model (2026-06-02, supersedes the marker-only rule in
``SESSION_HANDOFF.md`` §6a) makes the **Windows volume name** the source
of truth for the table and the operator's batch choice the source of
truth for the discipline. A card no longer needs a pre-written
``.sts-card.json`` marker.

* Table: the trailing digits of the volume name (``E01`` -> ``ET01``).
  The name is intrinsic to the card and independent of the drive letter,
  so the same card maps to the same table in any slot.
* Discipline hint: the leading letter (``E`` -> Einzel, ``D`` -> Doppel)
  is only a *suggestion*; the operator's batch selection (overridable per
  card) is authoritative.
* Card id: the volume serial number (stable per physical card). Falls
  back to the volume name, then the mount path, so resume still works
  when a serial cannot be read.

All functions are pure and Qt-free.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# A table is ``ET`` + two digits (INV-5, mirrors marker.TABLE_NAME_RE).
_TRAILING_DIGITS_RE = re.compile(r"(\d{1,3})\s*$")
_LEADING_LETTER_RE = re.compile(r"^\s*([A-Za-z])")

_DISCIPLINE_BY_LETTER = {"E": "Einzel", "D": "Doppel"}


def table_from_label(label: Optional[str]) -> Optional[str]:
    """Map a volume name to an ``ETxx`` table, or ``None`` if it has no number.

    ``E01`` -> ``ET01``, ``D12`` -> ``ET12``, ``STS_07`` -> ``ET07``. Numbers
    outside 1..99 (the two-digit ``ETxx`` range) yield ``None`` so a stray
    label never produces a bogus table.
    """
    if not label:
        return None
    match = _TRAILING_DIGITS_RE.search(label.strip())
    if not match:
        return None
    number = int(match.group(1))
    if not 1 <= number <= 99:
        return None
    return f"ET{number:02d}"


def discipline_hint_from_label(label: Optional[str]) -> Optional[str]:
    """Return the discipline suggested by the leading letter, or ``None``.

    Only a hint - the operator's batch choice is authoritative. ``E`` ->
    Einzel, ``D`` -> Doppel; anything else -> ``None``.
    """
    if not label:
        return None
    match = _LEADING_LETTER_RE.match(label)
    if not match:
        return None
    return _DISCIPLINE_BY_LETTER.get(match.group(1).upper())


def card_uuid_from(
    *,
    serial: Optional[str] = None,
    label: Optional[str] = None,
    root: Optional[Path] = None,
) -> str:
    """Build a stable per-card id, independent of discipline and slot.

    Prefers the volume serial (truly card-intrinsic). Falls back to the
    volume name, then the mount path, so a card without a readable serial
    still gets a stable key for resume/dedupe. The id deliberately does
    **not** include the discipline, so changing a card's discipline keeps
    its identity (and its row) stable.
    """
    if serial:
        return f"vol-{serial}"
    if label:
        return f"label-{label}"
    return f"path-{Path(root) if root is not None else '?'}"
