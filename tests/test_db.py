"""Tests for the db/ package (Tournaments + Runs + Phases)."""

from __future__ import annotations

from pathlib import Path

import pytest

from db import (
    create_tournament,
    db_path_for,
    get_active_tournament,
    get_or_create_active_tournament,
    get_tournament,
    list_tournaments,
    open_db,
    set_active_tournament,
    update_tournament,
)
from db.runs import finish_run, list_runs, record_phase, start_run
from db.schema import _reset_connections_for_tests


@pytest.fixture(autouse=True)
def _isolated_connections():
    """Each test gets its own connection cache - shared SQLite state otherwise."""
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


@pytest.fixture
def conn(tmp_path):
    return open_db(db_path_for(tmp_path))


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------

def test_create_tournament_persists_all_fields(conn) -> None:
    t = create_tournament(
        conn, "Bern 2026", date="2026-05-17", location="Festhalle",
        organizer="TFCSG", youtube_channel="Swisstablesoccer",
        video_prefix="STR_2026_Bern_",
        description_template="Tischfussball Bern 2026",
        tags="tischfussball,bern,2026", max_workers=6,
    )
    assert t.id is not None
    assert t.name == "Bern 2026"
    assert t.is_auto_created is False
    assert t.created_at is not None

    re = get_tournament(conn, t.id)
    assert re is not None
    assert re.location == "Festhalle"
    assert re.max_workers == 6
    assert re.tags == "tischfussball,bern,2026"


def test_update_tournament_ignores_unknown_fields(conn) -> None:
    t = create_tournament(conn, "x")
    updated = update_tournament(
        conn, t.id, location="Bern", evil_key="should not land",
    )
    assert updated.location == "Bern"
    raw = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (t.id,),
    ).fetchone()
    assert "evil_key" not in raw.keys()


def test_list_tournaments_newest_first(conn) -> None:
    a = create_tournament(conn, "A")
    b = create_tournament(conn, "B")
    c = create_tournament(conn, "C")
    names = [t.name for t in list_tournaments(conn)]
    assert names == ["C", "B", "A"]


def test_list_tournaments_filter_archived(conn) -> None:
    a = create_tournament(conn, "A")
    b = create_tournament(conn, "B")
    update_tournament(conn, a.id, archived_at="2026-05-19T10:00:00+02:00",
                       archive_path="/volume2/HDD12TB/archiv/A/")
    all_t = list_tournaments(conn, include_archived=True)
    active = list_tournaments(conn, include_archived=False)
    assert len(all_t) == 2
    assert len(active) == 1
    assert active[0].name == "B"


# ---------------------------------------------------------------------------
# Active tournament + auto-create
# ---------------------------------------------------------------------------

def test_get_active_tournament_returns_none_initially(conn) -> None:
    assert get_active_tournament(conn, "Doppel") is None


def test_get_or_create_active_tournament_creates_when_missing(conn) -> None:
    t = get_or_create_active_tournament(conn, "Doppel")
    assert t.is_auto_created is True
    assert "Untagged" in t.name and "Doppel" in t.name
    assert get_active_tournament(conn, "Doppel").id == t.id


def test_get_or_create_active_tournament_returns_existing(conn) -> None:
    pre = create_tournament(conn, "Bern 2026", disciplines="Doppel,Einzel")
    set_active_tournament(conn, "Doppel", pre.id)
    got = get_or_create_active_tournament(conn, "Doppel")
    assert got.id == pre.id
    assert got.is_auto_created is False


def test_set_active_tournament_upserts(conn) -> None:
    a = create_tournament(conn, "A")
    b = create_tournament(conn, "B")
    set_active_tournament(conn, "Doppel", a.id)
    set_active_tournament(conn, "Doppel", b.id)
    active = get_active_tournament(conn, "Doppel")
    assert active.id == b.id


# ---------------------------------------------------------------------------
# Runs + phases
# ---------------------------------------------------------------------------

def test_start_run_then_finish_run_lifecycle(conn) -> None:
    t = create_tournament(conn, "Bern 2026")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01", input_bytes=12345)
    assert run.state == "running"
    assert run.id is not None

    finish_run(conn, run_id=run.id, state="done",
               output_bytes=10000, output_filename="out.mp4")
    runs = list_runs(conn, discipline="Doppel")
    assert len(runs) == 1
    assert runs[0].state == "done"
    assert runs[0].output_bytes == 10000


def test_record_phase_attached_to_run(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Einzel",
                    folder_name="ET07", input_bytes=1000)

    record_phase(conn, run_id=run.id, phase="move",
                 started_at="2026-05-19T10:00:00+02:00", duration_s=0.42)
    record_phase(conn, run_id=run.id, phase="merge",
                 started_at="2026-05-19T10:00:01+02:00", duration_s=15.0)

    fetched = list_runs(conn, discipline="Einzel")[0]
    assert [p.phase for p in fetched.phases] == ["move", "merge"]
    assert fetched.phases[1].duration_s == 15.0


def test_finish_run_rejects_invalid_state(conn) -> None:
    t = create_tournament(conn, "X")
    run = start_run(conn, tournament_id=t.id, discipline="Doppel",
                    folder_name="ET01")
    with pytest.raises(ValueError):
        finish_run(conn, run_id=run.id, state="cancelled")


def test_list_runs_filtered_by_tournament(conn) -> None:
    a = create_tournament(conn, "A")
    b = create_tournament(conn, "B")
    start_run(conn, tournament_id=a.id, discipline="Doppel", folder_name="ET01")
    start_run(conn, tournament_id=b.id, discipline="Doppel", folder_name="ET02")
    a_runs = list_runs(conn, tournament_id=a.id)
    assert len(a_runs) == 1 and a_runs[0].folder_name == "ET01"
