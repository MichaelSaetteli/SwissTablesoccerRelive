"""Tests for archive.archiver (rsync + SHA-256 verify)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from archive import build_archive_plan, execute_archive, sha256_of


@pytest.fixture
def fake_ssd(tmp_path: Path) -> dict:
    """Create a small SSD-side directory tree for archiving tests."""
    eingang_d = tmp_path / "ssd" / "eingang_doppel" / "ET01"
    eingang_d.mkdir(parents=True)
    (eingang_d / "src_001.mp4").write_bytes(b"hello" * 200)
    (eingang_d / "src_002.mp4").write_bytes(b"world" * 200)

    output_d = tmp_path / "ssd" / "output_doppel"
    output_d.mkdir()
    (output_d / "2026 STS2 T01 Bern Doppel.mp4").write_bytes(b"X" * 1000)

    output_e = tmp_path / "ssd" / "output_einzel"
    output_e.mkdir()
    (output_e / "2026 STS2 T01 Bern Einzel.mp4").write_bytes(b"Y" * 500)

    hdd = tmp_path / "hdd_archive"
    hdd.mkdir()

    return {
        "ssd_root": tmp_path / "ssd",
        "eingang_d": eingang_d,
        "output_d": output_d,
        "output_e": output_e,
        "hdd_root": hdd,
    }


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def test_build_archive_plan_enumerates_all_files(fake_ssd) -> None:
    plan = build_archive_plan(
        tournament_name="Bern 2026",
        archive_root=fake_ssd["hdd_root"],
        sources={
            "Doppel": {"eingang": fake_ssd["eingang_d"].parent,
                       "output": fake_ssd["output_d"]},
            "Einzel": {"output": fake_ssd["output_e"]},
        },
        date_prefix="2026-05",
    )
    assert plan.total_files == 4   # 2 eingang + 1 doppel output + 1 einzel output
    assert plan.total_bytes == (200 * 5 * 2) + 1000 + 500
    assert plan.archive_root.endswith("2026-05-Bern-2026")


def test_build_archive_plan_missing_root_raises(tmp_path: Path) -> None:
    from archive import ArchiveError
    with pytest.raises(ArchiveError):
        build_archive_plan(
            tournament_name="X",
            archive_root=tmp_path / "does-not-exist",
            sources={},
        )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

def test_execute_archive_copies_and_verifies(fake_ssd) -> None:
    plan = build_archive_plan(
        tournament_name="Bern 2026",
        archive_root=fake_ssd["hdd_root"],
        sources={
            "Doppel": {"eingang": fake_ssd["eingang_d"].parent,
                       "output": fake_ssd["output_d"]},
        },
        date_prefix="2026-05",
    )

    result = execute_archive(plan, delete_source=False)

    assert result.state == "done"
    assert result.files_verified == 3
    assert result.files_failed == 0
    assert Path(result.meta_path).is_file()

    # Source files still present.
    assert (fake_ssd["eingang_d"] / "src_001.mp4").is_file()


def test_execute_archive_with_delete_source(fake_ssd) -> None:
    plan = build_archive_plan(
        tournament_name="Bern 2026",
        archive_root=fake_ssd["hdd_root"],
        sources={"Einzel": {"output": fake_ssd["output_e"]}},
        date_prefix="2026-05",
    )
    result = execute_archive(plan, delete_source=True)
    assert result.source_deleted is True
    # Source dir was empty after delete -> rmdir succeeds.
    assert not (fake_ssd["output_e"] / "2026 STS2 T01 Bern Einzel.mp4").exists()


def test_meta_contains_youtube_links(fake_ssd) -> None:
    plan = build_archive_plan(
        tournament_name="Bern 2026",
        archive_root=fake_ssd["hdd_root"],
        sources={"Einzel": {"output": fake_ssd["output_e"]}},
        date_prefix="2026-05",
    )
    result = execute_archive(
        plan, delete_source=False,
        youtube_links={"Einzel": ["https://youtu.be/abc"]},
    )
    meta = json.loads(Path(result.meta_path).read_text())
    assert meta["youtube_links"]["Einzel"] == ["https://youtu.be/abc"]
    assert meta["checksummen_ok"] is True
    assert meta["files_verified"] == 1


def test_corrupt_target_triggers_failure_no_delete(fake_ssd, monkeypatch) -> None:
    """If a sha256 mismatch occurs the plan aborts and sources stay."""
    plan = build_archive_plan(
        tournament_name="Corrupt Test",
        archive_root=fake_ssd["hdd_root"],
        sources={"Doppel": {"output": fake_ssd["output_d"]}},
        date_prefix="2026-05",
    )
    # Patch sha256_of: return different hashes for src vs dst.
    counter = {"i": 0}
    real_sha = sha256_of

    def fake_sha(path, chunk_size=1024 * 1024):
        counter["i"] += 1
        return "a" * 64 if counter["i"] % 2 else "b" * 64
    monkeypatch.setattr("archive.archiver.sha256_of", fake_sha)

    result = execute_archive(plan, delete_source=True)
    assert result.state == "error"
    assert result.files_failed >= 1
    # Crucial: source must still be there.
    assert (fake_ssd["output_d"] / "2026 STS2 T01 Bern Doppel.mp4").is_file()


def test_sha256_of_streams_large_files(tmp_path: Path) -> None:
    """Just confirms the helper handles chunked reads without OOM symptoms."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"A" * (2 * 1024 * 1024 + 17))  # 2 MiB + 17 bytes
    h = sha256_of(big, chunk_size=128 * 1024)
    import hashlib
    expected = hashlib.sha256(big.read_bytes()).hexdigest()
    assert h == expected


def test_progress_callback_is_invoked(fake_ssd) -> None:
    plan = build_archive_plan(
        tournament_name="Cb Test",
        archive_root=fake_ssd["hdd_root"],
        sources={"Doppel": {"eingang": fake_ssd["eingang_d"].parent}},
        date_prefix="2026-05",
    )
    calls = []
    execute_archive(plan, progress=lambda *args: calls.append(args))
    assert len(calls) == plan.total_files
    # Final call: all files done.
    assert calls[-1][0] == plan.total_files
