"""Tests for upload_client.mounts volume-info reading (injected reader)."""

from __future__ import annotations

from pathlib import Path

from upload_client.mounts import VolumeInfo, _posix_volume, read_volume_info


def test_read_volume_info_uses_injected_reader() -> None:
    info = read_volume_info(
        Path("/media/x"), reader=lambda r: ("E01", "A1B2C3D4"),
    )
    assert isinstance(info, VolumeInfo)
    assert info.label == "E01"
    assert info.serial == "A1B2C3D4"


def test_read_volume_info_normalises_empty_to_none() -> None:
    info = read_volume_info(Path("/media/x"), reader=lambda r: ("", ""))
    assert info.label is None
    assert info.serial is None


def test_read_volume_info_survives_oserror() -> None:
    def boom(_root):
        raise OSError("device busy")
    info = read_volume_info(Path("/media/x"), reader=boom)
    assert info.label is None and info.serial is None


def test_posix_volume_uses_mount_dir_name() -> None:
    label, serial = _posix_volume(Path("/media/user/E01"))
    assert label == "E01"
    assert serial is None
