"""SQLite-backed persistence: Tournaments + Run-History + Phase-Timings.

The database lives next to the operator's config files, by default at
``<VIDEO_PIPELINE_DATA_DIR>/runs.db``. One connection per call (SQLite
handles that fine for our low-traffic Web-UI). Schema migrations are
idempotent ``CREATE TABLE IF NOT EXISTS`` blocks - no Alembic-style
versioning needed at this scale.
"""

from .schema import open_db, ensure_schema, db_path_for
from .tournaments import (
    Tournament,
    create_tournament,
    get_active_tournament,
    get_or_create_active_tournament,
    get_tournament,
    list_tournaments,
    set_active_tournament,
    update_tournament,
)
from .runs import (
    PhaseTiming,
    Run,
    finish_run,
    list_runs,
    record_phase,
    start_run,
)

__all__ = [
    "open_db", "ensure_schema", "db_path_for",
    "Tournament", "create_tournament", "get_active_tournament",
    "get_or_create_active_tournament", "get_tournament",
    "list_tournaments", "set_active_tournament", "update_tournament",
    "PhaseTiming", "Run", "finish_run", "list_runs",
    "record_phase", "start_run",
]
