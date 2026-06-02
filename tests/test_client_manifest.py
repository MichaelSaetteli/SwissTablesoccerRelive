"""Tests for upload_client.manifest, incl. parity with the server."""

from __future__ import annotations

from pathlib import Path

from upload_client import manifest as client_manifest
from upload_client.manifest import build_manifest
from upload_staging import manifest as server_manifest


def _touch(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_ignore_rules_match_server_exactly() -> None:
    # The whole point of basing Slice 2 on the server branch: catch drift.
    assert client_manifest.IGNORE_NAMES == server_manifest.IGNORE_NAMES
    assert client_manifest.IGNORE_SUFFIXES == server_manifest.IGNORE_SUFFIXES


def test_counts_and_bytes(tmp_path: Path) -> None:
    _touch(tmp_path / "a.mp4", b"a" * 10)
    _touch(tmp_path / "b.mp4", b"b" * 20)
    man = build_manifest(tmp_path)
    assert man.total_files == 2
    assert man.total_bytes == 30
    assert not man.is_empty


def test_housekeeping_files_ignored(tmp_path: Path) -> None:
    _touch(tmp_path / "a.mp4", b"a" * 10)
    _touch(tmp_path / ".sts-card.json", b"{}")
    _touch(tmp_path / ".upload_meta.json", b"{}")
    _touch(tmp_path / "b.mp4.partial", b"junk")
    man = build_manifest(tmp_path)
    assert man.total_files == 1
    assert man.total_bytes == 10


def test_empty_card(tmp_path: Path) -> None:
    _touch(tmp_path / ".sts-card.json", b"{}")
    man = build_manifest(tmp_path)
    assert man.is_empty
    assert man.total_files == 0


def test_dcim_is_flattened(tmp_path: Path) -> None:
    _touch(tmp_path / "DCIM" / "100PANA" / "S001.mp4", b"x" * 5)
    _touch(tmp_path / "DCIM" / "100PANA" / "S002.mp4", b"y" * 5)
    man = build_manifest(tmp_path)
    names = sorted(f.relative_name for f in man.files)
    assert names == ["S001.mp4", "S002.mp4"]
    assert all("/" not in n for n in names)


def test_dcim_collisions_are_decollided_deterministically(tmp_path: Path) -> None:
    # Panasonic restarts numbering per sub-folder -> same basename twice.
    _touch(tmp_path / "DCIM" / "100PANA" / "S0001.mp4", b"x" * 5)
    _touch(tmp_path / "DCIM" / "101PANA" / "S0001.mp4", b"y" * 5)
    names1 = [f.relative_name for f in build_manifest(tmp_path).files]
    names2 = [f.relative_name for f in build_manifest(tmp_path).files]
    assert names1 == names2          # deterministic -> resume-safe
    assert len(set(names1)) == 2     # unique
    assert names1 == sorted(names1)  # lexical sort == walk order (merge order)
    assert all("/" not in n for n in names1)


def test_flatten_preserves_walk_order_across_subfolders(tmp_path: Path) -> None:
    # Two sessions; the merge must stay chronological: 100PANA before 101PANA.
    _touch(tmp_path / "DCIM" / "100PANA" / "S0001.mp4", b"a")
    _touch(tmp_path / "DCIM" / "100PANA" / "S0002.mp4", b"b")
    _touch(tmp_path / "DCIM" / "101PANA" / "S0001.mp4", b"c")
    files = build_manifest(tmp_path).files
    # The lexical order of flat names must follow the walk order, so the
    # original 100PANA files come before the 101PANA file after sorting.
    ordered = [f.relative_name for f in files]
    assert ordered == sorted(ordered)
    # First file maps to 100PANA/S0001, last to 101PANA/S0001.
    assert files[0].path.parent.name == "100PANA"
    assert files[-1].path.parent.name == "101PANA"


def test_no_flatten_keeps_relative_paths(tmp_path: Path) -> None:
    _touch(tmp_path / "DCIM" / "100PANA" / "S001.mp4", b"x" * 5)
    man = build_manifest(tmp_path, flatten=False)
    assert man.files[0].relative_name == "DCIM/100PANA/S001.mp4"


# ---------------------------------------------------------------------------
# Video-only whitelist + DCIM sub-folder selection (new ingest model)
# ---------------------------------------------------------------------------

def test_only_video_files_are_uploaded(tmp_path: Path) -> None:
    # Camera housekeeping in DCIM must never be uploaded.
    _touch(tmp_path / "DCIM" / "100PANA" / "v.mp4", b"x" * 10)
    _touch(tmp_path / "DCIM" / "BACKUP.HST", b"junk")
    _touch(tmp_path / "DCIM" / "INDEX.DAT", b"junk")
    man = build_manifest(tmp_path)
    assert man.total_files == 1
    assert [Path(f.relative_name).name for f in man.files] == ["v.mp4"]


def test_video_only_can_be_disabled(tmp_path: Path) -> None:
    _touch(tmp_path / "DCIM" / "100PANA" / "v.mp4", b"x" * 10)
    _touch(tmp_path / "DCIM" / "100PANA" / "note.txt", b"junk")
    man = build_manifest(tmp_path, video_only=False)
    assert man.total_files == 2


def test_selected_subdirs_filters_dcim(tmp_path: Path) -> None:
    _touch(tmp_path / "DCIM" / "OLD" / "a.mp4", b"x" * 5)
    _touch(tmp_path / "DCIM" / "NEW" / "b.mp4", b"y" * 5)
    man = build_manifest(tmp_path, selected_subdirs={"NEW"})
    names = [Path(f.relative_name).name for f in man.files]
    assert names == ["b.mp4"]


def test_selected_subdirs_keeps_non_dcim_video(tmp_path: Path) -> None:
    # A loose .mp4 at the card root is not under DCIM -> always kept.
    _touch(tmp_path / "DCIM" / "OLD" / "a.mp4", b"x" * 5)
    _touch(tmp_path / "root.mp4", b"z" * 5)
    man = build_manifest(tmp_path, selected_subdirs={"NEW"})
    names = sorted(Path(f.relative_name).name for f in man.files)
    assert names == ["root.mp4"]


def test_manifest_captures_mtime(tmp_path: Path) -> None:
    p = tmp_path / "v.mp4"
    _touch(p, b"x" * 5)
    man = build_manifest(tmp_path)
    assert man.files[0].mtime > 0
