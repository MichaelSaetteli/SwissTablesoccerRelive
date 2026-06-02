"""Tests for upload_staging.atomic_handoff."""

from __future__ import annotations

from pathlib import Path

import pytest

from upload_staging.atomic_handoff import HandoffError, atomic_release


def test_atomic_release_moves_directory(tmp_path):
    staging = tmp_path / "staging" / "abc"
    staging.mkdir(parents=True)
    (staging / "video.mp4").write_bytes(b"hello" * 100)
    final = tmp_path / "eingang" / "ET01"

    result = atomic_release(staging, final)

    assert result == final
    assert final.is_dir()
    assert (final / "video.mp4").read_bytes() == b"hello" * 100
    assert not staging.exists()


def test_atomic_release_refuses_existing_final(tmp_path):
    staging = tmp_path / "staging" / "abc"
    staging.mkdir(parents=True)
    final = tmp_path / "eingang" / "ET01"
    final.mkdir(parents=True)
    (final / "preexisting.mp4").write_bytes(b"old data")

    with pytest.raises(HandoffError, match="already exists"):
        atomic_release(staging, final)

    # Original data must be intact.
    assert (final / "preexisting.mp4").read_bytes() == b"old data"
    assert staging.is_dir()


def test_atomic_release_refuses_missing_staging(tmp_path):
    with pytest.raises(HandoffError, match="does not exist"):
        atomic_release(tmp_path / "nope", tmp_path / "eingang" / "ET01")


def test_atomic_release_creates_parent_dirs(tmp_path):
    staging = tmp_path / "staging" / "abc"
    staging.mkdir(parents=True)
    (staging / "f.mp4").write_bytes(b"x")
    final = tmp_path / "eingang" / "sub" / "deep" / "ET01"
    # eingang/sub/deep does not yet exist; release should mkdir -p them.
    result = atomic_release(staging, final)
    assert result.is_dir()
