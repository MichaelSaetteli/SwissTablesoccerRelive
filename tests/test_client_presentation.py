"""Tests for upload_client.presentation (UX-as-safety-layer logic)."""

from __future__ import annotations

from upload_client.card_scanner import (
    CARD_NO_MARKER,
    CARD_READY,
    CARD_WRONG_TOURNAMENT,
    ScannedCard,
)
from upload_client.manifest import CardManifest, FileEntry
from upload_client.marker import CardMarker
from upload_client.presentation import (
    DO_NOT_REMOVE,
    SAFE_TO_REMOVE,
    badge,
    can_release,
    friendly_error,
    human_bytes,
    row_for_progress,
    row_for_scanned,
    safe_to_remove_text,
    summarise,
)
from upload_client.upload_engine import (
    CLIENT_FAILED,
    CLIENT_INTERRUPTED,
    CLIENT_PENDING,
    CLIENT_RELEASED,
    CLIENT_UPLOADING,
    CLIENT_VERIFIED,
    CardProgress,
)


def test_badges_have_symbol_and_label() -> None:
    assert "verifiziert" in badge(CLIENT_VERIFIED)
    assert "hochladen" in badge(CLIENT_UPLOADING)
    assert "Fehler" in badge(CLIENT_FAILED)


def test_safe_to_remove_only_when_verified_or_released() -> None:
    assert safe_to_remove_text(CLIENT_VERIFIED) == SAFE_TO_REMOVE
    assert safe_to_remove_text(CLIENT_RELEASED) == SAFE_TO_REMOVE
    assert safe_to_remove_text(CLIENT_UPLOADING) == DO_NOT_REMOVE
    assert safe_to_remove_text(CLIENT_PENDING) == DO_NOT_REMOVE
    assert safe_to_remove_text(CLIENT_INTERRUPTED) == DO_NOT_REMOVE


def test_can_release_only_when_verified() -> None:
    assert can_release(CLIENT_VERIFIED) is True
    for s in (CLIENT_UPLOADING, CLIENT_PENDING, CLIENT_RELEASED, CLIENT_FAILED):
        assert can_release(s) is False


def test_friendly_error_is_operator_readable() -> None:
    msg = friendly_error("ET05", "SHA-256 mismatch chunk 47/812")
    assert "ET05" in msg
    assert "erneut hochladen" in msg.lower()
    assert "sha-256" not in msg.lower()  # jargon stays behind the disclosure


def test_human_bytes() -> None:
    assert human_bytes(512) == "512 B"
    assert human_bytes(1024) == "1.0 KB"
    assert human_bytes(24 * 1024 * 1024 * 1024) == "24.0 GB"


def _progress(state: str, **kw) -> CardProgress:
    base = dict(
        card_uuid="u1", table="ET01", discipline="Einzel",
        tournament_id=1, expected_files=18, expected_bytes=24 * 1024 * 1024,
    )
    base.update(kw)
    p = CardProgress(**base)
    p.state = state
    return p


def test_row_for_progress_uploading_shows_progress() -> None:
    p = _progress(CLIENT_UPLOADING, sent=["a", "b"])
    row = row_for_progress(p)
    assert row.state == CLIENT_UPLOADING
    assert "2/18" in row.detail
    assert row.can_release is False
    assert row.safe_to_remove == DO_NOT_REMOVE


def test_row_for_progress_failed_uses_friendly_language() -> None:
    p = _progress(CLIENT_FAILED, error="expected 18 files, got 17")
    row = row_for_progress(p)
    assert row.is_error is True
    assert "erneut hochladen" in row.detail.lower()
    assert row.error_detail == "expected 18 files, got 17"  # detail preserved


def test_row_for_progress_released() -> None:
    row = row_for_progress(_progress(CLIENT_RELEASED))
    assert "Pipeline" in row.detail
    assert row.safe_to_remove == SAFE_TO_REMOVE


def _scanned_ready() -> ScannedCard:
    marker = CardMarker("u1", 1, "T", "Einzel", "ET01")
    manifest = CardManifest(
        root=None,  # type: ignore[arg-type]
        files=(FileEntry(path=None, relative_name="a.mp4", size=100),),  # type: ignore[arg-type]
    )
    return ScannedCard(root=None, status=CARD_READY, marker=marker,  # type: ignore[arg-type]
                       manifest=manifest)


def test_row_for_scanned_ready_is_pending() -> None:
    row = row_for_scanned(_scanned_ready())
    assert row.state == CLIENT_PENDING
    assert "0/1" in row.detail
    assert row.is_error is False


def test_row_for_scanned_locked_card_shows_reason() -> None:
    marker = CardMarker("u9", 99, "Other", "Einzel", "ET09")
    card = ScannedCard(root=None, status=CARD_WRONG_TOURNAMENT,  # type: ignore[arg-type]
                       marker=marker, reason="falsches Turnier")
    row = row_for_scanned(card)
    assert row.is_error is True
    assert row.can_release is False
    assert "gesperrt" in row.badge.lower()


def test_summary_header_and_all_done() -> None:
    states = (
        [CLIENT_VERIFIED] * 3 + [CLIENT_RELEASED] * 2
        + [CLIENT_UPLOADING] * 1 + [CLIENT_PENDING] * 1 + [CLIENT_FAILED] * 1
    )
    s = summarise("Einzel", states, expected=10)
    assert s.done == 5            # 3 verified + 2 released
    assert s.uploading == 1
    assert s.waiting == 1
    assert s.failed == 1
    header = s.header_text()
    assert "Einzel" in header and "5 / 10" in header and "Fehler" in header
    assert s.all_done is False


def test_summary_all_done_when_all_released() -> None:
    s = summarise("Doppel", [CLIENT_RELEASED] * 4, expected=4)
    assert s.all_done is True
