"""Tests for youtube.youtube_uploader.

A FakeYouTubeService mimics the chained ``service.videos().insert(...)``
API used by google-api-python-client so the tests run without ever
touching Google.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from pipeline.config_loader import load_config
from tests.conftest import make_mp4
from youtube.upload_status import (
    UploadState,
    UploadStatusWriter,
    upload_status_path_for,
)
from youtube.youtube_uploader import (
    UploadError,
    add_to_playlist,
    create_playlist,
    upload_batch,
    upload_video,
)


# ---------------------------------------------------------------------------
# Fake Google API service
# ---------------------------------------------------------------------------

class _FakeStatus:
    def __init__(self, frac: float) -> None:
        self._frac = frac

    def progress(self) -> float:
        return self._frac


class _FakeInsertRequest:
    def __init__(self, video_id: str, chunks: int = 3) -> None:
        self._video_id = video_id
        self._chunks = chunks
        self._step = 0

    def next_chunk(self) -> Tuple[Optional[_FakeStatus], Optional[Dict[str, Any]]]:
        self._step += 1
        if self._step < self._chunks:
            return _FakeStatus(self._step / self._chunks), None
        return None, {"id": self._video_id}


class _FakeExecRequest:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def execute(self) -> Dict[str, Any]:
        return self._payload


class _Videos:
    def __init__(self, parent: "FakeYouTubeService") -> None:
        self._parent = parent

    def insert(self, *, part: str, body: Dict[str, Any], media_body: Any
               ) -> _FakeInsertRequest:
        self._parent.insert_calls.append({"part": part, "body": body, "media": media_body})
        video_id = self._parent.next_video_id()
        return _FakeInsertRequest(video_id, chunks=3)


class _Playlists:
    def __init__(self, parent: "FakeYouTubeService") -> None:
        self._parent = parent

    def insert(self, *, part: str, body: Dict[str, Any]) -> _FakeExecRequest:
        playlist_id = f"PL_FAKE_{len(self._parent.playlist_calls) + 1}"
        self._parent.playlist_calls.append({"part": part, "body": body, "id": playlist_id})
        return _FakeExecRequest({"id": playlist_id, **body})


class _PlaylistItems:
    def __init__(self, parent: "FakeYouTubeService") -> None:
        self._parent = parent

    def insert(self, *, part: str, body: Dict[str, Any]) -> _FakeExecRequest:
        self._parent.playlist_item_calls.append({"part": part, "body": body})
        return _FakeExecRequest({"id": "PLI_FAKE"})


class FakeYouTubeService:
    def __init__(self, video_ids: Optional[List[str]] = None) -> None:
        self._video_ids = list(video_ids or [])
        self._video_counter = 0
        self.insert_calls: List[Dict[str, Any]] = []
        self.playlist_calls: List[Dict[str, Any]] = []
        self.playlist_item_calls: List[Dict[str, Any]] = []

    def videos(self) -> _Videos:
        return _Videos(self)

    def playlists(self) -> _Playlists:
        return _Playlists(self)

    def playlistItems(self) -> _PlaylistItems:  # noqa: N802 - mirror Google API
        return _PlaylistItems(self)

    def next_video_id(self) -> str:
        if self._video_ids:
            return self._video_ids.pop(0)
        self._video_counter += 1
        return f"VID{self._video_counter:03d}"


def _fake_media_factory(file_path: str):
    """Replace MediaFileUpload with a stub object."""
    return {"file_path": file_path}


# ---------------------------------------------------------------------------
# upload_video
# ---------------------------------------------------------------------------

def test_upload_video_calls_progress_and_returns_id(tmp_path: Path) -> None:
    service = FakeYouTubeService(video_ids=["vid_abc"])
    file_path = make_mp4(tmp_path, "demo.mp4", b"x")
    progress: List[float] = []

    video_id = upload_video(
        service,
        file_path,
        title="My title",
        description="Body",
        progress_callback=progress.append,
        media_factory=_fake_media_factory,
    )

    assert video_id == "vid_abc"
    assert len(progress) >= 1
    assert all(0 <= p <= 100 for p in progress)
    body = service.insert_calls[0]["body"]
    assert body["snippet"]["title"] == "My title"
    assert body["status"]["privacyStatus"] == "private"


def test_upload_video_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(UploadError):
        upload_video(
            FakeYouTubeService(),
            tmp_path / "missing.mp4",
            title="t", description="d",
            media_factory=_fake_media_factory,
        )


# ---------------------------------------------------------------------------
# Playlist helpers
# ---------------------------------------------------------------------------

def test_create_playlist_returns_id() -> None:
    service = FakeYouTubeService()
    playlist_id = create_playlist(service, "STS Bern Doppel", "desc")
    assert playlist_id.startswith("PL_FAKE_")
    assert service.playlist_calls[0]["body"]["snippet"]["title"] == "STS Bern Doppel"


def test_add_to_playlist_sends_correct_body() -> None:
    service = FakeYouTubeService()
    add_to_playlist(service, "PL123", "VID42")
    body = service.playlist_item_calls[0]["body"]
    assert body["snippet"]["playlistId"] == "PL123"
    assert body["snippet"]["resourceId"]["videoId"] == "VID42"


# ---------------------------------------------------------------------------
# upload_batch (full orchestration)
# ---------------------------------------------------------------------------

def _seed_outputs(cfg, names: List[str]) -> None:
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    for n in names:
        make_mp4(cfg.paths.output, n, b"\x00")


def _patch_media(monkeypatch) -> None:
    monkeypatch.setattr(
        "youtube.youtube_uploader._default_media_factory",
        _fake_media_factory,
    )


def test_upload_batch_creates_playlist_when_requested(
    monkeypatch, doppel_config_path: Path,
) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["playlist_create_new"] = True
    cfg.youtube["playlist_new_title"] = "STS Bern Doppel"
    cfg.youtube["title_template"] = "{kamera} #{nummer}"
    _seed_outputs(cfg, [
        "2026 STS2 T01 Seetal Doppel.mp4",
        "2026 STS2 T02 Seetal Doppel.mp4",
    ])

    service = FakeYouTubeService(video_ids=["vid1", "vid2"])
    writer = UploadStatusWriter(upload_status_path_for(cfg), "Doppel")
    _patch_media(monkeypatch)

    result = upload_batch(service, cfg, writer=writer)

    assert result.playlist_id.startswith("PL_FAKE_")
    assert [u.video_id for u in result.uploads] == ["vid1", "vid2"]
    assert [u.title for u in result.uploads] == ["T01 #1", "T02 #2"]
    assert len(service.insert_calls) == 2
    assert len(service.playlist_calls) == 1
    assert len(service.playlist_item_calls) == 2
    assert writer.status.state == UploadState.DONE
    assert writer.status.completed_files == 2
    assert writer.status.uploaded_video_ids == ["vid1", "vid2"]


def test_upload_batch_uses_existing_playlist_id(
    monkeypatch, doppel_config_path: Path,
) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["playlist_create_new"] = False
    cfg.youtube["playlist_id"] = "PL_EXISTING"
    cfg.youtube["title_template"] = "{kamera}"
    _seed_outputs(cfg, ["2026 STS2 T01 Seetal Doppel.mp4"])

    service = FakeYouTubeService(video_ids=["vidX"])
    writer = UploadStatusWriter(upload_status_path_for(cfg), "Doppel")
    _patch_media(monkeypatch)

    result = upload_batch(service, cfg, writer=writer)

    # No new playlist created.
    assert service.playlist_calls == []
    assert result.playlist_id == "PL_EXISTING"
    # Video added to the configured playlist.
    assert service.playlist_item_calls[0]["body"]["snippet"]["playlistId"] == "PL_EXISTING"


def test_upload_batch_no_playlist_when_neither_set(
    monkeypatch, doppel_config_path: Path,
) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["playlist_create_new"] = False
    cfg.youtube["playlist_id"] = ""
    cfg.youtube["title_template"] = "{kamera}"
    _seed_outputs(cfg, ["2026 STS2 T01 Seetal Doppel.mp4"])

    service = FakeYouTubeService(video_ids=["vidY"])
    writer = UploadStatusWriter(upload_status_path_for(cfg), "Doppel")
    _patch_media(monkeypatch)

    upload_batch(service, cfg, writer=writer)
    assert service.playlist_calls == []
    assert service.playlist_item_calls == []  # no playlist -> no link step


def test_upload_batch_no_files_raises(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    with pytest.raises(UploadError):
        upload_batch(FakeYouTubeService(), cfg)


def test_upload_batch_marks_status_error_on_failure(
    monkeypatch, doppel_config_path: Path,
) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["playlist_create_new"] = False
    cfg.youtube["title_template"] = "{kamera}"
    _seed_outputs(cfg, ["2026 STS2 T01 Seetal Doppel.mp4"])
    _patch_media(monkeypatch)

    class BoomService(FakeYouTubeService):
        def videos(self) -> _Videos:
            raise RuntimeError("network down")

    writer = UploadStatusWriter(upload_status_path_for(cfg), "Doppel")
    with pytest.raises(RuntimeError):
        upload_batch(BoomService(), cfg, writer=writer)

    assert writer.status.state == UploadState.ERROR
    assert "network down" in (writer.status.error or "")
