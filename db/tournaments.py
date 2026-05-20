"""Tournament-CRUD + Active-Tournament-Tracking.

A Tournament is the operator-facing organisational unit (one tournament
per weekend, typically). Every Run is attributed to exactly one
Tournament; when no active Tournament exists for a discipline at the
moment a Run starts, one is auto-created with ``is_auto_created=True``
and a default name like ``Untagged 2026-05-19 Doppel``. The operator
can later rename + fill the metadata via the Web-UI.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pipeline.status_file import now_iso


@dataclass
class Tournament:
    id: Optional[int] = None
    name: str = ""
    date: Optional[str] = None
    location: str = ""
    organizer: str = ""
    disciplines: str = "Doppel,Einzel"
    youtube_channel: str = ""
    visibility_default: str = "private"
    video_prefix: str = ""
    description_template: str = ""
    tags: str = ""
    max_workers: int = 4
    created_at: Optional[str] = None
    is_auto_created: bool = False
    archived_at: Optional[str] = None
    archive_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_auto_created"] = bool(d["is_auto_created"])
        return d


# Columns we accept in create/update payloads. ``id``, ``created_at`` and
# ``is_auto_created`` are server-managed.
_EDITABLE_COLUMNS = (
    "name", "date", "location", "organizer", "disciplines",
    "youtube_channel", "visibility_default", "video_prefix",
    "description_template", "tags", "max_workers",
    "archived_at", "archive_path",
)


def _row_to_tournament(row: sqlite3.Row) -> Tournament:
    return Tournament(
        id=row["id"],
        name=row["name"],
        date=row["date"],
        location=row["location"] or "",
        organizer=row["organizer"] or "",
        disciplines=row["disciplines"] or "Doppel,Einzel",
        youtube_channel=row["youtube_channel"] or "",
        visibility_default=row["visibility_default"] or "private",
        video_prefix=row["video_prefix"] or "",
        description_template=row["description_template"] or "",
        tags=row["tags"] or "",
        max_workers=int(row["max_workers"] or 4),
        created_at=row["created_at"],
        is_auto_created=bool(row["is_auto_created"]),
        archived_at=row["archived_at"],
        archive_path=row["archive_path"],
    )


# ---------------------------------------------------------------------------
# Create / read / update
# ---------------------------------------------------------------------------

def create_tournament(
    conn: sqlite3.Connection,
    name: str,
    *,
    is_auto_created: bool = False,
    **fields_to_set: Any,
) -> Tournament:
    """Insert a new Tournament; return the persisted object."""
    cleaned = {k: v for k, v in fields_to_set.items() if k in _EDITABLE_COLUMNS}
    cleaned["name"] = name

    columns = list(cleaned.keys()) + ["created_at", "is_auto_created"]
    placeholders = ", ".join("?" for _ in columns)
    values = list(cleaned.values()) + [now_iso(), 1 if is_auto_created else 0]

    with conn:
        cur = conn.execute(
            f"INSERT INTO tournaments ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        new_id = cur.lastrowid

    return get_tournament(conn, new_id)  # type: ignore[arg-type]


def get_tournament(conn: sqlite3.Connection, tournament_id: int) -> Optional[Tournament]:
    row = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    return _row_to_tournament(row) if row else None


def list_tournaments(
    conn: sqlite3.Connection,
    *,
    include_archived: bool = True,
) -> List[Tournament]:
    """Return all Tournaments, newest first."""
    if include_archived:
        rows = conn.execute(
            "SELECT * FROM tournaments ORDER BY created_at DESC, id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tournaments WHERE archived_at IS NULL "
            "ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [_row_to_tournament(r) for r in rows]


def update_tournament(
    conn: sqlite3.Connection,
    tournament_id: int,
    **fields_to_set: Any,
) -> Optional[Tournament]:
    """Patch the Tournament row; ignore unknown keys."""
    cleaned = {k: v for k, v in fields_to_set.items() if k in _EDITABLE_COLUMNS}
    if not cleaned:
        return get_tournament(conn, tournament_id)

    set_clause = ", ".join(f"{k} = ?" for k in cleaned)
    values = list(cleaned.values()) + [tournament_id]
    with conn:
        conn.execute(
            f"UPDATE tournaments SET {set_clause} WHERE id = ?", values,
        )
    return get_tournament(conn, tournament_id)


# ---------------------------------------------------------------------------
# Active-Tournament tracking (per discipline)
# ---------------------------------------------------------------------------

def get_active_tournament(
    conn: sqlite3.Connection, discipline: str,
) -> Optional[Tournament]:
    row = conn.execute(
        "SELECT t.* FROM tournaments t "
        "JOIN active_tournament a ON a.tournament_id = t.id "
        "WHERE a.discipline = ?",
        (discipline,),
    ).fetchone()
    return _row_to_tournament(row) if row else None


def set_active_tournament(
    conn: sqlite3.Connection, discipline: str, tournament_id: int,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO active_tournament (discipline, tournament_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(discipline) DO UPDATE SET tournament_id = excluded.tournament_id",
            (discipline, tournament_id),
        )


def get_or_create_active_tournament(
    conn: sqlite3.Connection, discipline: str,
) -> Tournament:
    """Soft-binding entry point: never returns None.

    If an active Tournament exists for the discipline, return it. Otherwise
    auto-create one with ``Untagged YYYY-MM-DD <Discipline>`` and mark it
    as active.
    """
    existing = get_active_tournament(conn, discipline)
    if existing is not None:
        return existing

    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    name = f"Untagged {today} {discipline}"
    created = create_tournament(
        conn, name, is_auto_created=True, disciplines=discipline,
    )
    assert created.id is not None
    set_active_tournament(conn, discipline, created.id)
    return created
