"""Processing-time estimates derived from run history (Dashboard M3 / B2).

Every finished pipeline phase is recorded in ``run_phases`` with its
``duration_s``; the owning ``runs`` row carries ``input_bytes``. Dividing
the summed input volume by the summed phase duration gives a per-phase
throughput (bytes/s) that is stable enough for stream-copy work, which is
I/O-bound. For a tournament of known size we then estimate
``input_bytes / throughput`` per phase and sum the phases.

With no usable history the estimate is ``calibrating`` (total is ``None``);
``sample_runs`` lets the UI flag a low-confidence estimate while the first
few runs accumulate.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Pre-merge + merge phases, in pipeline order. Upload throughput is handled
# separately (it depends on the live internet link, not local I/O).
PIPELINE_PHASES = ("move", "organize", "rename", "merge")


@dataclass
class PhaseRate:
    phase: str
    bytes_per_second: float
    sample_runs: int


@dataclass
class PhaseEstimate:
    phase: str
    seconds: float


@dataclass
class ProcessingEstimate:
    input_bytes: int
    total_seconds: Optional[float]
    per_phase: List[PhaseEstimate] = field(default_factory=list)
    sample_runs: int = 0
    calibrating: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["per_phase"] = [asdict(p) for p in self.per_phase]
        return d


def phase_rates(
    conn: sqlite3.Connection,
    *,
    discipline: Optional[str] = None,
) -> List[PhaseRate]:
    """Per-phase throughput (bytes/s) over completed runs with known size."""
    sql = (
        "SELECT rp.phase AS phase, "
        "       SUM(rp.duration_s) AS total_s, "
        "       SUM(r.input_bytes) AS total_bytes, "
        "       COUNT(*)           AS n "
        "FROM run_phases rp "
        "JOIN runs r ON r.id = rp.run_id "
        "WHERE r.state = 'done' AND r.input_bytes > 0"
    )
    args: List[Any] = []
    if discipline is not None:
        sql += " AND r.discipline = ?"
        args.append(discipline)
    sql += " GROUP BY rp.phase"

    rates: List[PhaseRate] = []
    for row in conn.execute(sql, args):
        total_s = float(row["total_s"] or 0.0)
        total_bytes = float(row["total_bytes"] or 0.0)
        if total_s <= 0 or total_bytes <= 0:
            continue
        rates.append(PhaseRate(
            phase=str(row["phase"]),
            bytes_per_second=total_bytes / total_s,
            sample_runs=int(row["n"] or 0),
        ))
    return rates


def _sample_run_count(
    conn: sqlite3.Connection,
    discipline: Optional[str],
) -> int:
    sql = (
        "SELECT COUNT(DISTINCT r.id) AS n "
        "FROM runs r JOIN run_phases rp ON rp.run_id = r.id "
        "WHERE r.state = 'done' AND r.input_bytes > 0"
    )
    args: List[Any] = []
    if discipline is not None:
        sql += " AND r.discipline = ?"
        args.append(discipline)
    row = conn.execute(sql, args).fetchone()
    return int(row["n"] or 0) if row else 0


def estimate_processing(
    conn: sqlite3.Connection,
    input_bytes: int,
    *,
    discipline: Optional[str] = None,
) -> ProcessingEstimate:
    """Estimate total processing seconds for *input_bytes* of new footage.

    Returns a ``calibrating`` result (``total_seconds is None``) when no
    completed run with a known size exists yet.
    """
    sample_runs = _sample_run_count(conn, discipline)
    rates = {r.phase: r for r in phase_rates(conn, discipline=discipline)}

    per_phase: List[PhaseEstimate] = []
    total = 0.0
    for phase in PIPELINE_PHASES:
        rate = rates.get(phase)
        if rate is None or rate.bytes_per_second <= 0:
            continue
        seconds = input_bytes / rate.bytes_per_second
        per_phase.append(PhaseEstimate(phase=phase, seconds=seconds))
        total += seconds

    if not per_phase:
        return ProcessingEstimate(
            input_bytes=input_bytes,
            total_seconds=None,
            per_phase=[],
            sample_runs=sample_runs,
            calibrating=True,
        )
    return ProcessingEstimate(
        input_bytes=input_bytes,
        total_seconds=total,
        per_phase=per_phase,
        sample_runs=sample_runs,
        calibrating=False,
    )
