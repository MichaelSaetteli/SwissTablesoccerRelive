"""Admin CLI to label SD cards for the STS-Upload client tool (Issue #15).

Writes a ``.sts-card.json`` marker to the root of an SD card. The marker
binds the physical card to one ``(tournament, discipline, table)`` triple,
so the operator-side upload tool cannot mis-assign the card to a wrong
table.

Usage::

    python -m scripts.prepare_card \\
        --card E:\\ \\
        --tournament-id 42 \\
        --tournament-name "Seetal 2026 STS2" \\
        --discipline Einzel \\
        --table ET01

The marker contents intentionally include the tournament name (not just
id) so a forgotten card from last season is human-recognisable when an
operator finds it months later.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Sequence

sys.stdout.reconfigure(encoding="utf-8")

MARKER_NAME = ".sts-card.json"
ALLOWED_DISCIPLINES = ("Doppel", "Einzel")


def _make_marker(
    *,
    tournament_id: int,
    tournament_name: str,
    discipline: str,
    table: str,
) -> dict:
    table_upper = table.strip().upper()
    if not table_upper.startswith("ET") or len(table_upper) != 4:
        raise SystemExit(
            f"--table must be ETxx (uppercase, two-digit), got {table!r}"
        )
    if discipline not in ALLOWED_DISCIPLINES:
        raise SystemExit(
            f"--discipline must be one of {ALLOWED_DISCIPLINES}, "
            f"got {discipline!r}"
        )
    return {
        "version": 1,
        "card_uuid": uuid.uuid4().hex,
        "tournament_id": tournament_id,
        "tournament_name": tournament_name,
        "discipline": discipline,
        "table": table_upper,
    }


def _write_marker(card_root: Path, marker: dict, *, force: bool) -> Path:
    if not card_root.is_dir():
        raise SystemExit(f"--card path is not a directory: {card_root}")
    target = card_root / MARKER_NAME
    if target.exists() and not force:
        raise SystemExit(
            f"{target} already exists; pass --force to overwrite. "
            f"(Existing markers are kept to avoid relabeling a card that "
            f"was already prepared for another table.)"
        )
    # Atomic write so a crash mid-write does not leave a half-marker.
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(marker, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Write a .sts-card.json marker to an SD card root.",
    )
    parser.add_argument("--card", type=Path, required=True,
                        help="Path to the SD card mount root, e.g. E:\\")
    parser.add_argument("--tournament-id", type=int, required=True)
    parser.add_argument("--tournament-name", type=str, required=True)
    parser.add_argument("--discipline", type=str, required=True,
                        choices=ALLOWED_DISCIPLINES)
    parser.add_argument("--table", type=str, required=True,
                        help="Table identifier, e.g. ET01")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing marker.")
    args = parser.parse_args(list(argv))

    marker = _make_marker(
        tournament_id=args.tournament_id,
        tournament_name=args.tournament_name,
        discipline=args.discipline,
        table=args.table,
    )
    target = _write_marker(args.card, marker, force=args.force)
    print(f"Wrote {target}")
    print(f"  card_uuid={marker['card_uuid']}")
    print(f"  tournament={marker['tournament_name']} (id={marker['tournament_id']})")
    print(f"  discipline={marker['discipline']}")
    print(f"  table={marker['table']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
