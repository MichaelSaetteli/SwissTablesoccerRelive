"""Tests for db.uploads CRUD + state-machine helpers."""

from __future__ import annotations

import pytest

from db import open_db
from db.schema import _reset_connections_for_tests
from db.tournaments import create_tournament
from db.uploads import (
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_RELEASED,
    STATE_UPLOADING,
    STATE_VERIFIED,
    UploadCardError,
    create_upload_card,
    get_upload_card,
    get_upload_card_by_uuid,
    list_upload_cards,
    transition,
    update_progress,
)


@pytest.fixture(autouse=True)
def _isolated():
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


def _conn(tmp_path):
    return open_db(tmp_path / "runs.db")


def _tournament(conn):
    t = create_tournament(conn, "Seetal 2026")
    assert t.id is not None
    return t.id


def test_create_and_get_card(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = create_upload_card(
        conn, card_uuid="abc", tournament_id=tid,
        table_name="ET01", discipline="Einzel",
        expected_files=18, expected_bytes=24_000_000_000,
        staging_path=str(tmp_path / "staging" / "abc"),
    )
    assert card.id is not None
    assert card.state == STATE_UPLOADING

    fetched = get_upload_card(conn, card.id)
    assert fetched is not None
    assert fetched.card_uuid == "abc"
    assert fetched.expected_files == 18


def test_duplicate_card_uuid_raises(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    common = dict(
        card_uuid="abc", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "s"),
    )
    create_upload_card(conn, **common)
    with pytest.raises(UploadCardError, match="already exists"):
        create_upload_card(conn, **common)


def test_update_progress_does_not_change_state(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = create_upload_card(
        conn, card_uuid="abc", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=2, expected_bytes=200,
        staging_path=str(tmp_path / "s"),
    )
    updated = update_progress(
        conn, card.id, received_files=1, received_bytes=100,
    )
    assert updated is not None
    assert updated.received_files == 1
    assert updated.received_bytes == 100
    assert updated.state == STATE_UPLOADING


def test_legal_transition_uploading_to_verified(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = create_upload_card(
        conn, card_uuid="abc", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "s"),
    )
    verified = transition(conn, card.id, STATE_VERIFIED)
    assert verified.state == STATE_VERIFIED
    assert verified.released_at is None


def test_legal_transition_verified_to_released_records_final_path(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = create_upload_card(
        conn, card_uuid="abc", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "s"),
    )
    transition(conn, card.id, STATE_VERIFIED)
    released = transition(
        conn, card.id, STATE_RELEASED,
        final_path="/volume1/SDD/eingang_einzel/ET01",
    )
    assert released.state == STATE_RELEASED
    assert released.final_path == "/volume1/SDD/eingang_einzel/ET01"
    assert released.released_at is not None


def test_illegal_transition_uploading_to_released_rejected(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = create_upload_card(
        conn, card_uuid="abc", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "s"),
    )
    with pytest.raises(UploadCardError, match="illegal transition"):
        transition(conn, card.id, STATE_RELEASED)


def test_illegal_transition_released_to_anywhere_rejected(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = create_upload_card(
        conn, card_uuid="abc", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "s"),
    )
    transition(conn, card.id, STATE_VERIFIED)
    transition(conn, card.id, STATE_RELEASED)
    with pytest.raises(UploadCardError, match="illegal transition"):
        transition(conn, card.id, STATE_FAILED)


def test_failed_retry_back_to_uploading(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = create_upload_card(
        conn, card_uuid="abc", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "s"),
    )
    transition(conn, card.id, STATE_FAILED, error_message="manifest mismatch")
    failed = get_upload_card(conn, card.id)
    assert failed is not None
    assert failed.error_message == "manifest mismatch"

    retried = transition(conn, card.id, STATE_UPLOADING)
    assert retried.state == STATE_UPLOADING
    # Retry clears the prior error so the UI does not surface stale info.
    assert retried.error_message is None


def test_interrupted_path_resumable(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = create_upload_card(
        conn, card_uuid="abc", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "s"),
    )
    interrupted = transition(conn, card.id, STATE_INTERRUPTED)
    assert interrupted.state == STATE_INTERRUPTED
    resumed = transition(conn, card.id, STATE_UPLOADING)
    assert resumed.state == STATE_UPLOADING


def test_get_by_uuid(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    create_upload_card(
        conn, card_uuid="abc-uuid", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "s"),
    )
    found = get_upload_card_by_uuid(conn, "abc-uuid")
    assert found is not None
    assert found.table_name == "ET01"

    missing = get_upload_card_by_uuid(conn, "nope")
    assert missing is None


def test_list_with_filters(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    create_upload_card(
        conn, card_uuid="a", tournament_id=tid, table_name="ET01",
        discipline="Einzel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "a"),
    )
    create_upload_card(
        conn, card_uuid="b", tournament_id=tid, table_name="ET02",
        discipline="Doppel", expected_files=1, expected_bytes=1,
        staging_path=str(tmp_path / "b"),
    )
    einzel_only = list_upload_cards(conn, discipline="Einzel")
    assert {c.card_uuid for c in einzel_only} == {"a"}

    all_cards = list_upload_cards(conn)
    assert len(all_cards) == 2
