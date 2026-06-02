"""End-to-end tests for upload_client.upload_engine.

The happy paths, resume and batch release run the engine against the REAL
Slice-1 server (Flask test client via FlaskSessionAdapter), proving the
client core and the server contract agree byte-for-byte. The manifest
failure path uses a scripted fake session for determinism.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from db.schema import _reset_connections_for_tests
from tests.client_helpers import (
    FailAfterNChunks,
    FakeResp,
    FakeSession,
    FlaskSessionAdapter,
    TEST_PASS,
    TEST_USER,
    make_app,
    make_card,
    make_configs,
    setup_tournament,
)
from upload_client.api_client import ApiClient
from upload_client.manifest import build_manifest
from upload_client.marker import read_marker
from upload_client.state_store import StateStore
from upload_client.upload_engine import (
    CLIENT_FAILED,
    CLIENT_INTERRUPTED,
    CLIENT_RELEASED,
    CLIENT_VERIFIED,
    UploadEngine,
    UploadInterrupted,
)


@pytest.fixture(autouse=True)
def _isolated():
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


@pytest.fixture
def env(tmp_path: Path):
    """A booted server + an active tournament + a logged-in ApiClient."""
    configs = make_configs(tmp_path)
    tid = setup_tournament(configs)
    app = make_app(configs)
    client = app.test_client()
    adapter = FlaskSessionAdapter(client)
    api = ApiClient("http://server", session=adapter)
    api.login(TEST_USER, TEST_PASS)
    return {
        "configs": configs, "tid": tid, "app": app, "client": client,
        "adapter": adapter, "api": api, "tmp_path": tmp_path,
    }


def _engine(env, tmp_path: Path, api=None) -> UploadEngine:
    store = StateStore(tmp_path / "client_state.json")
    return UploadEngine(api or env["api"], store)


def test_active_tournament_discovery(env) -> None:
    data = env["api"].active_tournament()
    assert data["disciplines"]["Einzel"]["name"] == "Seetal 2026"


def test_full_upload_with_explicit_release(env, tmp_path: Path) -> None:
    card_root = make_card(tmp_path / "card", tournament_id=env["tid"])
    marker = read_marker(card_root)
    manifest = build_manifest(card_root)
    engine = _engine(env, tmp_path)

    progress = engine.upload(marker, manifest, auto_release=False)
    assert progress.state == CLIENT_VERIFIED
    assert progress.safe_to_remove is True
    assert progress.sent_files == 2

    released = engine.release([marker.card_uuid])
    assert len(released) == 1
    assert released[0].state == CLIENT_RELEASED
    assert released[0].final_path is not None

    # Atomic handoff really landed the folder in eingang_einzel/ET01.
    eingang = env["configs"]["Einzel"].paths.eingang / "ET01"
    assert eingang.is_dir()
    assert len(list(eingang.iterdir())) == 2


def test_auto_release_skips_manual_step(env, tmp_path: Path) -> None:
    card_root = make_card(tmp_path / "card", tournament_id=env["tid"])
    engine = _engine(env, tmp_path)
    progress = engine.upload(
        read_marker(card_root), build_manifest(card_root), auto_release=True,
    )
    assert progress.state == CLIENT_RELEASED
    assert progress.final_path is not None


def test_dcim_card_lands_flat(env, tmp_path: Path) -> None:
    card_root = make_card(
        tmp_path / "card", tournament_id=env["tid"],
        files={
            "DCIM/100PANA/S001.mp4": b"a" * 50,
            "DCIM/100PANA/S002.mp4": b"b" * 60,
        },
    )
    engine = _engine(env, tmp_path)
    engine.upload(read_marker(card_root), build_manifest(card_root),
                  auto_release=True)
    eingang = env["configs"]["Einzel"].paths.eingang / "ET01"
    names = sorted(p.name for p in eingang.iterdir())
    assert names == ["S001.mp4", "S002.mp4"]  # flattened, no DCIM dirs


def test_interrupted_then_resume(env, tmp_path: Path) -> None:
    card_root = make_card(
        tmp_path / "card", tournament_id=env["tid"],
        files={"v1.mp4": b"a" * 100, "v2.mp4": b"b" * 100, "v3.mp4": b"c" * 100},
    )
    marker = read_marker(card_root)
    manifest = build_manifest(card_root)

    # Fail on the 2nd chunk to simulate a card pull mid-upload.
    failing = FailAfterNChunks(
        env["client"], fail_on_chunk=2, error=OSError("card removed"),
    )
    failing_api = ApiClient("http://server", session=failing)
    failing_api.login(TEST_USER, TEST_PASS)
    store = StateStore(tmp_path / "client_state.json")
    engine1 = UploadEngine(failing_api, store)

    with pytest.raises(UploadInterrupted):
        engine1.upload(marker, manifest, auto_release=False)

    paused = engine1.load(marker.card_uuid)
    assert paused.state == CLIENT_INTERRUPTED
    assert paused.sent_files == 1            # only the first file got through
    assert paused.safe_to_remove is False    # ⛔ nicht entfernen

    # Resume with a healthy client against the SAME server card.
    engine2 = UploadEngine(env["api"], store)
    resumed = engine2.upload(marker, manifest, auto_release=False)
    assert resumed.state == CLIENT_VERIFIED
    assert resumed.sent_files == 3

    engine2.release([marker.card_uuid])
    eingang = env["configs"]["Einzel"].paths.eingang / "ET01"
    assert len(list(eingang.iterdir())) == 3


def test_resume_is_persisted_across_engine_instances(env, tmp_path: Path) -> None:
    card_root = make_card(tmp_path / "card", tournament_id=env["tid"])
    marker = read_marker(card_root)
    manifest = build_manifest(card_root)

    # First engine uploads + verifies, then we "restart the tool".
    _engine(env, tmp_path).upload(marker, manifest, auto_release=False)
    fresh_engine = _engine(env, tmp_path)  # new instance, same state file
    loaded = fresh_engine.load(marker.card_uuid)
    assert loaded is not None
    assert loaded.state == CLIENT_VERIFIED
    assert loaded.server_card_id is not None


def test_resumable_lists_in_flight_cards(env, tmp_path: Path) -> None:
    store = StateStore(tmp_path / "client_state.json")
    store.put("u1", {"card_uuid": "u1", "table": "ET01", "discipline": "Einzel",
                     "tournament_id": 1, "expected_files": 2,
                     "expected_bytes": 2, "state": "uploading"})
    store.put("u2", {"card_uuid": "u2", "table": "ET02", "discipline": "Einzel",
                     "tournament_id": 1, "expected_files": 2,
                     "expected_bytes": 2, "state": "released"})
    engine = UploadEngine(env["api"], store)
    resumable = engine.resumable()
    assert [p.card_uuid for p in resumable] == ["u1"]


def test_manifest_mismatch_marks_failed(tmp_path: Path) -> None:
    # Scripted: start ok, one chunk ok, finish -> 422.
    marker_card = make_card(tmp_path / "card", tournament_id=1,
                            files={"v.mp4": b"x" * 10})
    marker = read_marker(marker_card)
    manifest = build_manifest(marker_card)

    s = FakeSession().queue_resp(
        FakeResp(201, {"id": 5, "state": "uploading",
                       "staging_path": "/tmp/s", "card_uuid": marker.card_uuid}),
        FakeResp(200, {"id": 5, "received_files": 1, "received_bytes": 10}),
        FakeResp(422, {"id": 5, "state": "failed",
                       "error_message": "expected 1 files, got 0"}),
    )
    api = ApiClient("http://server", session=s)
    engine = UploadEngine(api, StateStore(tmp_path / "state.json"))

    progress = engine.upload(marker, manifest, auto_release=False)
    assert progress.state == CLIENT_FAILED
    assert "expected 1 files" in (progress.error or "")


def test_failed_then_reopen_retry_verifies(tmp_path: Path) -> None:
    # start ok -> chunk ok -> finish 422 (failed); retry: reopen -> chunk ->
    # finish ok (verified). Scripted so the reopen path is deterministic.
    card_root = make_card(tmp_path / "card", tournament_id=1,
                          files={"v.mp4": b"x" * 10})
    marker = read_marker(card_root)
    manifest = build_manifest(card_root)

    s = FakeSession().queue_resp(
        FakeResp(201, {"id": 5, "state": "uploading",
                       "staging_path": "/tmp/s", "card_uuid": marker.card_uuid}),
        FakeResp(200, {"id": 5, "received_files": 1, "received_bytes": 10}),
        FakeResp(422, {"id": 5, "state": "failed",
                       "error_message": "expected 1 files, got 0"}),
        FakeResp(200, {"id": 5, "state": "uploading",
                       "staging_path": "/tmp/s", "card_uuid": marker.card_uuid}),
        FakeResp(200, {"id": 5, "received_files": 1, "received_bytes": 10}),
        FakeResp(200, {"id": 5, "state": "verified", "staging_path": "/tmp/s"}),
    )
    api = ApiClient("http://server", session=s)
    engine = UploadEngine(api, StateStore(tmp_path / "state.json"))

    first = engine.upload(marker, manifest, auto_release=False)
    assert first.state == CLIENT_FAILED

    second = engine.retry(marker, manifest, auto_release=False)
    assert second.state == CLIENT_VERIFIED
    assert any(c["url"].endswith("/reopen") for c in s.calls)


def test_server_reopen_endpoint_failed_to_uploading(env, tmp_path: Path) -> None:
    # Real server: drive a card to failed, then reopen + re-upload + verify.
    card_root = make_card(tmp_path / "card", tournament_id=env["tid"],
                          files={"v.mp4": b"x" * 10})
    marker = read_marker(card_root)
    api = env["api"]
    # Start claiming 2 files but only send 1 -> finish fails the manifest.
    started = api.start_upload(
        tournament_id=marker.tournament_id, discipline=marker.discipline,
        table_name=marker.table, expected_files=2, expected_bytes=10,
        card_uuid=marker.card_uuid,
    )
    cid = started["id"]
    import io as _io
    api.upload_chunk(cid, relative_name="v.mp4", fileobj=_io.BytesIO(b"x" * 10))
    from upload_client.api_client import ManifestRejected
    with pytest.raises(ManifestRejected):
        api.finish_upload(cid)

    reopened = api.reopen(cid)
    assert reopened["state"] == "uploading"
    # Now send the second file so the (2-file) manifest matches and verify.
    api.upload_chunk(cid, relative_name="v2.mp4", fileobj=_io.BytesIO(b""))
    # expected_bytes was 10 and we now have 10 bytes across 2 files -> ok.
    finished = api.finish_upload(cid)
    assert finished["state"] == "verified"


def test_cancel_marks_cancelled(env, tmp_path: Path) -> None:
    card_root = make_card(tmp_path / "card", tournament_id=env["tid"])
    marker = read_marker(card_root)
    engine = _engine(env, tmp_path)
    engine.upload(marker, build_manifest(card_root), auto_release=False)
    cancelled = engine.cancel(marker.card_uuid)
    assert cancelled.state == "cancelled"
