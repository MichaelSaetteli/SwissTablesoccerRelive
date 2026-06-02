"""HTTP-level tests for the upload-staging API (Issue #15).

Boots the Flask test client with both disciplines configured, drives the
full upload lifecycle and asserts state transitions plus final filesystem
side effects.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Dict, List

import pytest

from db import open_db
from db.schema import _reset_connections_for_tests
from db.tournaments import create_tournament, set_active_tournament
from pipeline.config_loader import PipelineConfig, load_config
from web.app import create_app


TEST_USER = "tester"
TEST_PASS = "secret-pw"


def _config_dict(tmp_path: Path, discipline: str) -> dict:
    suffix = discipline.lower()
    return {
        "discipline": discipline,
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / f"eingang_{suffix}"),
            "work":    str(tmp_path / f"work_{suffix}"),
            "output":  str(tmp_path / f"output_{suffix}"),
            "logs":    str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026", "sts_nummer": "STS2",
            "turniername": "Seetal", "disziplin": discipline, "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }


@pytest.fixture(autouse=True)
def _isolated():
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


@pytest.fixture
def configs(tmp_path: Path) -> Dict[str, PipelineConfig]:
    out: Dict[str, PipelineConfig] = {}
    for discipline in ("Einzel", "Doppel"):
        cfg_path = tmp_path / f"config_{discipline.lower()}.json"
        cfg_path.write_text(
            json.dumps(_config_dict(tmp_path, discipline)),
            encoding="utf-8",
        )
        out[discipline] = load_config(cfg_path)
    return out


class _NoopRunner:
    def __call__(self, config: PipelineConfig):
        from watcher.status import StatusWriter, status_path_for
        return StatusWriter(status_path_for(config), config.discipline)


@pytest.fixture
def app(configs):
    app = create_app(
        configs,
        secret_key="test-secret",
        username=TEST_USER, password=TEST_PASS,
        runner=_NoopRunner(),
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    resp = client.post(
        "/login",
        data={"username": TEST_USER, "password": TEST_PASS},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code


@pytest.fixture
def tournament_id(configs) -> int:
    cfg = next(iter(configs.values()))
    db_path = cfg.source_path.parent / "runs.db"
    conn = open_db(db_path)
    t = create_tournament(conn, "Seetal 2026")
    assert t.id is not None
    set_active_tournament(conn, "Einzel", t.id)
    set_active_tournament(conn, "Doppel", t.id)
    return t.id


def _start(client, *, tournament_id, table="ET01", discipline="Einzel",
           expected_files=2, expected_bytes=300,
           auto_release=False, card_uuid=None):
    body = {
        "tournament_id": tournament_id,
        "discipline": discipline,
        "table_name": table,
        "expected_files": expected_files,
        "expected_bytes": expected_bytes,
        "auto_release": auto_release,
    }
    if card_uuid is not None:
        body["card_uuid"] = card_uuid
    return client.post("/api/upload/start", json=body)


def _chunk(client, card_id, *, relative_name, data: bytes):
    return client.post(
        f"/api/upload/{card_id}/chunk",
        data={
            "relative_name": relative_name,
            "file": (io.BytesIO(data), "anything.bin"),
        },
        content_type="multipart/form-data",
    )


def test_active_tournament_lists_disciplines(client, tournament_id):
    _login(client)
    resp = client.get("/api/upload/active-tournament")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "disciplines" in body
    assert body["disciplines"]["Einzel"]["name"] == "Seetal 2026"
    assert body["disciplines"]["Doppel"]["name"] == "Seetal 2026"
    assert body["disciplines"]["Einzel"]["expected_cards_einzel"] == 0


def test_full_upload_lifecycle_with_explicit_release(client, configs, tournament_id):
    _login(client)
    started = _start(
        client, tournament_id=tournament_id,
        expected_files=2, expected_bytes=300,
    ).get_json()
    card_id = started["id"]
    assert started["state"] == "uploading"

    r1 = _chunk(client, card_id, relative_name="v_001.mp4", data=b"x" * 100)
    assert r1.status_code == 200
    assert r1.get_json()["received_files"] == 1

    r2 = _chunk(client, card_id, relative_name="v_002.mp4", data=b"y" * 200)
    assert r2.status_code == 200

    finish = client.post(f"/api/upload/{card_id}/finish").get_json()
    assert finish["state"] == "verified"

    release = client.post(
        "/api/upload/release", json={"card_ids": [card_id]},
    ).get_json()
    final = release["released"][0]
    assert final["state"] == "released"

    eingang = configs["Einzel"].paths.eingang
    assert (eingang / "ET01" / "v_001.mp4").read_bytes() == b"x" * 100
    assert (eingang / "ET01" / "v_002.mp4").read_bytes() == b"y" * 200


def test_auto_release_skips_manual_step(client, configs, tournament_id):
    _login(client)
    started = _start(
        client, tournament_id=tournament_id,
        table="ET05", expected_files=1, expected_bytes=100,
        auto_release=True,
    ).get_json()
    card_id = started["id"]
    _chunk(client, card_id, relative_name="v.mp4", data=b"z" * 100)

    finish = client.post(f"/api/upload/{card_id}/finish").get_json()
    assert finish["state"] == "released"
    assert (configs["Einzel"].paths.eingang / "ET05" / "v.mp4").exists()


def test_manifest_mismatch_marks_failed_returns_422(client, tournament_id):
    _login(client)
    started = _start(
        client, tournament_id=tournament_id,
        expected_files=2, expected_bytes=300,
    ).get_json()
    card_id = started["id"]
    # only one file uploaded but two were promised
    _chunk(client, card_id, relative_name="v.mp4", data=b"x" * 100)

    resp = client.post(f"/api/upload/{card_id}/finish")
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["state"] == "failed"
    assert "files" in body["error_message"]


def test_cancel_removes_staging(client, tournament_id, configs):
    _login(client)
    started = _start(client, tournament_id=tournament_id).get_json()
    card_id = started["id"]
    _chunk(client, card_id, relative_name="v.mp4", data=b"x" * 50)

    cancelled = client.post(
        f"/api/upload/{card_id}/cancel",
    ).get_json()
    assert cancelled["state"] == "cancelled"
    assert not Path(started["staging_path"]).exists()


def test_status_lists_cards(client, tournament_id):
    _login(client)
    _start(
        client, tournament_id=tournament_id,
        table="ET01", expected_files=1, expected_bytes=10,
    )
    _start(
        client, tournament_id=tournament_id,
        discipline="Doppel", table="ET02",
        expected_files=1, expected_bytes=10,
    )
    body = client.get(
        "/api/upload/status?discipline=Einzel",
    ).get_json()
    tables = {c["table_name"] for c in body["cards"]}
    assert tables == {"ET01"}


def test_unauthenticated_blocked(client, tournament_id):
    # No login -> redirect to /login.
    resp = client.post("/api/upload/start", json={})
    assert resp.status_code in (302, 401, 403)


def test_release_into_existing_eingang_folder_marks_failed(
    client, configs, tournament_id,
):
    _login(client)
    eingang = configs["Einzel"].paths.eingang
    (eingang / "ET11").mkdir(parents=True)
    (eingang / "ET11" / "previous.mp4").write_bytes(b"OLD")

    started = _start(
        client, tournament_id=tournament_id, table="ET11",
        expected_files=1, expected_bytes=10,
    ).get_json()
    cid = started["id"]
    _chunk(client, cid, relative_name="v.mp4", data=b"x" * 10)
    client.post(f"/api/upload/{cid}/finish")

    rel = client.post(
        "/api/upload/release", json={"card_ids": [cid]},
    ).get_json()
    assert rel["released"][0]["state"] == "failed"
    # Original data intact - the watcher cannot have been triggered.
    assert (eingang / "ET11" / "previous.mp4").read_bytes() == b"OLD"
