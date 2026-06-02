"""Tests for upload_staging.manifest (count + bytes verification)."""

from __future__ import annotations

from pathlib import Path

import pytest

from upload_staging.manifest import (
    ManifestError,
    measure_staging,
    verify_manifest,
)


def _write(path: Path, name: str, data: bytes) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_bytes(data)


def test_measure_staging_counts_files_and_bytes(tmp_path):
    _write(tmp_path, "a.mp4", b"x" * 1024)
    _write(tmp_path, "b.mp4", b"y" * 2048)
    result = measure_staging(tmp_path)
    assert result.files_seen == 2
    assert result.bytes_seen == 3072


def test_measure_staging_recurses(tmp_path):
    sub = tmp_path / "DCIM" / "100PANA"
    _write(sub, "S001.MP4", b"z" * 512)
    result = measure_staging(tmp_path)
    assert result.files_seen == 1
    assert result.bytes_seen == 512


def test_measure_staging_ignores_sts_card_marker(tmp_path):
    _write(tmp_path, ".sts-card.json", b'{"table":"ET01"}')
    _write(tmp_path, "video.mp4", b"x" * 100)
    result = measure_staging(tmp_path)
    assert result.files_seen == 1
    assert result.bytes_seen == 100


def test_measure_staging_ignores_partial_chunks(tmp_path):
    _write(tmp_path, "video.mp4", b"x" * 100)
    _write(tmp_path, "video2.mp4.partial", b"y" * 50)
    result = measure_staging(tmp_path)
    assert result.files_seen == 1


def test_verify_manifest_happy_path(tmp_path):
    _write(tmp_path, "a.mp4", b"x" * 1024)
    _write(tmp_path, "b.mp4", b"y" * 2048)
    result = verify_manifest(
        tmp_path, expected_files=2, expected_bytes=3072,
    )
    assert result.files_seen == 2


def test_verify_manifest_file_count_mismatch(tmp_path):
    _write(tmp_path, "a.mp4", b"x" * 100)
    with pytest.raises(ManifestError, match="2 files"):
        verify_manifest(tmp_path, expected_files=2, expected_bytes=100)


def test_verify_manifest_byte_count_mismatch(tmp_path):
    _write(tmp_path, "a.mp4", b"x" * 100)
    with pytest.raises(ManifestError, match="200 bytes"):
        verify_manifest(tmp_path, expected_files=1, expected_bytes=200)


def test_verify_manifest_both_mismatches_listed(tmp_path):
    _write(tmp_path, "a.mp4", b"x" * 100)
    with pytest.raises(ManifestError) as exc_info:
        verify_manifest(tmp_path, expected_files=3, expected_bytes=500)
    msg = str(exc_info.value)
    assert "3 files" in msg
    assert "500 bytes" in msg
