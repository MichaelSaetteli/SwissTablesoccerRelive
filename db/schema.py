"""SQLite schema + connection helper.

One DB per deployment, located alongside the operator's configs. We use
``check_same_thread=False`` so the Flask request thread, the watcher
thread and the archive thread can all read/write through the same
``Connection`` if they want (each call gets its own cursor). SQLite's
own locking handles concurrent writes correctly for our load.

WAL mode is on so readers never block the writer (typical case: Web-UI
polling history while a run is in progress).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tournaments (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL,
    date                 TEXT,            -- ISO 8601 date or NULL
    location             TEXT    DEFAULT '',
    organizer            TEXT    DEFAULT '',
    disciplines          TEXT    DEFAULT 'Doppel,Einzel',  -- CSV
    youtube_channel      TEXT    DEFAULT '',
    visibility_default   TEXT    DEFAULT 'private',
    video_prefix         TEXT    DEFAULT '',
    description_template TEXT    DEFAULT '',
    tags                 TEXT    DEFAULT '',
    max_workers          INTEGER DEFAULT 4,
    created_at           TEXT    NOT NULL,
    is_auto_created      INTEGER NOT NULL DEFAULT 0,
    archived_at          TEXT,
    archive_path         TEXT
);

CREATE TABLE IF NOT EXISTS active_tournament (
    discipline    TEXT    PRIMARY KEY,    -- 'Doppel' or 'Einzel'
    tournament_id INTEGER NOT NULL,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    discipline      TEXT    NOT NULL,
    folder_name     TEXT    NOT NULL,    -- 'ET01', 'ET01_1', ...
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    state           TEXT    NOT NULL DEFAULT 'running',
    input_bytes     INTEGER DEFAULT 0,
    output_bytes    INTEGER DEFAULT 0,
    output_filename TEXT    DEFAULT '',
    error           TEXT,
    -- M2: priority + paused flag enable manual queue control.
    priority        INTEGER NOT NULL DEFAULT 50,
    paused          INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_tournament ON runs(tournament_id);
CREATE INDEX IF NOT EXISTS idx_runs_discipline ON runs(discipline);
CREATE INDEX IF NOT EXISTS idx_runs_started    ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_runs_priority   ON runs(priority);

CREATE TABLE IF NOT EXISTS run_phases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    phase       TEXT    NOT NULL,        -- 'move'|'organize'|'rename'|'merge'
    started_at  TEXT    NOT NULL,
    finished_at TEXT    NOT NULL,
    duration_s  REAL    NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_run_phases_run ON run_phases(run_id);

-- M2: one row per discipline holding manual-control flags.
-- 'paused=1' makes run_pipeline refuse to start until resumed.
CREATE TABLE IF NOT EXISTS pipeline_control (
    discipline TEXT PRIMARY KEY,
    paused     INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS archives (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    archive_path    TEXT    NOT NULL,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    state           TEXT    NOT NULL,    -- 'running'|'done'|'error'
    bytes_copied    INTEGER DEFAULT 0,
    files_verified  INTEGER DEFAULT 0,
    files_total     INTEGER DEFAULT 0,
    error           TEXT,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
);
"""


# A single connection per process is cheaper than reopening - SQLite with
# WAL serialises writes internally and we are very low-traffic.
_conn_lock = threading.Lock()
_connections: dict = {}


def db_path_for(config_dir: Path) -> Path:
    """Default DB path: alongside the operator's configs."""
    return Path(config_dir) / "runs.db"


def open_db(path: Path) -> sqlite3.Connection:
    """Return a thread-safe connection to *path*, initialised once per path."""
    path = Path(path)
    key = str(path.resolve())
    with _conn_lock:
        conn = _connections.get(key)
        if conn is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(path),
                check_same_thread=False,
                isolation_level=None,        # autocommit; we use BEGIN/COMMIT explicitly
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            ensure_schema(conn)
            _connections[key] = conn
        return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: create all tables/indexes if missing, then run migrations.

    SQLite's ``CREATE TABLE IF NOT EXISTS`` does not add new columns to
    an existing table, so we follow up with ``ALTER TABLE ... ADD COLUMN``
    for any column an older deployment may not yet have. Each ALTER is
    guarded by a PRAGMA check so we stay idempotent.
    """
    conn.executescript(SCHEMA_SQL)

    # ---- Migration: M2 adds priority + paused on runs ----
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
    if "priority" not in existing_cols:
        conn.execute(
            "ALTER TABLE runs ADD COLUMN priority INTEGER NOT NULL DEFAULT 50"
        )
    if "paused" not in existing_cols:
        conn.execute(
            "ALTER TABLE runs ADD COLUMN paused INTEGER NOT NULL DEFAULT 0"
        )


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------

def _reset_connections_for_tests() -> None:
    """Clear the global connection cache without closing the connections.

    Background daemon threads (e.g. bulk-restart workers) may still hold
    references and call ``.execute`` after teardown - closing the cache
    here would race them and raise ``sqlite3.ProgrammingError``. Letting
    Python's GC reclaim the connection once nothing references it (the
    tmp_path DB file is deleted with the test) is both safe and silent.
    """
    with _conn_lock:
        _connections.clear()
