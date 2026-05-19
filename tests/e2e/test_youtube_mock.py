"""End-to-end upload flow with a *mocked* YouTube Data API service.

We never call out to youtube.com from the test suite. The fake service
exposes the same chained call shape (`service.videos().insert(...)`,
`.next_chunk()`, `playlists().insert().execute()`, etc.) so the rest of
the upload pipeline is exercised exactly as it would be in production.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from pipeline.config_loader import load_config
from tests.e2e.conftest import FIXTURE, seed_camera_folder
from watcher.pipeline_runner import run_pipeline
from watcher.status import State
from youtube.upload_status import UploadState, UploadStatusWriter, upload_status_path_for
from youtube.youtube_uploader import upload_batch


# ---------------------------------------------------------------------------
# Fake YouTube service
# ---------------------------------------------------------------------------

class _FakeStatus:
    def __init__(self, frac: float) -> None: self._f = frac
    def progress(self) -> float: return self._f


class _FakeInsertRequest:
    def __init__(self, vid: str) -> None:
        self._vid = vid
        self._step = 0

    def next_chunk(self):
        self._step += 1
        if self._step < 3:
            return _FakeStatus(self._step / 3), None
        return None, {"id": self._vid}


class _Exec:
    def __init__(self, payload: Dict[str, Any]) -> None: self._p = payload
    def execute(self) -> Dict[str, Any]: return self._p


class FakeYouTubeService:
    def __init__(self) -> None:
        self.video_uploads: List[Dict[str, Any]] = []
        self.playlist_creates: List[Dict[str, Any]] = []
        self.playlist_items: List[Dict[str, Any]] = []
        self._counter = 0

    def videos(self):
        outer = self

        class Videos:
            @staticmethod
            def insert(*, part: str, body: Dict[str, Any], media_body: Any):
                outer._counter += 1
                outer.video_uploads.append({"part": part, "body": body, "media": media_body})
                return _FakeInsertRequest(f"e2e_vid_{outer._counter:03d}")
        return Videos()

    def playlists(self):
        outer = self

        class Playlists:
            @staticmethod
            def insert(*, part: str, body: Dict[str, Any]):
                pid = f"PL_E2E_{len(outer.playlist_creates) + 1}"
                outer.playlist_creates.append({"part": part, "body": body, "id": pid})
                return _Exec({"id": pid, **body})
        return Playlists()

    def playlistItems(self):
        outer = self

        class PI:
            @staticmethod
            def insert(*, part: str, body: Dict[str, Any]):
                outer.playlist_items.append({"part": part, "body": body})
                return _Exec({"id": "PLI_E2E"})
        return PI()


def _fake_media(file_path: str) -> Dict[str, str]:
    return {"file_path": file_path}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_full_pipeline_then_youtube_upload(
    monkeypatch, tmp_path: Path, doppel_config_path: Path,
) -> None:
    """Cradle-to-grave: drop video -> pipeline -> mock YouTube upload."""
    cfg = load_config(doppel_config_path)
    cfg.youtube["title_template"] = "{turniername} {disziplin} {kamera}"
    cfg.youtube["description_template"] = "Aufnahme: {kamera}, Nr. {nummer}"
    cfg.youtube["playlist_create_new"] = True
    cfg.youtube["playlist_new_title"] = "STS E2E Doppel"

    seed_camera_folder(cfg.paths.eingang, "ET01", n_files=2)
    seed_camera_folder(cfg.paths.eingang, "ET02", n_files=2)

    # 1) Real pipeline run (real ffmpeg).
    writer = run_pipeline(cfg)
    assert writer.status.state == State.DONE, writer.status.error
    assert len(list(cfg.paths.output.glob("*.mp4"))) == 2

    # 2) Mocked upload run.
    monkeypatch.setattr(
        "youtube.youtube_uploader._default_media_factory", _fake_media
    )
    service = FakeYouTubeService()
    upload_writer = UploadStatusWriter(upload_status_path_for(cfg), "Doppel")
    result = upload_batch(service, cfg, writer=upload_writer)

    # Playlist was created exactly once with the configured title.
    assert len(service.playlist_creates) == 1
    assert service.playlist_creates[0]["body"]["snippet"]["title"] == "STS E2E Doppel"

    # Both videos uploaded with templated titles.
    titles = sorted(c["body"]["snippet"]["title"] for c in service.video_uploads)
    assert titles == ["E2eTest Doppel T01", "E2eTest Doppel T02"]

    # Both videos linked to the playlist.
    assert len(service.playlist_items) == 2

    # Status writer reflects the run.
    assert upload_writer.status.state == UploadState.DONE
    assert upload_writer.status.completed_files == 2
    assert len(upload_writer.status.uploaded_video_ids) == 2
    assert result.playlist_id.startswith("PL_E2E_")
