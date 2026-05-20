"""Run + Phase-Timing persistence.

One ``Run`` row per (folder, pipeline-run) — i.e. an `ET01` folder
producing one merged output is one Run. Folders that get split by
``organize_folders`` into ETxx_1, ETxx_2 produce one Run *each*.

``record_phase`` adds a ``RunPhase`` entry every time a pipeline phase
finishes; from these we build the per-job phase-breakdown that the
dashboard displays (Modul 3.1).
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from pipeline.status_file import now_iso


@dataclass
class PhaseTiming:
    phase: str
    started_at: str
    finished_at: str
    duration_s: float


@dataclass
class Run:
    id: Optional[int] = None
    tournament_id: int = 0
    discipline: str = ""
    folder_name: str = ""
    started_at: str = ""
    finished_at: Optional[str] = None
    state: str = "running"           # running | done | error
    input_bytes: int = 0
    output_bytes: int = 0
    output_filename: str = ""
    error: Optional[str] = None
    phases: List[PhaseTiming] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["phases"] = [asdict(p) for p in self.phases]
        return d


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def start_run(
    conn: sqlite3.Connection,
    *,
    tournament_id: int,
    discipline: str,
    folder_name: str,
    input_bytes: int = 0,
) -> Run:
    """Insert a Run row with state='running' and return it."""
    started = now_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO runs "
            "(tournament_id, discipline, folder_name, started_at, "
            " state, input_bytes) "
            "VALUES (?, ?, ?, ?, 'running', ?)",
            (tournament_id, discipline, folder_name, started, input_bytes),
        )
        run_id = cur.lastrowid

    return Run(
        id=run_id,
        tournament_id=tournament_id,
        discipline=discipline,
        folder_name=folder_name,
        started_at=started,
        state="running",
        input_bytes=input_bytes,
    )


def record_phase(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    phase: str,
    started_at: str,
    duration_s: float,
) -> None:
    """Append a RunPhase entry. *started_at* is when the phase began;
    we compute finished_at from started_at + duration on insert."""
    from datetime import datetime
    # started_at is ISO with timezone; parse, add duration, re-format
    try:
        dt_start = datetime.fromisoformat(started_at)
        dt_end = dt_start.timestamp() + duration_s
        finished_at = datetime.fromtimestamp(
            dt_end, tz=dt_start.tzinfo
        ).isoformat(timespec="seconds")
    except (ValueError, AttributeError):
        finished_at = now_iso()

    with conn:
        conn.execute(
            "INSERT INTO run_phases "
            "(run_id, phase, started_at, finished_at, duration_s) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, phase, started_at, finished_at, duration_s),
        )


def finish_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    state: str,
    output_bytes: int = 0,
    output_filename: str = "",
    error: Optional[str] = None,
) -> None:
    """Mark the Run finished. State must be ``'done'`` or ``'error'``."""
    if state not in ("done", "error"):
        raise ValueError(f"invalid run state: {state!r}")
    with conn:
        conn.execute(
            "UPDATE runs SET "
            " finished_at = ?, state = ?, output_bytes = ?, "
            " output_filename = ?, error = ? "
            "WHERE id = ?",
            (now_iso(), state, output_bytes, output_filename, error, run_id),
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def _row_to_run(conn: sqlite3.Connection, row: sqlite3.Row) -> Run:
    phases = [
        PhaseTiming(
            phase=p["phase"],
            started_at=p["started_at"],
            finished_at=p["finished_at"],
            duration_s=float(p["duration_s"]),
        )
        for p in conn.execute(
            "SELECT phase, started_at, finished_at, duration_s "
            "FROM run_phases WHERE run_id = ? ORDER BY started_at",
            (row["id"],),
        )
    ]
    return Run(
        id=row["id"],
        tournament_id=row["tournament_id"],
        discipline=row["discipline"],
        folder_name=row["folder_name"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        state=row["state"],
        input_bytes=int(row["input_bytes"] or 0),
        output_bytes=int(row["output_bytes"] or 0),
        output_filename=row["output_filename"] or "",
        error=row["error"],
        phases=phases,
    )


def list_runs(
    conn: sqlite3.Connection,
    *,
    discipline: Optional[str] = None,
    tournament_id: Optional[int] = None,
    limit: int = 100,
) -> List[Run]:
    """Return Runs, newest first, optionally filtered."""
    sql = "SELECT * FROM runs"
    args: List[Any] = []
    clauses = []
    if discipline is not None:
        clauses.append("discipline = ?")
        args.append(discipline)
    if tournament_id is not None:
        clauses.append("tournament_id = ?")
        args.append(tournament_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY started_at DESC LIMIT ?"
    args.append(limit)

    return [_row_to_run(conn, r) for r in conn.execute(sql, args)]
