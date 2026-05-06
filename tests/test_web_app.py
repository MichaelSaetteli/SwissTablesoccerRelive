"""Tests for the Flask web interface (Schritt 3)."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from pathlib import Path
from typing import Dict, List

import pytest

from pipeline.config_loader import PipelineConfig, load_config
from tests.conftest import make_mp4
from watcher.status import State, StatusWriter, status_path_for
from web.app import create_app


TEST_USER = "tester"
TEST_PASS = "secret-pw"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def einzel_config_dict(tmp_path: Path) -> dict:
    return {
        "discipline": "Einzel",
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / "eingang_einzel"),
            "work": str(tmp_path / "work_einzel"),
            "output": str(tmp_path / "output_einzel"),
            "logs": str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026",
            "sts_nummer": "STS2",
            "turniername": "Seetal",
            "disziplin": "Einzel",
            "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }


@pytest.fixture
def einzel_config_path(tmp_path: Path, einzel_config_dict: dict) -> Path:
    path = tmp_path / "config_einzel.json"
    path.write_text(json.dumps(einzel_config_dict), encoding="utf-8")
    return path


class RecordingRunner:
    """Stand-in for run_pipeline that just records invocations."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.event = threading.Event()

    def __call__(self, config: PipelineConfig) -> StatusWriter:
        self.calls.append(config.discipline)
        writer = StatusWriter(status_path_for(config), config.discipline)
        self.event.set()
        return writer


@pytest.fixture
def app_factory(doppel_config_path: Path, einzel_config_path: Path):
    """Factory for building configured Flask apps with both disciplines."""
    def _build(
        *,
        runner=None,
        only: tuple = ("Doppel", "Einzel"),
    ):
        configs: Dict[str, PipelineConfig] = {}
        if "Doppel" in only:
            configs["Doppel"] = load_config(doppel_config_path)
        if "Einzel" in only:
            configs["Einzel"] = load_config(einzel_config_path)
        app = create_app(
            configs,
            secret_key="test-secret",
            username=TEST_USER,
            password=TEST_PASS,
            runner=runner or RecordingRunner(),
        )
        app.config["TESTING"] = True
        return app
    return _build


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    res = client.post(
        "/login",
        data={"username": TEST_USER, "password": TEST_PASS},
        follow_redirects=False,
    )
    assert res.status_code in (302, 303), res.data


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_index_redirects_to_login_when_anonymous(client) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_api_returns_401_when_anonymous(client) -> None:
    res = client.get("/api/state/Doppel")
    assert res.status_code == 401


def test_login_with_correct_credentials(client) -> None:
    res = client.post(
        "/login",
        data={"username": TEST_USER, "password": TEST_PASS},
        follow_redirects=False,
    )
    assert res.status_code == 302


