"""End-to-end tests for the M2 per-Job restart flow.

These tests run a real pipeline (with real ffmpeg via the e2e fixture),
sabotage one output, then re-run via ``restart_run`` to verify the
phase-restart mechanics + DB bookkeeping.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Tuple

import pytest

from db import db_path_for, list_jobs, open_db, set_paused
from db.runs import list_runs
from db.schema import _reset_connections_for_tests
from pipeline.config_loader import load_config
from tests.e2e.conftest import FIXTURE, seed_camera_folder
from watcher.pipeline_runner import PipelineRunError, restart_run, run_pipeline
from watcher.status import State


@pytest.fixture(autouse=True)
def _isolated_db():
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


@pytest.fixture(autouse=True)
def _require_ffmpeg():
    if not FIXTURE.is_file():
        pytest.skip("ffmpeg fixture missing - cannot run restart e2e tests")


def _seed_minimal_config(tmp_path: Path, discipline: str = "Doppel") -> Path:
    cfg = {
        "discipline": discipline,
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / f"eingang_{discipline.lower()}"),
            "work":    str(tmp_path / f"work_{discipline.lower()}"),
            "output":  str(tmp_path / f"output_{discipline.lower()}"),
            "logs":    str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026", "sts_nummer": "STS2",
            "turniername": "Restart", "disziplin": discipline, "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }
    path = tmp_path / f"config_{discipline.lower()}.json"
    path.write_text(json.dumps(cfg))
    return path


def _run_one_real_pipeline(tmp_path: Path) -> Tuple[Path, int]:
    """Run a real pipeline producing exactly one Run; return (config_path, run_id)."""
    cfg_path = _seed_minimal_config(tmp_path)
    cfg = load_config(cfg_path)
    seed_camera_folder(cfg.paths.eingang, "ET01", n_files=2)
    writer = run_pipeline(cfg)
    assert writer.status.state == State.DONE, writer.status.error

    conn = open_db(db_path_for(cfg_path.parent))
    runs = list_runs(conn, discipline="Doppel")
    assert len(runs) == 1
    return cfg_path, runs[0].id


# ---------------------------------------------------------------------------
# Pause / resume gates the runner
# ---------------------------------------------------------------------------

def test_pipeline_paused_blocks_run(tmp_path: Path) -> None:
    cfg_path = _seed_minimal_config(tmp_path)
    cfg = load_config(cfg_path)
    seed_camera_folder(cfg.paths.eingang, "ET01", n_files=1)

    conn = open_db(db_path_for(cfg_path.parent))
    set_paused(conn, "Doppel", True)

    with pytest.raises(PipelineRunError, match="paused"):
        run_pipeline(cfg)


def test_pipeline_resume_lets_run_proceed(tmp_path: Path) -> None:
    cfg_path = _seed_minimal_config(tmp_path)
    cfg = load_config(cfg_path)
    seed_camera_folder(cfg.paths.eingang, "ET01", n_files=1)

    conn = open_db(db_path_for(cfg_path.parent))
    set_paused(conn, "Doppel", True)
    set_paused(conn, "Doppel", False)

    writer = run_pipeline(cfg)
    assert writer.status.state == State.DONE


# ---------------------------------------------------------------------------
# Restart-at-merge: the primary 80% case
# ---------------------------------------------------------------------------

def test_restart_merge_re_produces_output(tmp_path: Path) -> None:
    cfg_path, run_id = _run_one_real_pipeline(tmp_path)
    cfg = load_config(cfg_path)

    # Sabotage: delete the output mp4 so we can see the restart re-create it.
    out_files = sorted(cfg.paths.output.glob("*.mp4"))
    assert len(out_files) == 1
    out_files[0].unlink()
    assert not list(cfg.paths.output.glob("*.mp4"))

    writer = restart_run(cfg, run_id, from_phase="merge")
    assert writer.status.state == State.DONE
    assert len(list(cfg.paths.output.glob("*.mp4"))) == 1


def test_restart_merge_clears_old_phase_record(tmp_path: Path) -> None:
    cfg_path, run_id = _run_one_real_pipeline(tmp_path)
    cfg = load_config(cfg_path)
    conn = open_db(db_path_for(cfg_path.parent))

    # Confirm pre-restart phases: move + organize + rename + merge
    phases_before = [
        r["phase"] for r in conn.execute(
            "SELECT phase FROM run_phases WHERE run_id = ? ORDER BY started_at",
            (run_id,),
        )
    ]
    assert "merge" in phases_before
    pre_merge_count = sum(1 for p in phases_before if p == "merge")
    assert pre_merge_count == 1

    restart_run(cfg, run_id, from_phase="merge")

    phases_after = [
        r["phase"] for r in conn.execute(
            "SELECT phase FROM run_phases WHERE run_id = ?", (run_id,)
        )
    ]
    # The old merge row is gone, a fresh one was written -> still 1 merge entry.
    assert sum(1 for p in phases_after if p == "merge") == 1
    # Pre-merge phases survived.
    assert "move" in phases_after and "rename" in phases_after


# ---------------------------------------------------------------------------
# Restart-at-rename: re-runs rename, then merge
# ---------------------------------------------------------------------------

def test_restart_rename_re_runs_rename_and_merge(tmp_path: Path) -> None:
    cfg_path, run_id = _run_one_real_pipeline(tmp_path)
    cfg = load_config(cfg_path)
    conn = open_db(db_path_for(cfg_path.parent))

    # Sabotage: trash the merged output AND rename a video back to a weird name
    out = sorted(cfg.paths.output.glob("*.mp4"))[0]
    out.unlink()

    # rename one of the work-folder files to a non-canonical name so rename_folder
    # has work to do.
    work_folder = cfg.paths.work / "ET01"
    vids = sorted(work_folder.glob("video_*.mp4"))
    vids[0].rename(work_folder / "weird-name.mp4")

    writer = restart_run(cfg, run_id, from_phase="rename")
    assert writer.status.state == State.DONE

    # rename re-canonicalised the file names
    canonical = sorted(work_folder.glob("video_*.mp4"))
    assert len(canonical) == 2 and not (work_folder / "weird-name.mp4").exists()
    # merge re-produced the output
    assert sorted(cfg.paths.output.glob("*.mp4"))


# ---------------------------------------------------------------------------
# Restart-at-output: pure sanity check on the existing output file
# ---------------------------------------------------------------------------

def test_restart_output_marks_done_when_file_exists(tmp_path: Path) -> None:
    cfg_path, run_id = _run_one_real_pipeline(tmp_path)
    cfg = load_config(cfg_path)
    conn = open_db(db_path_for(cfg_path.parent))

    # Move the existing run row to 'error' so we can tell the restart fixed it.
    conn.execute("UPDATE runs SET state='error', error='manual' WHERE id=?", (run_id,))
    writer = restart_run(cfg, run_id, from_phase="output")
    assert writer.status.state == State.DONE
    row = conn.execute("SELECT state, error FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["state"] == "done"
    assert row["error"] is None


def test_restart_output_fails_when_file_missing(tmp_path: Path) -> None:
    cfg_path, run_id = _run_one_real_pipeline(tmp_path)
    cfg = load_config(cfg_path)
    # Delete the output file -> restart-at-output should report error.
    for p in cfg.paths.output.glob("*.mp4"):
        p.unlink()

    writer = restart_run(cfg, run_id, from_phase="output")
    assert writer.status.state == State.ERROR


# ---------------------------------------------------------------------------
# Misuse + cross-discipline protection
# ---------------------------------------------------------------------------

def test_restart_unknown_run_raises(tmp_path: Path) -> None:
    cfg_path = _seed_minimal_config(tmp_path)
    cfg = load_config(cfg_path)
    with pytest.raises(PipelineRunError, match="not found"):
        restart_run(cfg, 99999, from_phase="merge")


def test_restart_wrong_discipline_raises(tmp_path: Path) -> None:
    doppel_path, doppel_run = _run_one_real_pipeline(tmp_path)
    einzel_path = _seed_minimal_config(tmp_path, "Einzel")
    einzel_cfg = load_config(einzel_path)

    # The Einzel config writes to the same parent dir, so it sees the same DB.
    with pytest.raises(PipelineRunError, match="belongs to Doppel"):
        restart_run(einzel_cfg, doppel_run, from_phase="merge")


def test_restart_paused_pipeline_refuses(tmp_path: Path) -> None:
    cfg_path, run_id = _run_one_real_pipeline(tmp_path)
    cfg = load_config(cfg_path)
    conn = open_db(db_path_for(cfg_path.parent))
    set_paused(conn, "Doppel", True)
    with pytest.raises(PipelineRunError, match="paused"):
        restart_run(cfg, run_id, from_phase="merge")
