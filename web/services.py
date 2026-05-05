"""High-level operations the Flask routes call into.

Keeps the route handlers thin so the same logic is unit-testable without
spinning up a Flask test client.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pipeline.config_loader import PipelineConfig, save_config
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