def test_login_with_wrong_password(client) -> None:
    res = client.post(
        "/login",
        data={"username": TEST_USER, "password": "wrong"},
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert b"fehlgeschlagen" in res.data.lower() or b"Login" in res.data


def test_logout_clears_session(client) -> None:
    _login(client)
    client.get("/logout", follow_redirects=False)
    res = client.get("/api/state/Doppel")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Index renders both tabs
# ---------------------------------------------------------------------------

def test_index_shows_both_tabs(client) -> None:
    _login(client)
    res = client.get("/")
    assert res.status_code == 200
    body = res.data.decode("utf-8")
    assert 'data-tab="Doppel"' in body
    assert 'data-tab="Einzel"' in body


def test_index_disables_missing_discipline(app_factory) -> None:
    app = app_factory(only=("Doppel",))
    client = app.test_client()
    _login(client)
    res = client.get("/")
    body = res.data.decode("utf-8")
    assert 'data-tab="Doppel"' in body
    assert 'data-tab="Einzel"' in body
    # Einzel button must be disabled when its config is missing.
    assert 'data-tab="Einzel"' in body
    assert "disabled" in body


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------

def test_api_state_returns_combined_payload(client) -> None:
    _login(client)
    res = client.get("/api/state/Doppel")
    assert res.status_code == 200
    data = res.get_json()
    assert set(data.keys()) == {"pipeline", "upload", "files"}
    assert data["pipeline"]["discipline"] == "Doppel"
    assert data["pipeline"]["state"] == State.IDLE
    assert data["upload"]["discipline"] == "Doppel"
    assert data["upload"]["state"] == "idle"
    assert data["files"] == []


def test_api_state_unknown_discipline(client) -> None:
    _login(client)
    res = client.get("/api/state/Mixed")
    assert res.status_code == 404


def test_api_state_reflects_writer_changes(app, client, doppel_config_path: Path) -> None:
    _login(client)
    cfg = app.config["PIPELINE_CONFIGS"]["Doppel"]
    writer = StatusWriter(status_path_for(cfg), "Doppel")
    writer.update(state=State.MERGING, folders_detected=["ET03"])

    data = client.get("/api/state/Doppel").get_json()
    assert data["pipeline"]["state"] == State.MERGING
    assert data["pipeline"]["folders_detected"] == ["ET03"]


def test_api_state_lists_output_files(app, client) -> None:
    _login(client)
    cfg = app.config["PIPELINE_CONFIGS"]["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    make_mp4(cfg.paths.output, "video_a.mp4", b"abc")

    data = client.get("/api/state/Doppel").get_json()
    assert [f["name"] for f in data["files"]] == ["video_a.mp4"]


# ---------------------------------------------------------------------------
# /api/run
# ---------------------------------------------------------------------------

def test_api_run_triggers_runner(app_factory) -> None:
    runner = RecordingRunner()
    app = app_factory(runner=runner)
    client = app.test_client()
    _login(client)

    res = client.post("/api/run/Doppel")
    assert res.status_code == 202
    assert runner.event.wait(timeout=2.0)
    assert runner.calls == ["Doppel"]


def test_api_run_unknown_discipline(client) -> None:
    _login(client)
    res = client.post("/api/run/Mixed")
    assert res.status_code == 404


def test_api_run_disabled_discipline(app_factory) -> None:
    runner = RecordingRunner()
    app = app_factory(runner=runner)
    app.config["PIPELINE_CONFIGS"]["Doppel"].enabled = False
    client = app.test_client()
    _login(client)

    res = client.post("/api/run/Doppel")
    assert res.status_code == 409
    assert runner.calls == []


# ---------------------------------------------------------------------------
# /api/files + downloads
# ---------------------------------------------------------------------------

def test_state_lists_output_files_with_sizes(app, client) -> None:
    _login(client)
    cfg = app.config["PIPELINE_CONFIGS"]["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    make_mp4(cfg.paths.output, "video_a.mp4", b"abc")
    make_mp4(cfg.paths.output, "video_b.mp4", b"defgh")

    data = client.get("/api/state/Doppel").get_json()
    names = [f["name"] for f in data["files"]]
    sizes = {f["name"]: f["size_bytes"] for f in data["files"]}
    assert names == ["video_a.mp4", "video_b.mp4"]
    assert sizes == {"video_a.mp4": 3, "video_b.mp4": 5}


def test_download_one_serves_file(app, client) -> None:
    _login(client)
    cfg = app.config["PIPELINE_CONFIGS"]["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    make_mp4(cfg.paths.output, "video_x.mp4", b"hello-bytes")

    res = client.get("/download/Doppel/video_x.mp4")
    assert res.status_code == 200
    assert res.data == b"hello-bytes"


def test_download_path_traversal_blocked(app, client, tmp_path: Path) -> None:
    """A '../' filename must not be served."""
    _login(client)
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"do-not-leak")

    res = client.get("/download/Doppel/..%2Fsecret.mp4")
    assert res.status_code == 404


def test_download_all_zip(app, client) -> None:
    _login(client)
    cfg = app.config["PIPELINE_CONFIGS"]["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    make_mp4(cfg.paths.output, "video_a.mp4", b"AAA")
    make_mp4(cfg.paths.output, "video_b.mp4", b"BBB")

    res = client.get("/download/Doppel/all.zip")
    assert res.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(res.data))
    assert sorted(archive.namelist()) == ["video_a.mp4", "video_b.mp4"]
    assert archive.read("video_a.mp4") == b"AAA"


def test_download_all_zip_empty_output(app, client) -> None:
    _login(client)
    cfg = app.config["PIPELINE_CONFIGS"]["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    res = client.get("/download/Doppel/all.zip")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# /api/youtube-config
# ---------------------------------------------------------------------------

def test_get_youtube_config_returns_defaults(client) -> None:
    _login(client)
    data = client.get("/api/youtube-config/Doppel").get_json()
    assert "title_template" in data
    assert "tournament_name" in data


def test_post_youtube_config_persists_to_disk(app, client, doppel_config_path: Path) -> None:
    _login(client)
    payload = {
        "tournament_name": "STS Bern 2026",
        "date": "17./18. Mai 2026",
        "location": "Bern, Schweiz",
        "title_template": "{turniername} {disziplin} {kamera}",
        "description_template": "Demo",
        "playlist_create_new": True,
        "playlist_new_title": "STS 2026 Doppel",
        "playlist_id": "",
    }
    res = client.post(
        "/api/youtube-config/Doppel",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert res.status_code == 200
    saved = json.loads(doppel_config_path.read_text(encoding="utf-8"))
    assert saved["youtube"]["tournament_name"] == "STS Bern 2026"
    assert saved["youtube"]["playlist_create_new"] is True


def test_get_filename_config_returns_current(client) -> None:
    _login(client)
    data = client.get("/api/filename-config/Doppel").get_json()
    assert data["jahr"] == "2026"
    assert data["sts_nummer"] == "STS2"
    assert data["turniername"] == "Seetal"
    assert data["disziplin"] == "Doppel"
    assert data["part"] == ""


def test_post_filename_config_persists(app, client, doppel_config_path: Path) -> None:
    _login(client)
    payload = {
        "jahr": "2027",
        "sts_nummer": "STS3",
        "turniername": "Bern",
        "disziplin": "Doppel",
        "part": "Part 1",
    }
    res = client.post(
        "/api/filename-config/Doppel",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert res.status_code == 200
    saved = json.loads(doppel_config_path.read_text(encoding="utf-8"))
    assert saved["filename_constants"]["jahr"] == "2027"
    assert saved["filename_constants"]["turniername"] == "Bern"
    assert saved["filename_constants"]["part"] == "Part 1"


def test_post_filename_config_rejects_unknown_keys(app, client, doppel_config_path: Path) -> None:
    _login(client)
    payload = {"jahr": "2099", "evil": "x"}
    client.post(
        "/api/filename-config/Doppel",
        data=json.dumps(payload),
        content_type="application/json",
    )
    saved = json.loads(doppel_config_path.read_text(encoding="utf-8"))
    assert "evil" not in saved["filename_constants"]
    assert saved["filename_constants"]["jahr"] == "2099"


def test_post_filename_config_strips_whitespace(app, client, doppel_config_path: Path) -> None:
    _login(client)
    payload = {"jahr": "  2026  ", "turniername": " Seetal "}
    client.post(
        "/api/filename-config/Doppel",
        data=json.dumps(payload),
        content_type="application/json",
    )
    saved = json.loads(doppel_config_path.read_text(encoding="utf-8"))
    assert saved["filename_constants"]["jahr"] == "2026"
    assert saved["filename_constants"]["turniername"] == "Seetal"


def test_post_youtube_config_rejects_unknown_keys(app, client, doppel_config_path: Path) -> None:
    _login(client)
    payload = {"tournament_name": "OK", "evil_key": "ignored"}
    client.post(
        "/api/youtube-config/Doppel",
        data=json.dumps(payload),
        content_type="application/json",
    )
    saved = json.loads(doppel_config_path.read_text(encoding="utf-8"))
    assert "evil_key" not in saved["youtube"]
    assert saved["youtube"]["tournament_name"] == "OK"


# ---------------------------------------------------------------------------
# YouTube upload preview + trigger + status (Schritt 4)
# ---------------------------------------------------------------------------

def test_upload_preview_lists_titles(app, client) -> None:
    _login(client)
    cfg = app.config["PIPELINE_CONFIGS"]["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    cfg.youtube["title_template"] = "{kamera} #{nummer}"
    make_mp4(cfg.paths.output, "2026 STS2 T01 Seetal Doppel.mp4")
    make_mp4(cfg.paths.output, "2026 STS2 T02 Seetal Doppel.mp4")

    data = client.get("/api/upload-preview/Doppel").get_json()
    titles = [f["title"] for f in data["files"]]
    assert titles == ["T01 #1", "T02 #2"]
    assert data["total"] == 2
    assert "Units" in data["quota_hint"]


def test_upload_preview_empty_output(app, client) -> None:
    _login(client)
    cfg = app.config["PIPELINE_CONFIGS"]["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    data = client.get("/api/upload-preview/Doppel").get_json()
    assert data["files"] == []
    assert data["quota_hint"] == ""


def test_upload_state_starts_idle(client) -> None:
    _login(client)
    data = client.get("/api/state/Doppel").get_json()
    assert data["upload"]["state"] == "idle"
    assert data["upload"]["total_files"] == 0


def test_upload_endpoint_uses_injected_runner(app_factory, doppel_config_path: Path) -> None:
    """POST /api/upload/<discipline> must use injected runner (no real Google)."""
    captured: List[Dict[str, object]] = []

    def fake_factory(config):
        return {"fake": "service"}

    def fake_runner(service, config, writer):
        captured.append({"service": service, "discipline": config.discipline})
        writer.update(state="done", total_files=2, completed_files=2)
        writer.append_log("fake-upload-done")
        return None

    app = app_factory()
    app.config["YOUTUBE_SERVICE_FACTORY"] = fake_factory
    app.config["YOUTUBE_UPLOAD_RUNNER"] = fake_runner
    client = app.test_client()
    _login(client)

    res = client.post("/api/upload/Doppel")
    assert res.status_code == 202

    # Wait until the background thread persists its state change.
    import time
    upload_state: Dict[str, object] = {}
    for _ in range(40):
        payload = client.get("/api/state/Doppel").get_json()
        upload_state = payload["upload"]
        if upload_state["state"] != "idle":
            break
        time.sleep(0.05)
    assert captured and captured[0]["discipline"] == "Doppel"
    assert upload_state["state"] == "done"


def test_upload_endpoint_disabled_discipline_returns_409(
    app_factory,
) -> None:
    app = app_factory()
    app.config["PIPELINE_CONFIGS"]["Doppel"].enabled = False
    client = app.test_client()
    _login(client)
    res = client.post("/api/upload/Doppel")
    assert res.status_code == 409


def test_upload_endpoint_unknown_discipline_returns_404(client) -> None:
    _login(client)
    assert client.post("/api/upload/Mixed").status_code == 404
