"""Pipeline orchestrator.

Runs the four engine steps in order and keeps the on-disk status file
in sync. Designed to be invoked either:

  * In-process (folder_watcher imports ``run_pipeline``)
  * As a CLI subprocess: ``python -m watcher.pipeline_runner <config.json>``

The runner is **idempotent and thread-safe per discipline**: a per-config
threading lock prevents two parallel invocations of the same pipeline.
Independent disciplines (Doppel/Einzel) are unaffected by each other.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pipeline.MoveFiles import move_path
from pipeline.config_loader import (
    PipelineConfig,
    ensure_pipeline_dirs,
    load_config,
)
from pipeline.merge_ffmpeg import MergeResult, merge_all
from pipeline.organize_folders import organize_root
from pipeline.rename_mp4 import rename_root
from pipeline.status_file import now_iso

from .status import State, StatusWriter, status_path_for

sys.stdout.reconfigure(encoding="utf-8")


# Per-discipline locks so two concurrent triggers cannot race.
_run_locks: Dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _lock_for(config: PipelineConfig) -> threading.Lock:
    with _locks_lock:
        return _run_locks.setdefault(config.discipline, threading.Lock())


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_folders(eingang: Path) -> List[Path]:
    """Return ET-folders directly inside *eingang*, sorted by name."""
    if not eingang.is_dir():
        return []
    return sorted(p for p in eingang.iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# Step wrappers (kept tiny so the runner reads top-down)
# ---------------------------------------------------------------------------

def _step_move(folders: Sequence[Path], work_dir: Path) -> List[Path]:
    moved_folders: List[Path] = []
    for folder in folders:
        target = work_dir / folder.name
        move_path(folder, target)
        moved_folders.append(target)
    return moved_folders


# Stream-copy concat means output bytes ~= sum of input bytes. We add a
# 5 % safety cushion to cover MP4 container overhead and stay clear of
# edge-of-volume "no space" errors that would crash a 30-minute merge.
DISK_SPACE_SAFETY_FACTOR = 1.05


def _bytes_under(folders: Sequence[Path]) -> int:
    """Sum the size of every regular file under *folders*."""
    total = 0
    for folder in folders:
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    return total


def check_disk_space(folders: Sequence[Path], output_dir: Path) -> None:
    """Raise ``PipelineRunError`` if the output volume is too tight.

    Briefing s.1 says one run produces 1-2 TB. Running out of disk
    halfway through ffmpeg leaves behind a corrupt ``.partial`` and
    wastes 30+ minutes of work; a 1-second pre-flight is well worth it.
    """
    needed = int(_bytes_under(folders) * DISK_SPACE_SAFETY_FACTOR)
    output_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(output_dir).free
    if needed > free:
        gb = lambda n: f"{n / 1024**3:.1f} GB"
        raise PipelineRunError(
            f"Not enough free space on {output_dir}: "
            f"need ~{gb(needed)} (incl. 5 % cushion), have {gb(free)}"
        )


# ---------------------------------------------------------------------------
# DB persistence helpers (optional - run_pipeline works without a DB)
# ---------------------------------------------------------------------------

class _NullRecorder:
    """No-op recorder used when no db_path is configured."""
    tournament_id: int = 0

    def start_run(self, folder_name: str, input_bytes: int = 0) -> Optional[int]:
        return None

    def record_phase(self, run_id: Optional[int], phase: str,
                     started_at: str, duration_s: float) -> None:
        pass

    def finish_run(self, run_id: Optional[int], state: str,
                   output_bytes: int = 0, output_filename: str = "",
                   error: Optional[str] = None) -> None:
        pass


class _DBRecorder:
    """Writes Tournament + Run + Phase rows to SQLite for the Dashboard."""

    def __init__(self, db_path: Path, discipline: str) -> None:
        # Imported here so a missing db/ package would never crash the
        # core pipeline; the recorder is only constructed when db_path is set.
        from db import (
            get_or_create_active_tournament,
            open_db,
        )
        self._db = open_db(db_path)
        self._discipline = discipline
        tournament = get_or_create_active_tournament(self._db, discipline)
        assert tournament.id is not None
        self.tournament_id = tournament.id

    def start_run(self, folder_name: str, input_bytes: int = 0) -> Optional[int]:
        from db.runs import start_run
        run = start_run(
            self._db,
            tournament_id=self.tournament_id,
            discipline=self._discipline,
            folder_name=folder_name,
            input_bytes=input_bytes,
        )
        return run.id

    def record_phase(self, run_id: Optional[int], phase: str,
                     started_at: str, duration_s: float) -> None:
        if run_id is None:
            return
        from db.runs import record_phase
        record_phase(self._db, run_id=run_id, phase=phase,
                     started_at=started_at, duration_s=duration_s)

    def finish_run(self, run_id: Optional[int], state: str,
                   output_bytes: int = 0, output_filename: str = "",
                   error: Optional[str] = None) -> None:
        if run_id is None:
            return
        from db.runs import finish_run
        finish_run(self._db, run_id=run_id, state=state,
                   output_bytes=output_bytes,
                   output_filename=output_filename, error=error)


@contextmanager
def _timed_phase():
    """Context manager that records start time + duration of a code block."""
    started = now_iso()
    t0 = time.monotonic()
    state = {"started_at": started, "duration_s": 0.0}
    try:
        yield state
    finally:
        state["duration_s"] = time.monotonic() - t0


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

class PipelineRunError(RuntimeError):
    """Raised when the pipeline cannot complete a run."""


def _resolve_db_path(config: PipelineConfig) -> Optional[Path]:
    """The DB lives next to the config files; if source_path is missing
    we run without persistence (tests, ad-hoc CLI usage)."""
    if config.source_path is None:
        return None
    return config.source_path.parent / "runs.db"


def run_pipeline(
    config: PipelineConfig,
    *,
    db_path: Optional[Path] = None,
) -> StatusWriter:
    """Run the full pipeline once for *config*.

    The function blocks until the run finishes and returns the live
    StatusWriter so callers can inspect the final state.

    Concurrency: a per-discipline lock guarantees only one run is active
    at a time for the same discipline. Calling it again while a run is in
    progress raises ``PipelineRunError`` immediately (does not queue).
    """
    if not config.enabled:
        raise PipelineRunError(
            f"Pipeline for {config.discipline} is disabled in config"
        )

    # M2: honour the per-discipline pause flag. We check this BEFORE
    # grabbing the per-discipline lock so a paused pipeline never
    # appears as "already running" when the operator inspects state.
    effective_db = db_path if db_path is not None else _resolve_db_path(config)
    if effective_db is not None:
        try:
            from db import is_paused, open_db
            if is_paused(open_db(effective_db), config.discipline):
                raise PipelineRunError(
                    f"Pipeline for {config.discipline} is paused - "
                    f"resume it in the UI before triggering a run"
                )
        except PipelineRunError:
            raise
        except Exception:
            # Stale DB or transient error: log via writer, do not block.
            pass

    lock = _lock_for(config)
    if not lock.acquire(blocking=False):
        raise PipelineRunError(
            f"Pipeline for {config.discipline} is already running"
        )

    writer = StatusWriter(status_path_for(config), config.discipline)

    # Build a recorder if DB persistence is requested (Dashboard support).
    # Resolution order: explicit db_path arg > derived from config dir.
    recorder: "_NullRecorder|_DBRecorder"
    if effective_db is not None:
        try:
            recorder = _DBRecorder(effective_db, config.discipline)
        except Exception as exc:
            # DB failure must never block the pipeline - degrade to no-op.
            writer.append_log(f"DB persistence disabled: {exc}")
            recorder = _NullRecorder()
    else:
        recorder = _NullRecorder()

    try:
        ensure_pipeline_dirs(config)

        folders = detect_folders(config.paths.eingang)
        if not folders:
            writer.update(state=State.IDLE)
            writer.append_log("No folders detected in eingang - run skipped")
            return writer

        # Pre-flight: refuse to start if the output volume cannot hold the
        # result. Cheap (one stat per file + one statvfs) and saves 30+
        # minutes of ffmpeg time on a doomed run.
        try:
            check_disk_space(folders, config.paths.output)
        except PipelineRunError as exc:
            writer.fail_run(str(exc))
            writer.append_log(f"Aborted: {exc}")
            raise

        writer.begin_run([f.name for f in folders])
        writer.append_log(f"Detected {len(folders)} folder(s): "
                          f"{[f.name for f in folders]}")

        # --- Step 1: move eingang -> work
        writer.update(state=State.MOVING)
        with _timed_phase() as move_t:
            moved_folders = _step_move(folders, config.paths.work)
        writer.append_log(
            f"Moved {len(moved_folders)} folder(s) to work "
            f"in {move_t['duration_s']:.2f}s"
        )

        # --- Step 2: organize (split >24)
        writer.update(state=State.ORGANIZING)
        with _timed_phase() as org_t:
            prepared = organize_root(
                config.paths.work,
                max_files=config.max_files_per_folder,
            )
        writer.append_log(
            f"Organized into {len(prepared)} folder(s) "
            f"in {org_t['duration_s']:.2f}s: "
            f"{[p.name for p in prepared]}"
        )

        # --- Step 3: rename to video_NNN.mp4
        writer.update(state=State.RENAMING)
        with _timed_phase() as rn_t:
            renamed = rename_root(config.paths.work)
        writer.append_log(
            f"Renamed {len(renamed)} mp4 file(s) "
            f"in {rn_t['duration_s']:.2f}s"
        )

        # --- Per-folder Run rows: one row per output file we are about
        # to produce. Pre-merge phases (move/organize/rename) are recorded
        # against every Run because they are shared overhead.
        run_id_by_folder: Dict[str, Optional[int]] = {}
        for prep_folder in prepared:
            input_bytes = sum(
                (f.stat().st_size for f in prep_folder.iterdir()
                 if f.is_file() and f.suffix.lower() == ".mp4"),
                0,
            )
            rid = recorder.start_run(prep_folder.name, input_bytes=input_bytes)
            run_id_by_folder[prep_folder.name] = rid
            for phase, timing in (
                ("move", move_t), ("organize", org_t), ("rename", rn_t),
            ):
                recorder.record_phase(
                    rid, phase,
                    timing["started_at"], timing["duration_s"],
                )

        # --- Step 4: ffmpeg merge (parallel)
        writer.update(state=State.MERGING)
        results = merge_all(prepared, config)

        failed = [r for r in results if not r.success]
        succeeded = [r for r in results if r.success]

        # Per-folder merge timing -> Run.run_phases + Run finish state.
        for r in results:
            rid = run_id_by_folder.get(r.folder.name)
            if r.started_at is not None:
                recorder.record_phase(
                    rid, "merge", r.started_at, r.duration_s,
                )
            if r.success:
                recorder.finish_run(
                    rid, "done",
                    output_bytes=r.output_bytes,
                    output_filename=r.output.name,
                )
            else:
                recorder.finish_run(
                    rid, "error",
                    error=f"rc={r.returncode}",
                )

        for r in succeeded:
            log_hint = f" [log: {r.log_path.name}]" if r.log_path else ""
            writer.append_log(
                f"OK     {r.folder.name} -> {r.output.name} "
                f"({r.duration_s:.1f}s){log_hint}"
            )
        for r in failed:
            log_hint = f" [log: {r.log_path}]" if r.log_path else ""
            writer.append_log(
                f"FAIL   {r.folder.name} rc={r.returncode}{log_hint}"
            )

        if failed:
            error_summary = (
                f"{len(failed)} of {len(results)} merge(s) failed: "
                f"{[r.folder.name for r in failed]}"
            )
            writer.fail_run(error_summary)
            return writer

        writer.update(folders_processed=[r.folder.name for r in succeeded])
        writer.finish_run([r.output.name for r in succeeded])
        return writer

    except PipelineRunError:
        raise
    except Exception as exc:
        writer.fail_run(f"{type(exc).__name__}: {exc}")
        writer.append_log("Traceback: " + traceback.format_exc().splitlines()[-1])
        raise PipelineRunError(str(exc)) from exc
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-Job restart (M2)
# ---------------------------------------------------------------------------

def restart_run(
    config: PipelineConfig,
    run_id: int,
    *,
    from_phase: str = "merge",
) -> StatusWriter:
    """Re-execute the pipeline for a single existing Run, starting from
    *from_phase*. Useful when a single ffmpeg merge failed and the operator
    wants to retry without re-processing every other folder.

    Phases supported:
      * ``rename`` - re-run rename_folder, then merge
      * ``merge``  - re-run merge_folder only (the default)
      * ``output`` - just verify the output file exists (sanity check)

    The work directory must still contain the prepared
    ``video_*.mp4`` files - the runner does not move the folder back
    out of work/.
    """
    from db import (
        clear_phases_for_restart,
        is_paused,
        mark_restart_started,
        open_db,
        resolve_restart_target,
    )
    from db.runs import finish_run as db_finish_run
    from db.runs import record_phase as db_record_phase
    from pipeline.merge_ffmpeg import merge_folder
    from pipeline.rename_mp4 import rename_folder

    if from_phase not in ("rename", "merge", "output"):
        raise PipelineRunError(
            f"Unsupported restart phase {from_phase!r}; "
            f"allowed: rename | merge | output"
        )

    effective_db = _resolve_db_path(config)
    if effective_db is None:
        raise PipelineRunError(
            "Cannot restart - this config has no source_path; "
            "DB persistence is required for the restart flow."
        )
    conn = open_db(effective_db)
    if is_paused(conn, config.discipline):
        raise PipelineRunError(
            f"Pipeline for {config.discipline} is paused - resume first"
        )

    target = resolve_restart_target(
        conn, run_id, work_root=str(config.paths.work), from_phase=from_phase,
    )
    if target is None:
        raise PipelineRunError(f"Run #{run_id} not found")
    if target.discipline != config.discipline:
        raise PipelineRunError(
            f"Run #{run_id} belongs to {target.discipline}, "
            f"not {config.discipline}"
        )

    # Acquire the per-discipline lock just like a normal run so we never
    # race with the watcher.
    lock = _lock_for(config)
    if not lock.acquire(blocking=False):
        raise PipelineRunError(
            f"Pipeline for {config.discipline} is already running"
        )

    writer = StatusWriter(status_path_for(config), config.discipline)
    try:
        work_folder = Path(target.work_path)
        if not work_folder.is_dir():
            raise PipelineRunError(
                f"Work folder vanished: {work_folder} - did the original "
                f"run already archive? Re-trigger a full pipeline run."
            )

        clear_phases_for_restart(conn, run_id, from_phase)
        mark_restart_started(conn, run_id)
        writer.append_log(
            f"Restart run #{run_id} ({target.folder_name}) from {from_phase}"
        )

        if from_phase == "rename":
            with _timed_phase() as rn_t:
                rename_folder(work_folder)
            db_record_phase(
                conn, run_id=run_id, phase="rename",
                started_at=rn_t["started_at"],
                duration_s=rn_t["duration_s"],
            )
            from_phase = "merge"   # fall through

        if from_phase == "merge":
            writer.update(state=State.MERGING)
            result = merge_folder(work_folder, config)
            if result.started_at is not None:
                db_record_phase(
                    conn, run_id=run_id, phase="merge",
                    started_at=result.started_at,
                    duration_s=result.duration_s,
                )
            if result.success:
                db_finish_run(
                    conn, run_id=run_id, state="done",
                    output_bytes=result.output_bytes,
                    output_filename=result.output.name,
                )
                writer.append_log(
                    f"OK restart {target.folder_name} -> "
                    f"{result.output.name} ({result.duration_s:.1f}s)"
                )
                writer.update(state=State.DONE)
            else:
                db_finish_run(
                    conn, run_id=run_id, state="error",
                    error=f"rc={result.returncode}",
                )
                writer.append_log(
                    f"FAIL restart {target.folder_name} rc={result.returncode}"
                )
                writer.update(state=State.ERROR)
            return writer

        # from_phase == "output": just verify existence
        out = config.paths.output / target.output_filename
        if out.is_file():
            db_finish_run(
                conn, run_id=run_id, state="done",
                output_bytes=out.stat().st_size,
                output_filename=target.output_filename,
            )
            writer.append_log(f"OK output verify {out.name}")
            writer.update(state=State.DONE)
        else:
            db_finish_run(
                conn, run_id=run_id, state="error",
                error=f"output missing: {out}",
            )
            writer.append_log(f"FAIL output verify - {out} missing")
            writer.update(state=State.ERROR)
        return writer
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m watcher.pipeline_runner <config.json>")
        return 2

    config = load_config(argv[1])
    print(f"Starting pipeline for {config.discipline} "
          f"(eingang={config.paths.eingang})")
    try:
        writer = run_pipeline(config)
    except PipelineRunError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    final = writer.status
    print(f"Final state: {final.state}")
    if final.error:
        print(f"Error: {final.error}", file=sys.stderr)
        return 1
    print(f"Output files: {final.output_files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
