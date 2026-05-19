"""Tests for the dashboard API endpoints (Auftrag 4 M1)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List

import pytest

from db.schema import _reset_connections_for_tests
from pipeline.config_loader import PipelineConfig, load_config
from web.app import create_app


TEST_USER = "tester"
TEST_PASS = "secret"


@pytest.fixture(autouse=True)
def _isolated_db():
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


@pytest.fixture
def app_with_config(tmp_path):
    """Build a working Flask app + a Doppel config that lives in tmp_path."""
    cfg_path = tmp_path / "config_doppel.json"
    cfg_path.write_text(json.dumps({
        "discipline": "Doppel",
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / "eingang"),
            "work":    str(tmp_path / "work"),
            "output":  str(tmp_path / "output"),
            "logs":    str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026", "sts_nummer": "STS2",
            "turniername": "Test", "disziplin": "Doppel", "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }))

    configs = {"Doppel": load_config(cfg_path)}
    app = create_app(
        configs,
        secret_key="dash-test",
        username=TEST_USER,
        password=TEST_PASS,
    )
    app.config["TESTING"] = True
    return app, configs


@pytest.fixture
def client(app_with_config):
    app, _ = app_with_config
    return app.test_client()


def _login(client) -> None:
    client.post("/login",
                data={"username": TEST_USER, "password": TEST_PASS},
                follow_redirects=False)


# ---------------------------------------------------------------------------
# /api/tournaments
# ---------------------------------------------------------------------------

def test_tournaments_empty_initially(client) -> None:
    _login(client)
    data = client.get("/api/tournaments/Doppel").get_json()
    assert data == {"tournaments": [], "active_tournament": None}


def test_tournament_create_and_list(client) -> None:
    _login(client)
    payload = {
        "name": "Bern 2026",
        "date": "2026-05-17",
        "location": "Festhalle",
        "organizer": "TFCSG",
        "video_prefix": "STR_2026_Bern_",
        "max_workers": 6,
    }
    res = client.post("/api/tournaments/Doppel",
                       data=json.dumps(payload),
                       content_type="application/json")
    assert res.status_code == 201
    created = res.get_json()
    assert created["name"] == "Bern 2026"
    assert created["max_workers"] == 6
    assert created["is_auto_created"] is False

    listed = client.get("/api/tournaments/Doppel").get_json()
    assert len(listed["tournaments"]) == 1
    assert listed["tournaments"][0]["id"] == created["id"]


def test_tournament_create_rejects_empty_name(client) -> None:
    _login(client)
    res = client.post("/api/tournaments/Doppel",
                       data=json.dumps({"name": ""}),
                       content_type="application/json")
    assert res.status_code == 400


def test_tournament_update_patches_subset(client) -> None:
    _login(client)
    created = client.post(
        "/api/tournaments/Doppel",
        data=json.dumps({"name": "x"}),
        content_type="application/json",
    ).get_json()
    res = client.patch(
        f"/api/tournaments/Doppel/{created['id']}",
        data=json.dumps({"location": "Bern", "evil_key": "ignored"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    assert res.get_json()["location"] == "Bern"


def test_tournament_activate_sets_state(client) -> None:
    _login(client)
    a = client.post("/api/tournaments/Doppel",
                     data=json.dumps({"name": "A"}),
                     content_type="application/json").get_json()
    b = client.post("/api/tournaments/Doppel",
                     data=json.dumps({"name": "B"}),
                     content_type="application/json").get_json()
    client.post(f"/api/tournaments/Doppel/{a['id']}/activate")
    client.post(f"/api/tournaments/Doppel/{b['id']}/activate")

    state = client.get("/api/state/Doppel").get_json()
    assert state["active_tournament"]["id"] == b["id"]


# ---------------------------------------------------------------------------
# /api/history
# ---------------------------------------------------------------------------

def test_history_returns_empty_initially(client) -> None:
    _login(client)
    data = client.get("/api/history/Doppel").get_json()
    assert data["runs"] == []
    assert data["stats"]["total_runs"] == 0


def test_history_reflects_real_run(app_with_config) -> None:
    app, configs = app_with_config
    client = app.test_client()
    _login(client)

    # Persist a run through the actual pipeline_runner so the e2e path
    # is exercised. Use the test fixture clip for real ffmpeg work.
    import shutil
    cfg = configs["Doppel"]
    folder = cfg.paths.eingang / "ET01"
    folder.mkdir(parents=True)
    src = Path(__file__).resolve().parents[1] / "tests" / "e2e" / "fixtures" / "sample_clip.mp4"
    if not src.is_file():
        pytest.skip("ffmpeg fixture missing")

    for i in range(2):
        shutil.copy2(src, folder / f"src_{i:03d}.mp4")

    from watcher.pipeline_runner import run_pipeline
    run_pipeline(cfg)

    data = client.get("/api/history/Doppel").get_json()
    assert data["stats"]["total_runs"] == 1
    run = data["runs"][0]
    assert run["folder_name"] == "ET01"
    assert run["state"] == "done"
    assert run["input_bytes"] > 0
    phases = [p["phase"] for p in run["phases"]]
    assert "move" in phases and "merge" in phases


# ---------------------------------------------------------------------------
# /api/storage
# ---------------------------------------------------------------------------

def test_storage_endpoint_returns_overall(client, monkeypatch) -> None:
    _login(client)
    # The service falls back to config-path dirs when no /volume[N] is found.
    res = client.get("/api/storage")
    assert res.status_code == 200
    data = res.get_json()
    assert "volumes" in data
    assert "overall_status" in data


# ---------------------------------------------------------------------------
# /api/archive/*
# ---------------------------------------------------------------------------

def test_archive_plan_returns_total_bytes(app_with_config, tmp_path) -> None:
    app, configs = app_with_config
    client = app.test_client()
    _login(client)

    cfg = configs["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    (cfg.paths.output / "final.mp4").write_bytes(b"X" * 2048)
    cfg.paths.eingang.mkdir(parents=True, exist_ok=True)
    (cfg.paths.eingang / "raw.mp4").write_bytes(b"Y" * 1024)

    # Create a tournament we can reference.
    t = client.post("/api/tournaments/Doppel",
                     data=json.dumps({"name": "Archive Test"}),
                     content_type="application/json").get_json()

    archive_root = tmp_path / "hdd_archive"
    archive_root.mkdir()
    res = client.post(
        "/api/archive/Doppel/plan",
        data=json.dumps({
            "tournament_id": t["id"],
            "archive_root": str(archive_root),
        }),
        content_type="application/json",
    )
    assert res.status_code == 200
    plan = res.get_json()
    assert plan["total_files"] == 2
    assert plan["total_bytes"] == 3072


def test_archive_execute_requires_confirmation(client) -> None:
    _login(client)
    res = client.post(
        "/api/archive/Doppel/execute",
        data=json.dumps({
            "tournament_id": 1,
            "archive_root": "/tmp",
            # confirm missing!
        }),
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "confirmation" in res.get_json()["error"].lower()


def test_archive_execute_runs_background(app_with_config, tmp_path) -> None:
    app, configs = app_with_config
    client = app.test_client()
    _login(client)

    cfg = configs["Doppel"]
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    (cfg.paths.output / "vid.mp4").write_bytes(b"Z" * 4096)

    t = client.post("/api/tournaments/Doppel",
                     data=json.dumps({"name": "X"}),
                     content_type="application/json").get_json()

    archive_root = tmp_path / "hdd"
    archive_root.mkdir()
    res = client.post(
        "/api/archive/Doppel/execute",
        data=json.dumps({
            "tournament_id": t["id"],
            "archive_root": str(archive_root),
            "delete_source": False,
            "confirm": "yes",
        }),
        content_type="application/json",
    )
    assert res.status_code == 202

    # Wait briefly for the background thread to update the archives table.
    for _ in range(40):
        archives = client.get("/api/archives/Doppel").get_json()["archives"]
        if archives and archives[0]["state"] == "done":
            break
        time.sleep(0.05)
    archives = client.get("/api/archives/Doppel").get_json()["archives"]
    assert archives and archives[0]["state"] == "done"
    assert archives[0]["files_verified"] >= 1
