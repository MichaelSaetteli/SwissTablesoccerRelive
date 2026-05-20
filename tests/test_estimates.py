"""Tests for db.estimates - processing-time estimation from run history."""

from __future__ import annotations

import pytest

from db import db_path_for, estimate_processing, open_db, phase_rates
from db.runs import finish_run, record_phase, start_run
from db.schema import _reset_connections_for_tests
from db.tournaments import create_tournament


@pytest.fixture(autouse=True)
def _isolated_connections():
    _reset_connections_for_tests()
    yield
    _reset_connections_for_tests()


@pytest.fixture
def conn(tmp_path):
    return open_db(db_path_for(tmp_path))


def _completed_run(conn, *, input_bytes, merge_s, discipline="Doppel"):
    """Create a finished run with a single 'merge' phase of *merge_s* seconds."""
    t = create_tournament(conn, "T", date="2026-05-20")
    run = start_run(
        conn, tournament_id=t.id, discipline=discipline,
        folder_name="ET01", input_bytes=input_bytes,
    )
    record_phase(
        conn, run_id=run.id, phase="merge",
        started_at="2026-05-20T10:00:00+00:00", duration_s=merge_s,
    )
    finish_run(conn, run_id=run.id, state="done", output_bytes=input_bytes)
    return run.id


def test_calibrating_when_no_history(conn) -> None:
    est = estimate_processing(conn, 1_000_000_000)
    assert est.calibrating is True
    assert est.total_seconds is None
    assert est.per_phase == []
    assert est.sample_runs == 0


def test_estimate_uses_observed_throughput(conn) -> None:
    # 1 GB merged in 100 s -> 10 MB/s. A 2 GB job should take ~200 s.
    _completed_run(conn, input_bytes=1_000_000_000, merge_s=100.0)

    est = estimate_processing(conn, 2_000_000_000)
    assert est.calibrating is False
    assert est.sample_runs == 1
    assert est.total_seconds == pytest.approx(200.0, rel=1e-6)
    assert [p.phase for p in est.per_phase] == ["merge"]
    assert est.per_phase[0].seconds == pytest.approx(200.0, rel=1e-6)


def test_throughput_averages_over_runs(conn) -> None:
    # Two runs: 1 GB/100 s and 3 GB/100 s -> 4 GB / 200 s = 20 MB/s.
    _completed_run(conn, input_bytes=1_000_000_000, merge_s=100.0)
    _completed_run(conn, input_bytes=3_000_000_000, merge_s=100.0)

    rates = {r.phase: r for r in phase_rates(conn)}
    assert rates["merge"].sample_runs == 2
    assert rates["merge"].bytes_per_second == pytest.approx(20_000_000.0, rel=1e-6)


def test_running_runs_are_ignored(conn) -> None:
    # A run that never finished must not contribute to throughput.
    t = create_tournament(conn, "T", date="2026-05-20")
    run = start_run(
        conn, tournament_id=t.id, discipline="Doppel",
        folder_name="ET02", input_bytes=5_000_000_000,
    )
    record_phase(
        conn, run_id=run.id, phase="merge",
        started_at="2026-05-20T10:00:00+00:00", duration_s=1.0,
    )
    # left in 'running' state on purpose

    est = estimate_processing(conn, 1_000_000_000)
    assert est.calibrating is True
    assert est.sample_runs == 0


def test_discipline_filter(conn) -> None:
    _completed_run(conn, input_bytes=1_000_000_000, merge_s=100.0, discipline="Doppel")

    doppel = estimate_processing(conn, 1_000_000_000, discipline="Doppel")
    einzel = estimate_processing(conn, 1_000_000_000, discipline="Einzel")
    assert doppel.calibrating is False
    assert einzel.calibrating is True
