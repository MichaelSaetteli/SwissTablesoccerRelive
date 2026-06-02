"""Upload-card CRUD and state-machine helpers (Issue #15).

One row per SD card upload session. The state field drives the operator
UX in the client tool and the server-side handoff to the watcher.

State transitions::

    uploading  ->  verified   (after upload-finish with manifest match)
    uploading  ->  failed     (manifest mismatch, IO error)
    uploading  ->  interrupted (operator cancels, transport drops)
    interrupted -> uploading  (operator retries; same card_uuid)
    verified   ->  released   (operator clicks Freigeben, atomic rename done)
    verified   ->  failed     (release IO error)

`released` and `cancelled` are terminal. `failed` allows a retry that
moves back to `uploading`.

The DB row is the source of truth - the staging directory on disk is
derived from `staging_path`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from pipeline.status_file import now_iso


# State constants - using strings instead of enum so the SQLite text round-
# trip stays trivial. Keep the set small and explicit; the state-machine
# functions below are the only allowed transitions.
STATE_UPLOADING = "uploading"
STATE_INTERRUPTED = "interrupted"
STATE_VERIFIED = "verified"
STATE_RELEASED = "released"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"

ALL_STATES = (
    STATE_UPLOADING,
    STATE_INTERRUPTED,
    STATE_VERIFIED,
    STATE_RELEASED,
    STATE_FAILED,
    STATE_CANCELLED,
)

# Which transitions are legal. Used by `_transition` to enforce the
# state machine in one place instead of every callsite.
_ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    STATE_UPLOADING: frozenset({STATE_VERIFIED, STATE_FAILED, STATE_INTERRUPTED, STATE_CANCELLED}),
    STATE_INTERRUPTED: frozenset({STATE_UPLOADING, STATE_CANCELLED, STATE_FAILED}),
    STATE_VERIFIED: frozenset({STATE_RELEASED, STATE_FAILED, STATE_CANCELLED}),
    STATE_FAILED: frozenset({STATE_UPLOADING, STATE_CANCELLED}),
    # released and cancelled are terminal.
    STATE_RELEASED: frozenset(),
    STATE_CANCELLED: frozenset(),
}


class UploadCardError(RuntimeError):
    """Raised on invalid state transitions or DB constraint violations."""


@dataclass
class UploadCard:
    id: Optional[int] = None
    card_uuid: str = ""
    tournament_id: int = 0
    table_name: str = ""
    discipline: str = ""
    state: str = STATE_UPLOADING
    expected_files: int = 0
    expected_bytes: int = 0
    received_files: int = 0
    received_bytes: int = 0
    staging_path: str = ""
    final_path: Optional[str] = None
    auto_release: bool = False
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    released_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["auto_release"] = bool(d["auto_release"])
        return d


def _row_to_card(row: sqlite3.Row) -> UploadCard:
    return UploadCard(
        id=row["id"],
        card_uuid=row["card_uuid"],
        tournament_id=row["tournament_id"],
        table_name=row["table_name"],
        discipline=row["discipline"],
        state=row["state"],
        expected_files=int(row["expected_files"] or 0),
        expected_bytes=int(row["expected_bytes"] or 0),
        received_files=int(row["received_files"] or 0),
        received_bytes=int(row["received_bytes"] or 0),
        staging_path=row["staging_path"],
        final_path=row["final_path"],
        auto_release=bool(row["auto_release"]),
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        released_at=row["released_at"],
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_upload_card(
    conn: sqlite3.Connection,
    *,
    card_uuid: str,
    tournament_id: int,
    table_name: str,
    discipline: str,
    expected_files: int,
    expected_bytes: int,
    staging_path: str,
    auto_release: bool = False,
) -> UploadCard:
    """Insert a new card in state=uploading. Raises on duplicate card_uuid."""
    ts = now_iso()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO upload_cards ("
                "  card_uuid, tournament_id, table_name, discipline, state, "
                "  expected_files, expected_bytes, staging_path, "
                "  auto_release, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    card_uuid, tournament_id, table_name, discipline,
                    STATE_UPLOADING, expected_files, expected_bytes,
                    staging_path, 1 if auto_release else 0, ts, ts,
                ),
            )
            new_id = cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise UploadCardError(
            f"card_uuid {card_uuid!r} already exists - resume not supported "
            f"via re-create; use update_progress / _transition instead"
        ) from exc
    fetched = get_upload_card(conn, new_id)
    assert fetched is not None
    return fetched


def get_upload_card(
    conn: sqlite3.Connection, card_id: int,
) -> Optional[UploadCard]:
    row = conn.execute(
        "SELECT * FROM upload_cards WHERE id = ?", (card_id,)
    ).fetchone()
    return _row_to_card(row) if row else None


def get_upload_card_by_uuid(
    conn: sqlite3.Connection, card_uuid: str,
) -> Optional[UploadCard]:
    row = conn.execute(
        "SELECT * FROM upload_cards WHERE card_uuid = ?", (card_uuid,)
    ).fetchone()
    return _row_to_card(row) if row else None


def list_upload_cards(
    conn: sqlite3.Connection,
    *,
    tournament_id: Optional[int] = None,
    discipline: Optional[str] = None,
    state: Optional[str] = None,
) -> List[UploadCard]:
    """List cards, newest first. All filters optional."""
    where = []
    params: List[Any] = []
    if tournament_id is not None:
        where.append("tournament_id = ?")
        params.append(tournament_id)
    if discipline is not None:
        where.append("discipline = ?")
        params.append(discipline)
    if state is not None:
        where.append("state = ?")
        params.append(state)
    sql = "SELECT * FROM upload_cards"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_card(r) for r in rows]


def update_progress(
    conn: sqlite3.Connection,
    card_id: int,
    *,
    received_files: int,
    received_bytes: int,
) -> Optional[UploadCard]:
    """Bump the progress counters without touching state."""
    with conn:
        conn.execute(
            "UPDATE upload_cards SET "
            "  received_files = ?, received_bytes = ?, updated_at = ? "
            "WHERE id = ?",
            (received_files, received_bytes, now_iso(), card_id),
        )
    return get_upload_card(conn, card_id)


def transition(
    conn: sqlite3.Connection,
    card_id: int,
    new_state: str,
    *,
    error_message: Optional[str] = None,
    final_path: Optional[str] = None,
) -> UploadCard:
    """Atomic state-machine transition. Raises if not legal.

    `final_path` is only relevant for the verified->released transition.
    `error_message` clears on any non-failed transition (so retries do not
    leak the previous error into the UI).
    """
    current = get_upload_card(conn, card_id)
    if current is None:
        raise UploadCardError(f"upload card id={card_id} not found")
    if new_state not in ALL_STATES:
        raise UploadCardError(f"unknown state {new_state!r}")
    legal = _ALLOWED_TRANSITIONS.get(current.state, frozenset())
    if new_state not in legal:
        raise UploadCardError(
            f"illegal transition {current.state} -> {new_state} "
            f"for card id={card_id}; allowed: {sorted(legal)}"
        )
    ts = now_iso()
    released_at = ts if new_state == STATE_RELEASED else current.released_at
    # Clear error message unless we are transitioning into `failed`.
    msg = error_message if new_state == STATE_FAILED else None
    with conn:
        conn.execute(
            "UPDATE upload_cards SET "
            "  state = ?, error_message = ?, updated_at = ?, "
            "  released_at = ?, final_path = COALESCE(?, final_path) "
            "WHERE id = ?",
            (new_state, msg, ts, released_at, final_path, card_id),
        )
    updated = get_upload_card(conn, card_id)
    assert updated is not None
    return updated


def delete_upload_card(conn: sqlite3.Connection, card_id: int) -> None:
    """Hard delete - only callers are cleanup / test fixtures."""
    with conn:
        conn.execute("DELETE FROM upload_cards WHERE id = ?", (card_id,))
