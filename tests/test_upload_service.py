"""Tests for upload_staging.service (orchestration over DB + filesystem)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from db import open_db, uploads as db_uploads
from db.schema import _reset_connections_for_tests
from db.tournaments import create_tournament
from upload_staging import service as upload_service


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


def _eingang(tmp_path):
    p = tmp_path / "eingang_einzel"
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_start_upload_creates_staging_dir(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = upload_service.start_upload(
        conn,
        eingang_root=_eingang(tmp_path),
        tournament_id=tid,
        table_name="ET01",
        discipline="Einzel",
        expected_files=2,
        expected_bytes=200,
    )
    assert card.state == db_uploads.STATE_UPLOADING
    assert Path(card.staging_path).is_dir()
    # Staging dir is the sibling of eingang on the same volume.
    assert "staging_einzel" in card.staging_path


def test_start_upload_rejects_unknown_tournament(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(upload_service.UploadServiceError, match="not found"):
        upload_service.start_upload(
            conn,
            eingang_root=_eingang(tmp_path),
            tournament_id=99999,
            table_name="ET01",
            discipline="Einzel",
            expected_files=1,
            expected_bytes=1,
        )


def test_start_upload_rejects_zero_expected(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    with pytest.raises(upload_service.UploadServiceError, match="must be > 0"):
        upload_service.start_upload(
            conn,
            eingang_root=_eingang(tmp_path),
            tournament_id=tid,
            table_name="ET01",
            discipline="Einzel",
            expected_files=0,
            expected_bytes=0,
        )


def test_start_upload_rejects_hdd_path(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    with pytest.raises(upload_service.UploadServiceError, match="INV-1"):
        upload_service.start_upload(
            conn,
            eingang_root=Path("/volume2/HDD12TB/eingang_einzel"),
            tournament_id=tid,
            table_name="ET01",
            discipline="Einzel",
            expected_files=1,
            expected_bytes=1,
        )


def test_store_chunk_writes_file_and_bumps_progress(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = upload_service.start_upload(
        conn,
        eingang_root=_eingang(tmp_path),
        tournament_id=tid, table_name="ET01", discipline="Einzel",
        expected_files=2, expected_bytes=300,
    )
    updated = upload_service.store_chunk(
        conn, card.id,
        relative_name="video_001.mp4",
        stream=io.BytesIO(b"x" * 100),
    )
    assert updated.received_files == 1
    assert updated.received_bytes == 100

    target = Path(updated.staging_path) / "video_001.mp4"
    assert target.read_bytes() == b"x" * 100


def test_store_chunk_rejects_path_traversal(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    card = upload_service.start_upload(
        conn,
        eingang_root=_eingang(tmp_path),
        tournament_id=tid, table_name="ET01", discipline="Einzel",
        expected_files=1, expected_bytes=1,
    )
    with pytest.raises(upload_service.UploadServiceError, match="traversal"):
        upload_service.store_chunk(
            conn, card.id,
            relative_name="../escape.mp4",
            stream=io.BytesIO(b"x"),
        )


def test_finish_upload_happy_path(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang = _eingang(tmp_path)
    card = upload_service.start_upload(
        conn, eingang_root=eingang,
        tournament_id=tid, table_name="ET01", discipline="Einzel",
        expected_files=2, expected_bytes=300,
    )
    upload_service.store_chunk(
        conn, card.id, relative_name="video_001.mp4",
        stream=io.BytesIO(b"x" * 100),
    )
    upload_service.store_chunk(
        conn, card.id, relative_name="video_002.mp4",
        stream=io.BytesIO(b"y" * 200),
    )

    verified = upload_service.finish_upload(
        conn, card.id, eingang_root=eingang,
    )
    assert verified.state == db_uploads.STATE_VERIFIED


def test_finish_upload_with_manifest_mismatch_marks_failed(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang = _eingang(tmp_path)
    card = upload_service.start_upload(
        conn, eingang_root=eingang,
        tournament_id=tid, table_name="ET01", discipline="Einzel",
        expected_files=2, expected_bytes=200,  # claims 200
    )
    upload_service.store_chunk(
        conn, card.id, relative_name="video_001.mp4",
        stream=io.BytesIO(b"x" * 100),
    )
    # only one file delivered while two were promised -> failed
    failed = upload_service.finish_upload(
        conn, card.id, eingang_root=eingang,
    )
    assert failed.state == db_uploads.STATE_FAILED
    assert failed.error_message is not None
    assert "files" in failed.error_message


def test_finish_upload_with_auto_release_releases_in_one_call(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang = _eingang(tmp_path)
    card = upload_service.start_upload(
        conn, eingang_root=eingang,
        tournament_id=tid, table_name="ET05", discipline="Einzel",
        expected_files=1, expected_bytes=100,
        auto_release=True,
    )
    upload_service.store_chunk(
        conn, card.id, relative_name="video_001.mp4",
        stream=io.BytesIO(b"x" * 100),
    )
    released = upload_service.finish_upload(
        conn, card.id, eingang_root=eingang,
    )
    assert released.state == db_uploads.STATE_RELEASED
    assert (eingang / "ET05" / "video_001.mp4").read_bytes() == b"x" * 100


def test_release_card_atomic_rename(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang = _eingang(tmp_path)
    card = upload_service.start_upload(
        conn, eingang_root=eingang,
        tournament_id=tid, table_name="ET07", discipline="Einzel",
        expected_files=1, expected_bytes=10,
    )
    upload_service.store_chunk(
        conn, card.id, relative_name="video.mp4",
        stream=io.BytesIO(b"x" * 10),
    )
    upload_service.finish_upload(conn, card.id, eingang_root=eingang)
    released = upload_service.release_card(
        conn, card.id, eingang_root=eingang,
    )
    assert released.state == db_uploads.STATE_RELEASED
    assert (eingang / "ET07" / "video.mp4").exists()
    assert not Path(card.staging_path).exists()


def test_release_card_collision_with_existing_eingang_folder(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang = _eingang(tmp_path)
    (eingang / "ET09").mkdir(parents=True)
    (eingang / "ET09" / "existing.mp4").write_bytes(b"old")

    card = upload_service.start_upload(
        conn, eingang_root=eingang,
        tournament_id=tid, table_name="ET09", discipline="Einzel",
        expected_files=1, expected_bytes=10,
    )
    upload_service.store_chunk(
        conn, card.id, relative_name="video.mp4",
        stream=io.BytesIO(b"x" * 10),
    )
    upload_service.finish_upload(conn, card.id, eingang_root=eingang)
    released = upload_service.release_card(
        conn, card.id, eingang_root=eingang,
    )
    # release into an existing folder is refused; card moves to failed,
    # operator decides what to do (rename old folder away, then retry).
    assert released.state == db_uploads.STATE_FAILED
    assert (eingang / "ET09" / "existing.mp4").read_bytes() == b"old"


def test_release_many_picks_eingang_by_discipline(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang_e = tmp_path / "eingang_einzel"
    eingang_e.mkdir()
    eingang_d = tmp_path / "eingang_doppel"
    eingang_d.mkdir()

    card_e = upload_service.start_upload(
        conn, eingang_root=eingang_e,
        tournament_id=tid, table_name="ET01", discipline="Einzel",
        expected_files=1, expected_bytes=10,
    )
    upload_service.store_chunk(
        conn, card_e.id, relative_name="v.mp4",
        stream=io.BytesIO(b"x" * 10),
    )
    upload_service.finish_upload(conn, card_e.id, eingang_root=eingang_e)

    card_d = upload_service.start_upload(
        conn, eingang_root=eingang_d,
        tournament_id=tid, table_name="ET02", discipline="Doppel",
        expected_files=1, expected_bytes=20,
    )
    upload_service.store_chunk(
        conn, card_d.id, relative_name="v.mp4",
        stream=io.BytesIO(b"y" * 20),
    )
    upload_service.finish_upload(conn, card_d.id, eingang_root=eingang_d)

    results = upload_service.release_many(
        conn, [card_e.id, card_d.id],
        eingang_roots={"Einzel": eingang_e, "Doppel": eingang_d},
    )
    states = [c.state for _id, c in results]
    assert states == [db_uploads.STATE_RELEASED, db_uploads.STATE_RELEASED]
    assert (eingang_e / "ET01" / "v.mp4").exists()
    assert (eingang_d / "ET02" / "v.mp4").exists()


def test_cancel_upload_removes_staging(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang = _eingang(tmp_path)
    card = upload_service.start_upload(
        conn, eingang_root=eingang,
        tournament_id=tid, table_name="ET01", discipline="Einzel",
        expected_files=1, expected_bytes=10,
    )
    upload_service.store_chunk(
        conn, card.id, relative_name="v.mp4",
        stream=io.BytesIO(b"x" * 10),
    )

    cancelled = upload_service.cancel_upload(conn, card.id)
    assert cancelled.state == db_uploads.STATE_CANCELLED
    assert not Path(card.staging_path).exists()


def test_cancel_released_card_refused(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang = _eingang(tmp_path)
    card = upload_service.start_upload(
        conn, eingang_root=eingang,
        tournament_id=tid, table_name="ET01", discipline="Einzel",
        expected_files=1, expected_bytes=10,
        auto_release=True,
    )
    upload_service.store_chunk(
        conn, card.id, relative_name="v.mp4",
        stream=io.BytesIO(b"x" * 10),
    )
    upload_service.finish_upload(conn, card.id, eingang_root=eingang)

    with pytest.raises(upload_service.UploadServiceError, match="already released"):
        upload_service.cancel_upload(conn, card.id)


def test_status_snapshot_returns_dicts(tmp_path):
    conn = _conn(tmp_path)
    tid = _tournament(conn)
    eingang = _eingang(tmp_path)
    upload_service.start_upload(
        conn, eingang_root=eingang,
        tournament_id=tid, table_name="ET01", discipline="Einzel",
        expected_files=1, expected_bytes=10,
    )
    snap = upload_service.status_snapshot(conn)
    assert len(snap) == 1
    assert snap[0]["table_name"] == "ET01"
    assert snap[0]["state"] == db_uploads.STATE_UPLOADING
