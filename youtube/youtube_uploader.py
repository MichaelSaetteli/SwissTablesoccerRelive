"""Thin wrapper around YouTube Data API v3.

All Google calls are funnelled through a small set of helpers so tests
can substitute a fake ``service`` (any object that exposes ``videos()``,
``playlists()`` and ``playlistItems()`` with a chainable API).

Briefing s.6 operations:
  * upload_video       - resumable video insert
  * create_playlist    - one playlist per discipline per tournament
  * add_to_playlist    - link a freshly uploaded video
  * upload_batch       - orchestrates the three above

A ``progress_callback`` hook lets the Web-Interface surface per-file
progress in real time.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from pipeline.config_loader import PipelineConfig

from .metadata_builder import VideoMetadata, build_upload_batch, quota_hint
from .upload_status import UploadStatusWriter, upload_status_path_for

sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class UploadOutcome:
    file: str
    video_id: str
    title: str


@dataclass
class BatchResult:
    playlist_id: str = ""
    uploads: List[UploadOutcome] = field(default_factory=list)


class UploadError(RuntimeError):
    """Raised when the upload pipeline cannot continue."""


# ---------------------------------------------------------------------------
# Single-video upload
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[float], None]
MediaUploadFactory = Callable[[str], Any]


def _default_media_factory(file_path: str) -> Any:
    from googleapiclient.http import MediaFileUpload

    return MediaFileUpload(file_path, resumable=True, chunksize=-1)


def upload_video(
    service: Any,
    file_path: Path,
    title: str,
    description: str,
    *,
    privacy_status: str = "private",
    category_id: str = "17",  # YouTube category 17 = Sports
    progress_callback: Optional[ProgressCallback] = None,
    media_factory: MediaUploadFactory = _default_media_factory,
) -> str:
    """Upload one video and return its YouTube video ID.

    The privacy default is ``private`` so the operator can review the
    video on YouTube Studio before flipping it public - much safer
    than auto-publishing to a tournament playlist.
    """
    if not file_path.is_file():
        raise UploadError(f"Video file does not exist: {file_path}")

    body: Dict[str, Any] = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = media_factory(str(file_path))
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status is not None and progress_callback is not None:
            try:
                progress_callback(status.progress() * 100.0)
            except Exception:  # pragma: no cover - cb must never break upload
                pass

    if not response or "id" not in response:
        raise UploadError(f"YouTube did not return a video id: {response!r}")
    return response["id"]


# ---------------------------------------------------------------------------
# Playlist helpers
# ---------------------------------------------------------------------------

def create_playlist(
    service: Any,
    title: str,
    description: str = "",
    *,
    privacy_status: str = "private",
) -> str:
    body = {
        "snippet": {"title": title, "description": description},
        "status": {"privacyStatus": privacy_status},
    }
    response = service.playlists().insert(
        part="snippet,status", body=body,
    ).execute()
    if not response or "id" not in response:
        raise UploadError(f"Playlist creation returned no id: {response!r}")
    return response["id"]


def add_to_playlist(
    service: Any, playlist_id: str, video_id: str,
) -> None:
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id,
            },
        }
    }
    service.playlistItems().insert(part="snippet", body=body).execute()


# ---------------------------------------------------------------------------
# Batch orchestrator (used by the Web-Interface)
# ---------------------------------------------------------------------------

def _resolve_playlist(
    service: Any,
    config: PipelineConfig,
    writer: UploadStatusWriter,
) -> str:
    """Decide whether to use the configured playlist id or create a new one.

    Briefing s.5: option "Neu anlegen" or "Bestehende ID". Empty or
    missing -> no playlist (uploads are unlinked but still happen).
    """
    yt = config.youtube
    create_new = bool(yt.get("playlist_create_new", False))
    existing_id = str(yt.get("playlist_id", "") or "").strip()

    if create_new:
        new_title = (
            str(yt.get("playlist_new_title", "") or "").strip()
            or f"{yt.get('tournament_name', config.filename_constants.turniername)} "
               f"{config.filename_constants.disziplin}"
        ).strip()
        playlist_id = create_playlist(service, new_title)
        writer.append_log(f"Playlist '{new_title}' angelegt (id={playlist_id})")
        return playlist_id

    return existing_id


def upload_batch(
    service: Any,
    config: PipelineConfig,
    writer: Optional[UploadStatusWriter] = None,
) -> BatchResult:
    """Upload every ``output/*.mp4`` for *config*'s discipline.

    Files are uploaded sequentially (YouTube quota makes parallelism
    pointless). Per-file progress is reported through *writer*.
    """
    output_dir = config.paths.output
    if not output_dir.is_dir():
        raise UploadError(f"Output dir not found: {output_dir}")
    files = sorted(
        p for p in output_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".mp4"
    )
    if not files:
        raise UploadError("No mp4 files in output dir")

    metadata_list: List[VideoMetadata] = build_upload_batch(
        config, [f.name for f in files],
    )

    if writer is None:
        writer = UploadStatusWriter(
            upload_status_path_for(config), config.discipline,
        )

    writer.begin(
        total_files=len(metadata_list),
        quota_hint=quota_hint(len(metadata_list)),
    )

    try:
        playlist_id = _resolve_playlist(service, config, writer)
        if playlist_id:
            writer.update(playlist_id=playlist_id)

        result = BatchResult(playlist_id=playlist_id)

        for meta, path in zip(metadata_list, files):
            writer.begin_file(meta.file)
            writer.append_log(f"Upload start: {meta.file} -> {meta.title!r}")

            video_id = upload_video(
                service,
                path,
                title=meta.title,
                description=meta.description,
                progress_callback=lambda pct, w=writer: w.update_progress(pct),
            )
            writer.finish_file(video_id)
            writer.append_log(f"Upload OK: {meta.file} (id={video_id})")
            result.uploads.append(UploadOutcome(
                file=meta.file, video_id=video_id, title=meta.title,
            ))

            if playlist_id:
                add_to_playlist(service, playlist_id, video_id)
                writer.append_log(
                    f"Added {video_id} to playlist {playlist_id}"
                )

        writer.finish()
        return result

    except Exception as exc:
        writer.fail(f"{type(exc).__name__}: {exc}")
        writer.append_log(f"Upload aborted: {exc}")
        raise
