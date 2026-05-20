"""Tests for archive.tiering (SSD->HDD staging + retention sweep)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from archive import stage_to_hdd, sweep_staging
from archive.tiering import staged_age_epoch


def _write(path: Path, name: str, data: bytes) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    f = path / name
    f.write_bytes(data)
    return f


# ---------------------------------------------------------------------------
# stage_to_hdd
# ---------------------------------------------------------------------------

def test_stage_moves_and_verifies_then_deletes_source(tmp_path):
    work = tmp_path / "ssd" / "work_doppel" / "ET01"
    _write(work, "video_001.mp4", b"A" * 5000)
    _write(work, "video_002.mp4", b"B" * 3000)
    staging = tmp_path / "hdd" / "staging"

    res = stage_to_hdd(
        tournament_name="Bern 2026", discipline="Doppel", role="work",
        source_dir=tmp_path / "ssd" / "work_doppel", staging_root=staging,
    )

    assert res.state == "done"
    assert res.files_verified == 2
    assert res.source_deleted is True
    # source emptied
    assert not (work / "video_001.mp4").exists()
    # files present on the HDD side
    staged_root = Path(res.plan.archive_root)
    staged = list(staged_root.rglob("video_*.mp4"))
    assert len(staged) == 2


def test_stage_rejects_unknown_role(tmp_path):
    with pytest.raises(ValueError):
        stage_to_hdd(
            tournament_name="T", discipline="Doppel", role="eingang",
            source_dir=tmp_path, staging_root=tmp_path / "staging",
        )


def test_stage_empty_source_is_noop_done(tmp_path):
    src = tmp_path / "ssd" / "output_doppel"
    src.mkdir(parents=True)
    staging = tmp_path / "hdd" / "staging"
    res = stage_to_hdd(
        tournament_name="T", discipline="Doppel", role="output",
        source_dir=src, staging_root=staging,
    )
    assert res.state == "done"
    assert res.files_verified == 0


# ---------------------------------------------------------------------------
# sweep_staging
# ---------------------------------------------------------------------------

def test_sweep_deletes_only_expired_folders(tmp_path):
    staging = tmp_path / "staging"
    old = staging / "2026-04-old"
    fresh = staging / "2026-05-fresh"
    _write(old, "a.mp4", b"x" * 1000)
    _write(fresh, "b.mp4", b"y" * 2000)

    now = time.time()
    # old: staged 10 days ago; fresh: staged 1 day ago
    (old / "archiv_meta.json").write_text(json.dumps({
        "archiviert_am": _iso(now - 10 * 86400),
    }))
    (fresh / "archiv_meta.json").write_text(json.dumps({
        "archiviert_am": _iso(now - 1 * 86400),
    }))

    res = sweep_staging(staging, retention_days=7, now=now)

    assert res.scanned == 2
    assert res.deleted == [str(old)]
    # >= the a.mp4 payload; the folder's archiv_meta.json adds a few bytes
    assert res.bytes_freed >= 1000
    assert not old.exists()
    assert fresh.exists()


def test_sweep_uses_dir_mtime_when_no_meta(tmp_path):
    staging = tmp_path / "staging"
    folder = staging / "2026-04-nometa"
    _write(folder, "a.mp4", b"x" * 100)
    # backdate the directory mtime to 30 days ago
    old_epoch = time.time() - 30 * 86400
    import os
    os.utime(folder, (old_epoch, old_epoch))

    res = sweep_staging(staging, retention_days=7)
    assert str(folder) in res.deleted
    assert not folder.exists()


def test_sweep_missing_root_is_safe(tmp_path):
    res = sweep_staging(tmp_path / "does-not-exist", retention_days=7)
    assert res.deleted == []
    assert res.scanned == 0


def test_staged_age_prefers_freigegeben_timestamp(tmp_path):
    folder = tmp_path / "t"
    folder.mkdir()
    now = time.time()
    (folder / "archiv_meta.json").write_text(json.dumps({
        "archiviert_am": _iso(now - 10 * 86400),
        "ssd_freigegeben_am": _iso(now - 2 * 86400),
    }))
    age = staged_age_epoch(folder)
    # should match the freigegeben (newer) timestamp, not archiviert
    assert age == pytest.approx(now - 2 * 86400, abs=2)


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
