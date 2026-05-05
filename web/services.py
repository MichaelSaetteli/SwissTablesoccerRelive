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
    from youtube.youtube_uploader import upload_batch

    return upload_batch(service, config, writer=writer)


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
