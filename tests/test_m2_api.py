"""Tests for the M2 manual-control API endpoints."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Tuple

import pytest

from db import db_path_for, open_db
from db.runs import list_runs
from db.schema import _reset_connections_for_tests
from pipeline.config_loader import PipelineConfig, load_config
from tests.e2e.conftest import FIXTURE, seed_camera_folder
from web.app import create_app


TEST_USER = "tester"
TEST_PASS = "secret"


@pytest.fixture(autouse=True)
def _isolated_db():
    from web.services import _reset_storage_watchers_for_tests
    _reset_connections_for_tests()
    _reset_storage_watchers_for_tests()
    yield
    _reset_connections_for_tests()
    _reset_storage_watchers_for_tests()


@pytest.fixture
def app_with_config(tmp_path):
    cfg = {
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
            "turniername": "ApiTest", "disziplin": "Doppel", "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }
    p = tmp_path / "config_doppel.json"
    p.write_text(json.dumps(cfg))
    configs = {"Doppel": load_config(p)}
    app = create_app(configs, secret_key="m2", username=TEST_USER, password=TEST_PASS)
    app.config["TESTING"] = True
    return app, configs


@pytest.fixture
def client(app_with_config):
    app, _ = app_with_config
    return app.test_client()


def _login(c) -> None:
    c.post("/login", data={"username": TEST_USER, "password": TEST_PASS},
           follow_redirects=False)


def _seed_real_run(configs) -> int:
    """Run the real pipeline once and return the created Run id."""
    if not FIXTURE.is_file():
        pytest.skip("ffmpeg fixture missing")
    cfg = configs["Doppel"]
    seed_camera_folder(cfg.paths.eingang, "ET01", n_files=2)
    from watcher.pipeline_runner import run_pipeline
    run_pipeline(cfg)
    runs = list_runs(open_db(db_path_for(cfg.source_path.parent)), discipline="Doppel")
    assert len(runs) >= 1
    return runs[0].id


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------

def test_pause_resume_round_trip(client) -> None:
    _login(client)
    # initial
    initial = client.get("/api/pipeline/Doppel/control").get_json()
    assert initial == {"discipline": "Doppel", "paused": False}

    paused = client.post("/api/pipeline/Doppel/pause").get_json()
    assert paused == {"discipline": "Doppel", "paused": True}

    state = client.get("/api/pipeline/Doppel/control").get_json()
    assert state["paused"] is True

    resumed = client.post("/api/pipeline/Doppel/resume").get_json()
    assert resumed["paused"] is False


def test_pause_unknown_discipline_404(client) -> None:
    _login(client)
    assert client.post("/api/pipeline/Mixed/pause").status_code == 404


def test_pause_requires_login(app_with_config) -> None:
    app, _ = app_with_config
    c = app.test_client()
    assert c.post("/api/pipeline/Doppel/pause").status_code == 401


def test_paused_pipeline_run_is_409_via_runner(app_with_config) -> None:
    """The API /api/run/<d> uses start_run_async which silently swallows
    the PipelineRunError, but the run is rejected by run_pipeline at
    the DB pause-check. Verify the DB state instead."""
    app, configs = app_with_config
    c = app.test_client()
    _login(c)
    c.post("/api/pipeline/Doppel/pause")

    # Try to manually trigger a run - the background thread will reject.
    cfg = configs["Doppel"]
    cfg.paths.eingang.mkdir(parents=True, exist_ok=True)
    (cfg.paths.eingang / "ET01").mkdir()

    from watcher.pipeline_runner import PipelineRunError, run_pipeline
    with pytest.raises(PipelineRunError, match="paused"):
        run_pipeline(cfg)


# ---------------------------------------------------------------------------
# Jobs list
# ---------------------------------------------------------------------------

def test_jobs_list_empty_when_no_runs(client) -> None:
    _login(client)
    data = client.get("/api/jobs/Doppel").get_json()
    assert data == {"jobs": [], "paused": False}


def test_jobs_list_after_real_run(app_with_config) -> None:
    app, configs = app_with_config
    c = app.test_client()
    _login(c)
    _seed_real_run(configs)

    data = c.get("/api/jobs/Doppel").get_json()
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["folder_name"] == "ET01"
    assert data["jobs"][0]["state"] == "done"
    assert "priority" in data["jobs"][0]
    assert "paused" in data["jobs"][0]


def test_jobs_list_filter_states(app_with_config) -> None:
    app, configs = app_with_config
    c = app.test_client()
    _login(c)
    _seed_real_run(configs)

    # Done run with state=done -> filter by state=running yields empty.
    data = c.get("/api/jobs/Doppel?states=running").get_json()
    assert data["jobs"] == []


# ---------------------------------------------------------------------------
# Job update (priority + paused)
# ---------------------------------------------------------------------------

def test_job_update_priority(app_with_config) -> None:
    app, configs = app_with_config
    c = app.test_client()
    _login(c)
    run_id = _seed_real_run(configs)

    res = c.patch(
        f"/api/jobs/Doppel/{run_id}",
        data=json.dumps({"priority": 10}),
        content_type="application/json",
    )
    assert res.status_code == 200

    data = c.get("/api/jobs/Doppel").get_json()
    assert data["jobs"][0]["priority"] == 10


def test_job_update_unknown_run_404(client) -> None:
    _login(client)
    res = client.patch(
        "/api/jobs/Doppel/99999",
        data=json.dumps({"priority": 1}),
        content_type="application/json",
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Single-job restart
# ---------------------------------------------------------------------------

def test_restart_endpoint_re_runs_merge(app_with_config) -> None:
    app, configs = app_with_config
    c = app.test_client()
    _login(c)
    run_id = _seed_real_run(configs)
    cfg = configs["Doppel"]

    # Delete the output to see the restart re-produce it.
    for p in cfg.paths.output.glob("*.mp4"):
        p.unlink()

    res = c.post(
        f"/api/jobs/Doppel/{run_id}/restart",
        data=json.dumps({"from_phase": "merge"}),
        content_type="application/json",
    )
    assert res.status_code == 202

    # Wait for the background thread.
    for _ in range(40):
        files = list(cfg.paths.output.glob("*.mp4"))
        if files:
            break
        time.sleep(0.05)
    assert files, "restart did not re-create the output mp4"


def test_restart_endpoint_rejects_unknown_phase(client) -> None:
    _login(client)
    res = client.post(
        "/api/jobs/Doppel/1/restart",
        data=json.dumps({"from_phase": "schneiden"}),
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "invalid from_phase" in res.get_json()["error"]


# ---------------------------------------------------------------------------
# Bulk restart
# ---------------------------------------------------------------------------

def test_bulk_restart_requires_int_list(client) -> None:
    _login(client)
    res = client.post(
        "/api/jobs/Doppel/bulk-restart",
        data=json.dumps({"run_ids": ["a", "b"]}),
        content_type="application/json",
    )
    assert res.status_code == 400


def test_bulk_restart_schedules_each(app_with_config) -> None:
    app, configs = app_with_config
    c = app.test_client()
    _login(c)
    run_id = _seed_real_run(configs)
    cfg = configs["Doppel"]
    for p in cfg.paths.output.glob("*.mp4"):
        p.unlink()

    res = c.post(
        "/api/jobs/Doppel/bulk-restart",
        data=json.dumps({"run_ids": [run_id], "from_phase": "merge"}),
        content_type="application/json",
    )
    assert res.status_code == 202
    body = res.get_json()
    assert body["count"] == 1
    assert body["from_phase"] == "merge"

    for _ in range(40):
        if list(cfg.paths.output.glob("*.mp4")):
            break
        time.sleep(0.05)
    assert list(cfg.paths.output.glob("*.mp4"))
