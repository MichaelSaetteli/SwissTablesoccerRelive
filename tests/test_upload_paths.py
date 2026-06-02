"""Tests for upload_staging.paths (INV-1 + ETxx convention)."""

from __future__ import annotations

from pathlib import Path

import pytest

from upload_staging.paths import (
    StagingPathError,
    final_path_for,
    staging_path_for,
    validate_staging_root,
)


def test_validate_staging_root_accepts_ssd():
    validate_staging_root(Path("/volume1/SDD/staging_einzel"))


def test_validate_staging_root_rejects_hdd_volume2():
    with pytest.raises(StagingPathError, match="INV-1"):
        validate_staging_root(Path("/volume2/HDD12TB/staging_einzel"))


def test_validate_staging_root_rejects_hdd_volume3():
    with pytest.raises(StagingPathError, match="INV-1"):
        validate_staging_root(Path("/volume3/HDD11TB/staging_einzel"))


def test_staging_path_for_includes_uuid(tmp_path):
    root = tmp_path / "staging_einzel"
    # tmp_path lives under /tmp, which is neither /volume2 nor /volume3 -
    # validate_staging_root accepts it. The path itself uses the uuid as
    # the leaf so two concurrent cards for the same table do not collide.
    p = staging_path_for(root, "deadbeef-uuid")
    assert p == root / "deadbeef-uuid"


def test_staging_path_for_rejects_traversal(tmp_path):
    root = tmp_path / "staging_einzel"
    with pytest.raises(StagingPathError):
        staging_path_for(root, "../escape")


def test_staging_path_for_rejects_empty_uuid(tmp_path):
    root = tmp_path / "staging_einzel"
    with pytest.raises(StagingPathError):
        staging_path_for(root, "")


def test_final_path_for_happy_path(tmp_path):
    eingang = tmp_path / "eingang_einzel"
    assert final_path_for(eingang, "ET01") == eingang / "ET01"


def test_final_path_for_rejects_lowercase_table():
    with pytest.raises(StagingPathError, match="INV-5"):
        final_path_for(Path("/volume1/SDD/eingang_einzel"), "et01")


def test_final_path_for_rejects_missing_t():
    with pytest.raises(StagingPathError, match="INV-5"):
        final_path_for(Path("/volume1/SDD/eingang_einzel"), "E01")


def test_final_path_for_rejects_three_digit_table():
    with pytest.raises(StagingPathError, match="INV-5"):
        final_path_for(Path("/volume1/SDD/eingang_einzel"), "ET101")
