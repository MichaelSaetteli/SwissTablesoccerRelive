"""Tests for db.control - manual pipeline control (M2 v0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from db import (
    RESTARTABLE_PHASES,
    clear_phases_for_restart,
    create_tournament,
    db_path_for,
    is_paused,
    list_jobs,
    mark_restart_started,
    open_db,
    resolve_restart_target,
    set_paused,
    set_run_paused,
    set_run_priority,
)
from db.runs import finish_run, record_phase, start_run
from db.schema import _reset_connections_for_tests


@pytest.fixture(autouse=True)
def _isolated_connections():
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


@pytest.fixture
def conn(tmp_path: Path):
    return open_db(db_path_for(tmp_path))


# ---------------------------------------------------------------------------
# Pause / resume per discipline
# ---------------------------------------------------------------------------

def test_is_paused_defaults_false(conn) -> None:
    assert is_paused(conn, "Doppel") is False


def test_set_paused_then_query(conn) -> None:
    set_paused(conn, "Doppel", True)
    assert is_paused(conn, "Doppel") is True
    assert is_paused(conn, "Einzel") is False   # independent per discipline


def test_set_paused_upsert(conn) -> None:
    set_paused(conn, "Doppel", True)
    set_paused(conn, "Doppel", False)
    set_paused(conn, "Doppel", True)
    assert is_paused(conn, "Doppel") is True
    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM pipeline_control WHERE discipline = 'Doppel'"
    ).fetchone()
    assert rows["c"] == 1     # upserts, never duplicates


# ---------------------------------------------------------------------------
# Run priority + paused flag
# ---------------------------------------------------------------------------

def test_set_run_priority_updates_value(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01")
    assert set_run_priority(conn, run.id, 5) is True
    row = conn.execute("SELECT priority FROM runs WHERE id = ?", (run.id,)).fetchone()
    assert row["priority"] == 5


def test_set_run_priority_missing_run(conn) -> None:
    assert set_run_priority(conn, 9999, 1) is False


def test_set_run_paused(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01")
    assert set_run_paused(conn, run.id, True) is True
    row = conn.execute("SELECT paused FROM runs WHERE id = ?", (run.id,)).fetchone()
    assert row["paused"] == 1
    set_run_paused(conn, run.id, False)
    row = conn.execute("SELECT paused FROM runs WHERE id = ?", (run.id,)).fetchone()
    assert row["paused"] == 0


# ---------------------------------------------------------------------------
# Restart target resolution
# ---------------------------------------------------------------------------

def test_resolve_restart_target_happy_path(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01", input_bytes=1234)
    finish_run(conn, run_id=run.id, state="error", error="boom")

    target = resolve_restart_target(
        conn, run.id, work_root="/work_doppel", from_phase="merge",
    )
    assert target is not None
    assert target.run_id == run.id
    assert target.discipline == "Doppel"
    assert target.work_path == "/work_doppel/ET01"
    assert target.from_phase == "merge"


def test_resolve_restart_target_missing_run(conn) -> None:
    assert resolve_restart_target(
        conn, 99999, work_root="/x",
    ) is None


def test_resolve_restart_target_rejects_unknown_phase(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01")
    assert resolve_restart_target(
        conn, run.id, work_root="/x", from_phase="schneiden",
    ) is None


# ---------------------------------------------------------------------------
# Phase clearing for restart
# ---------------------------------------------------------------------------

def test_clear_phases_for_restart_merge_keeps_pre_merge(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01")
    for phase, dur in (
        ("move", 0.1), ("organize", 0.2), ("rename", 0.3), ("merge", 5.0),
    ):
        record_phase(conn, run_id=run.id, phase=phase,
                     started_at="2026-05-19T10:00:00+02:00",
                     duration_s=dur)

    clear_phases_for_restart(conn, run.id, "merge")

    remaining = [
        r["phase"] for r in conn.execute(
            "SELECT phase FROM run_phases WHERE run_id = ?", (run.id,)
        )
    ]
    assert remaining == ["move", "organize", "rename"]


def test_clear_phases_for_restart_rename_clears_rename_merge_output(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01")
    for phase in ("move", "organize", "rename", "merge", "output"):
        record_phase(conn, run_id=run.id, phase=phase,
                     started_at="2026-05-19T10:00:00+02:00", duration_s=1.0)

    clear_phases_for_restart(conn, run.id, "rename")
    remaining = [
        r["phase"] for r in conn.execute(
            "SELECT phase FROM run_phases WHERE run_id = ?", (run.id,)
        )
    ]
    assert remaining == ["move", "organize"]


def test_mark_restart_started_resets_state(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01")
    finish_run(conn, run_id=run.id, state="error", error="boom")

    mark_restart_started(conn, run.id)
    row = conn.execute(
        "SELECT state, error, finished_at FROM runs WHERE id = ?",
        (run.id,),
    ).fetchone()
    assert row["state"] == "running"
    assert row["error"] is None
    assert row["finished_at"] is None


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------

def test_list_jobs_orders_running_first(conn) -> None:
    t = create_tournament(conn, "X")
    done = start_run(conn, tournament_id=t.id, discipline="Doppel",
                     folder_name="ET01")
    finish_run(conn, run_id=done.id, state="done", output_filename="o.mp4")
    err = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET02")
    finish_run(conn, run_id=err.id, state="error", error="boom")
    running = start_run(conn, tournament_id=t.id, discipline="Doppel",
                        folder_name="ET03")

    jobs = list_jobs(conn, discipline="Doppel")
    states = [j["state"] for j in jobs]
    assert states[0] == "running"
    assert "error" in states
    assert "done" in states


def test_list_jobs_filters_by_discipline(conn) -> None:
    t = create_tournament(conn, "X")
    start_run(conn, tournament_id=t.id, discipline="Doppel", folder_name="ET01")
    start_run(conn, tournament_id=t.id, discipline="Einzel", folder_name="ET02")

    doppel = list_jobs(conn, discipline="Doppel")
    einzel = list_jobs(conn, discipline="Einzel")
    assert {j["folder_name"] for j in doppel} == {"ET01"}
    assert {j["folder_name"] for j in einzel} == {"ET02"}


def test_list_jobs_states_filter(conn) -> None:
    t = create_tournament(conn, "X")
    a = start_run(conn, tournament_id=t.id, discipline="Doppel", folder_name="A")
    finish_run(conn, run_id=a.id, state="done", output_filename="a.mp4")
    b = start_run(conn, tournament_id=t.id, discipline="Doppel", folder_name="B")
    finish_run(conn, run_id=b.id, state="error", error="boom")

    only_err = list_jobs(conn, discipline="Doppel", states=["error"])
    assert [j["folder_name"] for j in only_err] == ["B"]


def test_list_jobs_priority_ordering_within_state(conn) -> None:
    t = create_tournament(conn, "X")
    runs = [
        start_run(conn, tournament_id=t.id, discipline="Doppel", folder_name=f"ET{i:02d}")
        for i in range(3)
    ]
    set_run_priority(conn, runs[0].id, 80)
    set_run_priority(conn, runs[1].id, 10)
    set_run_priority(conn, runs[2].id, 40)

    jobs = list_jobs(conn, discipline="Doppel")
    # All three are 'running' so priority is the secondary sort key.
    assert [j["priority"] for j in jobs] == [10, 40, 80]


# ---------------------------------------------------------------------------
# Restartable phases are exactly the documented set
# ---------------------------------------------------------------------------

def test_restartable_phases_constant() -> None:
    assert RESTARTABLE_PHASES == ("rename", "merge", "output")
