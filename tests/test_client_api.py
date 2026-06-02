"""Unit tests for upload_client.api_client request shaping + error paths.

These use a scripted fake session so the deterministic error paths (auth
failure, 422 manifest rejection, generic 4xx) are covered without a live
server. The happy-path round-trip against the REAL server lives in
test_client_engine.py.
"""

from __future__ import annotations

import io

import pytest

from tests.client_helpers import FakeResp, FakeSession
from upload_client.api_client import (
    ApiClient,
    ApiError,
    AuthError,
    ManifestRejected,
)


def _api(session: FakeSession) -> ApiClient:
    return ApiClient("http://server", session=session)


def test_login_success_on_redirect() -> None:
    s = FakeSession().queue_resp(FakeResp(302))
    _api(s).login("admin", "pw")
    call = s.calls[0]
    assert call["url"] == "http://server/login"
    assert call["data"] == {"username": "admin", "password": "pw"}
    assert call["allow_redirects"] is False


def test_login_failure_raises_auth() -> None:
    s = FakeSession().queue_resp(FakeResp(200))  # 200 = login page re-rendered
    with pytest.raises(AuthError):
        _api(s).login("admin", "bad")


def test_start_upload_sends_body_and_returns_card() -> None:
    s = FakeSession().queue_resp(FakeResp(201, {"id": 7, "state": "uploading"}))
    card = _api(s).start_upload(
        tournament_id=42, discipline="Einzel", table_name="ET01",
        expected_files=2, expected_bytes=300, auto_release=True,
        card_uuid="u1",
    )
    assert card["id"] == 7
    body = s.calls[0]["json"]
    assert body["tournament_id"] == 42
    assert body["auto_release"] is True
    assert body["card_uuid"] == "u1"


def test_start_upload_omits_card_uuid_when_none() -> None:
    s = FakeSession().queue_resp(FakeResp(201, {"id": 1}))
    _api(s).start_upload(
        tournament_id=1, discipline="Einzel", table_name="ET01",
        expected_files=1, expected_bytes=1,
    )
    assert "card_uuid" not in s.calls[0]["json"]


def test_upload_chunk_multipart_shape() -> None:
    s = FakeSession().queue_resp(FakeResp(200, {"received_files": 1}))
    _api(s).upload_chunk(
        7, relative_name="v.mp4", fileobj=io.BytesIO(b"x"), filename="v.mp4",
    )
    call = s.calls[0]
    assert call["url"] == "http://server/api/upload/7/chunk"
    assert call["data"] == {"relative_name": "v.mp4"}
    assert "file" in call["files"]


def test_finish_422_raises_manifest_rejected() -> None:
    failed = {"id": 7, "state": "failed",
              "error_message": "expected 2 files, got 1"}
    s = FakeSession().queue_resp(FakeResp(422, failed))
    with pytest.raises(ManifestRejected) as ei:
        _api(s).finish_upload(7)
    assert ei.value.card["state"] == "failed"
    assert "expected 2 files" in str(ei.value)


def test_finish_success() -> None:
    s = FakeSession().queue_resp(FakeResp(200, {"id": 7, "state": "verified"}))
    assert _api(s).finish_upload(7)["state"] == "verified"


def test_release_returns_released_list() -> None:
    s = FakeSession().queue_resp(
        FakeResp(200, {"released": [{"id": 7, "state": "released"}]})
    )
    out = _api(s).release([7])
    assert out[0]["state"] == "released"
    assert s.calls[0]["json"] == {"card_ids": [7]}


def test_reopen_posts_and_returns_card() -> None:
    s = FakeSession().queue_resp(FakeResp(200, {"id": 7, "state": "uploading"}))
    card = _api(s).reopen(7)
    assert card["state"] == "uploading"
    assert s.calls[0]["url"] == "http://server/api/upload/7/reopen"


def test_status_filters_and_unwraps() -> None:
    s = FakeSession().queue_resp(FakeResp(200, {"cards": [{"id": 1}]}))
    out = _api(s).status(discipline="Einzel")
    assert out == [{"id": 1}]
    assert s.calls[0]["params"] == {"discipline": "Einzel"}


def test_generic_error_raises_api_error_with_message() -> None:
    s = FakeSession().queue_resp(FakeResp(400, {"error": "no DB"}))
    with pytest.raises(ApiError) as ei:
        _api(s).active_tournament()
    assert ei.value.status_code == 400
    assert "no DB" in str(ei.value)
