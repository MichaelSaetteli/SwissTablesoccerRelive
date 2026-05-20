"""Manual pipeline control (Auftrag 4 / Dashboard Modul 2 + Briefing M2).

Three concerns:

* **Pause/Resume per discipline** - a single boolean per discipline
  decides whether ``run_pipeline`` is allowed to start. The watcher
  bails early when paused; manual API triggers return 409.
* **Per-Job restart** - re-execute one or more phases for an existing
  Run record. Useful when ffmpeg failed for a single folder and the
  operator wants to retry without re-processing everything.
* **Priority** - mutable integer on the Run; smaller = earlier in any
  bulk operation. M2 v0 uses it as informational metadata; a real
  worker-queue that respects it lands in M2 v1.

The pipeline phases that can be restarted (in stream-copy mode) are:

  rename   re-rename video_*.mp4 (idempotent + fast)
  merge    re-run ffmpeg concat -> overwrites the output file
  output   re-validate the existing output file (sanity check)

``move`` and ``organize`` are not supported as restart targets because
the file is already long-since gone from eingang and the operator
typically does not want to undo that.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pipeline.status_file import now_iso


# Phases that can serve as a restart target.
RESTARTABLE_PHASES = ("rename", "merge", "output")


# ---------------------------------------------------------------------------
# Pause / Resume per discipline
# ---------------------------------------------------------------------------

def is_paused(conn: sqlite3.Connection, discipline: str) -> bool:
    row = conn.execute(
        "SELECT paused FROM pipeline_control WHERE discipline = ?",
        (discipline,),
    ).fetchone()
    return bool(row and row["paused"])


def set_paused(
    conn: sqlite3.Connection, discipline: str, paused: bool,
) -> None:
    """Upsert the per-discipline pause flag."""
    with conn:
        conn.execute(
            "INSERT INTO pipeline_control (discipline, paused, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(discipline) DO UPDATE SET "
            "  paused = excluded.paused, "
            "  updated_at = excluded.updated_at",
            (discipline, 1 if paused else 0, now_iso()),
        )


# ---------------------------------------------------------------------------
# Priority + paused flag on individual Runs
# ---------------------------------------------------------------------------

def set_run_priority(
    conn: sqlite3.Connection, run_id: int, priority: int,
) -> bool:
    """Update a Run's priority. Returns False if the run does not exist."""
    with conn:
        cur = conn.execute(
            "UPDATE runs SET priority = ? WHERE id = ?",
            (int(priority), run_id),
        )
    return cur.rowcount > 0


def set_run_paused(
    conn: sqlite3.Connection, run_id: int, paused: bool,
) -> bool:
    """Mark/unmark a single Run as paused. Returns False if not found."""
    with conn:
        cur = conn.execute(
            "UPDATE runs SET paused = ? WHERE id = ?",
            (1 if paused else 0, run_id),
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Restart helpers
# ---------------------------------------------------------------------------

@dataclass
class RestartTarget:
    """Resolved target for a Run-restart request."""
    run_id: int
    tournament_id: int
    discipline: str
    folder_name: str
    work_path: str          # absolute path to the work/<folder> directory
    output_filename: str
    from_phase: str         # one of RESTARTABLE_PHASES


def resolve_restart_target(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    work_root: str,
    from_phase: str = "merge",
) -> Optional[RestartTarget]:
    """Read the Run row + decide the absolute work-folder path.

    Returns ``None`` when the run does not exist or when *from_phase*
    is not a known restart target.
    """
    if from_phase not in RESTARTABLE_PHASES:
        return None

    row = conn.execute(
        "SELECT id, tournament_id, discipline, folder_name, output_filename "
        "FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None

    return RestartTarget(
        run_id=row["id"],
        tournament_id=row["tournament_id"],
        discipline=row["discipline"],
        folder_name=row["folder_name"],
        work_path=f"{work_root.rstrip('/')}/{row['folder_name']}",
        output_filename=row["output_filename"] or "",
        from_phase=from_phase,
    )


def mark_restart_started(
    conn: sqlite3.Connection, run_id: int,
) -> None:
    """Move an existing Run row back to ``state='running'`` and clear error."""
    with conn:
        conn.execute(
            "UPDATE runs SET state = 'running', error = NULL, "
            " started_at = ?, finished_at = NULL "
            "WHERE id = ?",
            (now_iso(), run_id),
        )


def clear_phases_for_restart(
    conn: sqlite3.Connection, run_id: int, from_phase: str,
) -> None:
    """Remove phase rows that will be overwritten by the restart.

    Older phase records for the same Run stay in place so the operator
    can still see the original timings of phases that were not redone.
    """
    # Phases after & including from_phase are about to be re-executed.
    redo = {
        "rename": ("rename", "merge", "output"),
        "merge":  ("merge", "output"),
        "output": ("output",),
    }[from_phase]
    placeholders = ", ".join("?" * len(redo))
    with conn:
        conn.execute(
            f"DELETE FROM run_phases "
            f"WHERE run_id = ? AND phase IN ({placeholders})",
            (run_id, *redo),
        )


# ---------------------------------------------------------------------------
# Bulk listing for the UI / API
# ---------------------------------------------------------------------------

def list_jobs(
    conn: sqlite3.Connection,
    *,
    discipline: str,
    states: Optional[List[str]] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Return Runs filtered by state, ordered for the operator's UI.

    Sort order mirrors what the dashboard shows: live jobs first
    (running > queued > paused > error), then completed by recency.
    """
    sql = "SELECT * FROM runs WHERE discipline = ?"
    args: List[Any] = [discipline]
    if states:
        placeholders = ", ".join("?" * len(states))
        sql += f" AND state IN ({placeholders})"
        args.extend(states)
    sql += (
        " ORDER BY "
        " CASE state "
        "   WHEN 'running' THEN 0 "
        "   WHEN 'queued'  THEN 1 "
        "   WHEN 'paused'  THEN 2 "
        "   WHEN 'error'   THEN 3 "
        "   ELSE 4 "
        " END, "
        " priority ASC, started_at DESC LIMIT ?"
    )
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]
