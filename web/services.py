"""High-level operations the Flask routes call into.

Keeps the route handlers thin so the same logic is unit-testable without
spinning up a Flask test client.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pipeline.config_loader import (
    FilenameConstants,
    PipelineConfig,
    save_config,
)
from watcher.pipeline_runner import (
    PipelineRunError,
    run_pipeline,
)
from watcher.status import (
    PipelineStatus,
    StatusWriter,
    read_status,
    status_path_for,
)
from youtube.metadata_builder import (
    VideoMetadata,
    build_upload_batch,
    quota_hint,
)
from youtube.upload_status import (
    UploadStatus,
    UploadStatusWriter,
    read_upload_status,
    upload_status_path_for,
)


Runner = Callable[[PipelineConfig], StatusWriter]


# ---------------------------------------------------------------------------
# Status / files
# ---------------------------------------------------------------------------

def get_status(config: PipelineConfig) -> PipelineStatus:
    """Return the current persisted status (or a fresh idle one)."""
    existing = read_status(status_path_for(config))
    if existing is not None:
        return existing
    return PipelineStatus(discipline=config.discipline)


def list_output_files(config: PipelineConfig) -> List[Dict[str, object]]:
    """List ``output/*.mp4`` files with size and mtime, sorted by name."""
    output_dir = config.paths.output
    if not output_dir.is_dir():
        return []
    files = sorted(
        p for p in output_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".mp4"
    )
    return [
        {
            "name": p.name,
            "size_bytes": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        }
        for p in files
    ]


def resolve_output_file(config: PipelineConfig, filename: str) -> Optional[Path]:
    """Safely resolve *filename* inside the configured output directory.

    Returns ``None`` if the file does not exist or escapes the directory
    (path-traversal protection).
    """
    output_dir = config.paths.output.resolve()
    candidate = (output_dir / filename).resolve()
    try:
        candidate.relative_to(output_dir)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


# ---------------------------------------------------------------------------
# Manual pipeline trigger
# ---------------------------------------------------------------------------

def start_run_async(
    config: PipelineConfig,
    runner: Runner = run_pipeline,
) -> threading.Thread:
    """Spawn a daemon thread that runs the pipeline once.

    The per-discipline lock inside ``run_pipeline`` is the source of truth
    for "is a run already in progress" - if it is, the thread raises
    ``PipelineRunError`` and exits silently (the failure is also recorded
    in the status file).
    """
    def _target() -> None:
        try:
            runner(config)
        except PipelineRunError:
            # Already-running case is not an error worth crashing the thread.
            pass

    thread = threading.Thread(
        target=_target,
        name=f"manual-run-{config.discipline}",
        daemon=True,
    )
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# YouTube config (Schritt 3 only persists; upload comes in Schritt 4)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Filename constants (operator-defined per processing run)
# ---------------------------------------------------------------------------

FILENAME_FIELDS = (
    "jahr",
    "sts_nummer",
    "turniername",
    "disziplin",
    "part",
)


def get_filename_config(config: PipelineConfig) -> Dict[str, str]:
    return config.filename_constants.as_dict()


def update_filename_config(
    config: PipelineConfig, payload: Dict[str, object],
) -> Dict[str, str]:
    """Merge *payload* into ``config.filename_constants`` and persist atomically.

    Unknown keys are ignored so a malformed front-end payload cannot
    pollute the config. Empty strings are allowed (e.g. ``part`` is
    intentionally optional).
    """
    current = config.filename_constants.as_dict()
    for key in FILENAME_FIELDS:
        if key in payload:
            value = payload[key]
            current[key] = "" if value is None else str(value).strip()
    config.filename_constants = FilenameConstants(**current)
    save_config(config)
    return get_filename_config(config)


YOUTUBE_FIELDS = (
    "tournament_name",
    "date",
    "location",
    "title_template",
    "description_template",
    "playlist_id",
    "playlist_create_new",
    "playlist_new_title",
)


def get_youtube_config(config: PipelineConfig) -> Dict[str, object]:
    return {key: config.youtube.get(key, "") for key in YOUTUBE_FIELDS}


def update_youtube_config(
    config: PipelineConfig, payload: Dict[str, object],
) -> Dict[str, object]:
    """Merge *payload* into ``config.youtube`` and persist atomically.

    Unknown keys are ignored on purpose so a malformed front-end payload
    cannot pollute the config with arbitrary data.
    """
    cleaned: Dict[str, object] = {}
    for key in YOUTUBE_FIELDS:
        if key in payload:
            value = payload[key]
            if key == "playlist_create_new":
                cleaned[key] = bool(value)
            else:
                cleaned[key] = "" if value is None else str(value)
    config.youtube.update(cleaned)
    save_config(config)
    return get_youtube_config(config)


# ---------------------------------------------------------------------------
# YouTube upload (preview + run)
# ---------------------------------------------------------------------------

# Type aliases for injection points (tests pass fakes here).
ServiceFactory = Callable[[PipelineConfig], object]
UploadRunner = Callable[[object, PipelineConfig, UploadStatusWriter], object]


def get_upload_preview(config: PipelineConfig) -> Dict[str, object]:
    """Return a JSON-serialisable preview of titles + descriptions.

    The Web-Interface shows this list before the operator commits to an
    upload so they can sanity-check the generated metadata.
    """
    files = list_output_files(config)
    file_names = [str(entry["name"]) for entry in files]
    metadata: List[VideoMetadata] = build_upload_batch(config, file_names)
    return {
        "files": [m.to_dict() for m in metadata],
        "quota_hint": quota_hint(len(file_names)),
        "total": len(file_names),
    }


def get_upload_status(config: PipelineConfig) -> UploadStatus:
    existing = read_upload_status(upload_status_path_for(config))
    return existing or UploadStatus(discipline=config.discipline)


def _default_service_factory(config: PipelineConfig) -> object:
    """Build a real Google YouTube service from the saved token."""
    from youtube.oauth_setup import build_youtube_service, load_credentials

    if config.source_path is None:
        raise RuntimeError("config.source_path is None - cannot locate token")
    token_path = config.source_path.parent / "youtube_token.json"
    creds = load_credentials(token_path)
    if creds is None:
        raise RuntimeError(
            f"No valid YouTube credentials at {token_path}. Run "
            f"'python -m youtube.oauth_setup <client_secrets.json> {token_path}' "
            f"on a machine with a browser, then copy the token to the NAS."
        )
    return build_youtube_service(creds)


def _default_upload_runner(
    service: object,
    config: PipelineConfig,
    writer: UploadStatusWriter,
) -> object:
    from youtube.youtube_uploader import UploadError, upload_batch

    try:
        return upload_batch(service, config, writer=writer)
    except UploadError as exc:
        # "Already running" is reported via the status file by upload_batch
        # itself - swallow here so the daemon thread exits cleanly without
        # an unhandled stack trace in the logs.
        if "already running" in str(exc):
            return None
        raise


def start_upload_async(
    config: PipelineConfig,
    *,
    service_factory: ServiceFactory = _default_service_factory,
    upload_runner: UploadRunner = _default_upload_runner,
) -> threading.Thread:
    """Spawn a daemon thread that uploads every output file to YouTube.

    *service_factory* and *upload_runner* are injection points so tests
    can run the full Web flow without hitting Google.
    """
    writer = UploadStatusWriter(
        upload_status_path_for(config), config.discipline,
    )

    def _target() -> None:
        try:
            service = service_factory(config)
            upload_runner(service, config, writer)
        except Exception as exc:
            writer.fail(f"{type(exc).__name__}: {exc}")
            writer.append_log(f"Upload aborted: {exc}")

    thread = threading.Thread(
        target=_target,
        name=f"upload-{config.discipline}",
        daemon=True,
    )
    thread.start()
    return thread


# ===========================================================================
# Dashboard services (Auftrag 4 M1: Tournaments + Runs + Storage + Archive)
# ===========================================================================

def _db_path_for(config: PipelineConfig) -> Optional[Path]:
    """The dashboard DB lives next to the config files."""
    if config.source_path is None:
        return None
    return config.source_path.parent / "runs.db"


# ---- Tournaments ----------------------------------------------------------

def list_tournaments_for(config: PipelineConfig) -> List[Dict[str, object]]:
    """All Tournaments visible to the dashboard, newest first."""
    from db import open_db, list_tournaments
    db_path = _db_path_for(config)
    if db_path is None:
        return []
    conn = open_db(db_path)
    return [t.to_dict() for t in list_tournaments(conn)]


def create_tournament_for(
    config: PipelineConfig, payload: Dict[str, object],
) -> Optional[Dict[str, object]]:
    """Insert a Tournament from a UI payload; clean unknown keys."""
    from db import create_tournament, open_db
    db_path = _db_path_for(config)
    if db_path is None:
        return None
    conn = open_db(db_path)
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    allowed = ("date", "location", "organizer", "disciplines",
               "youtube_channel", "visibility_default", "video_prefix",
               "description_template", "tags", "max_workers")
    clean = {k: payload[k] for k in allowed if k in payload}
    if "max_workers" in clean:
        try:
            clean["max_workers"] = int(clean["max_workers"])
        except (TypeError, ValueError):
            del clean["max_workers"]
    t = create_tournament(conn, name, **clean)
    return t.to_dict()


def update_tournament_for(
    config: PipelineConfig, tournament_id: int, payload: Dict[str, object],
) -> Optional[Dict[str, object]]:
    from db import open_db, update_tournament
    db_path = _db_path_for(config)
    if db_path is None:
        return None
    conn = open_db(db_path)
    allowed = ("name", "date", "location", "organizer", "disciplines",
               "youtube_channel", "visibility_default", "video_prefix",
               "description_template", "tags", "max_workers")
    clean = {k: payload[k] for k in allowed if k in payload}
    if "max_workers" in clean:
        try:
            clean["max_workers"] = int(clean["max_workers"])
        except (TypeError, ValueError):
            del clean["max_workers"]
    updated = update_tournament(conn, tournament_id, **clean)
    return updated.to_dict() if updated else None


def set_active_tournament_for(
    config: PipelineConfig, discipline: str, tournament_id: int,
) -> bool:
    """Pin a Tournament as 'active' for *discipline*."""
    from db import get_tournament, open_db, set_active_tournament
    db_path = _db_path_for(config)
    if db_path is None:
        return False
    conn = open_db(db_path)
    if get_tournament(conn, tournament_id) is None:
        return False
    set_active_tournament(conn, discipline, tournament_id)
    return True


def get_active_tournament_for(
    config: PipelineConfig,
) -> Optional[Dict[str, object]]:
    from db import get_active_tournament, open_db
    db_path = _db_path_for(config)
    if db_path is None:
        return None
    conn = open_db(db_path)
    t = get_active_tournament(conn, config.discipline)
    return t.to_dict() if t else None


# ---- Run history ----------------------------------------------------------

def get_run_history_for(
    config: PipelineConfig, *, limit: int = 50,
) -> Dict[str, object]:
    """Recent runs + simple aggregates for the dashboard."""
    from db import list_tournaments, open_db
    from db.runs import list_runs
    db_path = _db_path_for(config)
    if db_path is None:
        return {"runs": [], "stats": {}}
    conn = open_db(db_path)
    runs = list_runs(conn, discipline=config.discipline, limit=limit)

    total_in = sum(r.input_bytes for r in runs)
    total_out = sum(r.output_bytes for r in runs)
    total_duration_s = 0.0
    for r in runs:
        total_duration_s += sum(p.duration_s for p in r.phases)
    avg_throughput_gbph = (
        (total_in / 1024 ** 3) / (total_duration_s / 3600)
        if total_duration_s > 0 else 0.0
    )

    return {
        "runs": [r.to_dict() for r in runs],
        "stats": {
            "total_runs": len(runs),
            "total_input_bytes": total_in,
            "total_output_bytes": total_out,
            "total_duration_s": round(total_duration_s, 1),
            "avg_throughput_gbph": round(avg_throughput_gbph, 1),
            "failed_runs": sum(1 for r in runs if r.state == "error"),
        },
    }


# ---- Storage --------------------------------------------------------------

_storage_watcher_cache: Dict[str, object] = {}


def get_storage_watcher(volume_paths: List[Path]):
    """Lazy singleton - we want one watcher per process, not one per request."""
    from watcher.storage_watcher import StorageWatcher
    key = ",".join(sorted(str(p) for p in volume_paths))
    w = _storage_watcher_cache.get(key)
    if w is None:
        w = StorageWatcher(volume_paths)
        w.start()
        _storage_watcher_cache[key] = w
    return w


def _reset_storage_watchers_for_tests() -> None:
    """Stop + clear all cached watchers. Only used by pytest fixtures."""
    for w in list(_storage_watcher_cache.values()):
        try:
            w.stop()                                           # type: ignore[attr-defined]
        except Exception:
            pass
    _storage_watcher_cache.clear()


def get_storage_snapshot(volume_paths: List[Path]) -> Dict[str, object]:
    watcher = get_storage_watcher(volume_paths)
    return watcher.snapshot().to_dict()


# ---- Archive --------------------------------------------------------------

def build_archive_plan_for(
    config: PipelineConfig,
    *,
    tournament_id: int,
    archive_root: Path,
) -> Optional[Dict[str, object]]:
    """Dry-run plan for the operator's confirmation dialog."""
    from db import get_tournament, open_db
    from archive import build_archive_plan
    db_path = _db_path_for(config)
    if db_path is None:
        return None
    conn = open_db(db_path)
    t = get_tournament(conn, tournament_id)
    if t is None:
        return None

    sources = {
        config.discipline: {
            "eingang": config.paths.eingang,
            "output":  config.paths.output,
        },
    }
    try:
        plan = build_archive_plan(
            tournament_name=t.name,
            tournament_id=t.id,
            archive_root=archive_root,
            sources=sources,
        )
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "tournament_name": plan.tournament_name,
        "archive_root": plan.archive_root,
        "total_files": plan.total_files,
        "total_bytes": plan.total_bytes,
        "total_gb": round(plan.total_bytes / 1024 ** 3, 2),
    }


def start_archive_async(
    config: PipelineConfig,
    *,
    tournament_id: int,
    archive_root: Path,
    delete_source: bool = True,
) -> threading.Thread:
    """Run the archive in a daemon thread; UI polls runs.db for status."""
    from archive import build_archive_plan, execute_archive
    from db import get_tournament, open_db, update_tournament

    db_path = _db_path_for(config)

    def _target() -> None:
        if db_path is None:
            return
        conn = open_db(db_path)
        t = get_tournament(conn, tournament_id)
        if t is None:
            return
        sources = {
            config.discipline: {
                "eingang": config.paths.eingang,
                "output":  config.paths.output,
            },
        }
        try:
            plan = build_archive_plan(
                tournament_name=t.name,
                tournament_id=t.id,
                archive_root=archive_root,
                sources=sources,
            )
            with conn:
                cur = conn.execute(
                    "INSERT INTO archives "
                    "(tournament_id, archive_path, started_at, state, "
                    " bytes_copied, files_total) "
                    "VALUES (?, ?, ?, 'running', 0, ?)",
                    (tournament_id, plan.archive_root, __import__(
                        "pipeline.status_file", fromlist=["now_iso"]
                    ).now_iso(), plan.total_files),
                )
                archive_row_id = cur.lastrowid

            result = execute_archive(plan, delete_source=delete_source)

            with conn:
                conn.execute(
                    "UPDATE archives SET "
                    " finished_at = ?, state = ?, bytes_copied = ?, "
                    " files_verified = ?, error = ? "
                    "WHERE id = ?",
                    (result.finished_at, result.state, result.bytes_copied,
                     result.files_verified, result.error, archive_row_id),
                )

            if result.state == "done":
                update_tournament(
                    conn, tournament_id,
                    archived_at=result.finished_at,
                    archive_path=plan.archive_root,
                )
        except Exception as exc:                   # pragma: no cover - defensive
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO archives "
                        "(tournament_id, archive_path, started_at, "
                        " state, error) VALUES (?, '', '', 'error', ?)",
                        (tournament_id, str(exc)),
                    )
            except Exception:
                pass

    thread = threading.Thread(
        target=_target, name=f"archive-{config.discipline}", daemon=True,
    )
    thread.start()
    return thread


def list_archives_for(config: PipelineConfig) -> List[Dict[str, object]]:
    from db import open_db
    db_path = _db_path_for(config)
    if db_path is None:
        return []
    conn = open_db(db_path)
    rows = conn.execute(
        "SELECT a.*, t.name AS tournament_name FROM archives a "
        "JOIN tournaments t ON t.id = a.tournament_id "
        "ORDER BY a.started_at DESC LIMIT 50"
    ).fetchall()
    return [dict(r) for r in rows]
