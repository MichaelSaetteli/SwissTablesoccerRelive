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

import sys
import threading
import traceback
from pathlib import Path
from typing import Dict, List, Sequence

from pipeline.MoveFiles import move_path
from pipeline.config_loader import (
    PipelineConfig,
    ensure_pipeline_dirs,
    load_config,
)
from pipeline.merge_ffmpeg import merge_all
from pipeline.organize_folders import organize_root
from pipeline.rename_mp4 import rename_root

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


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

class PipelineRunError(RuntimeError):
    """Raised when the pipeline cannot complete a run."""


def run_pipeline(config: PipelineConfig) -> StatusWriter:
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

    lock = _lock_for(config)
    if not lock.acquire(blocking=False):
        raise PipelineRunError(
            f"Pipeline for {config.discipline} is already running"
        )

    writer = StatusWriter(status_path_for(config), config.discipline)
    try:
        ensure_pipeline_dirs(config)

        folders = detect_folders(config.paths.eingang)
        if not folders:
            writer.update(state=State.IDLE)
            writer.append_log("No folders detected in eingang - run skipped")
            return writer

        writer.begin_run([f.name for f in folders])
        writer.append_log(f"Detected {len(folders)} folder(s): "
                          f"{[f.name for f in folders]}")

        # --- Step 1: move eingang -> work
        writer.update(state=State.MOVING)
        moved_folders = _step_move(folders, config.paths.work)
        writer.append_log(f"Moved {len(moved_folders)} folder(s) to work")

        # --- Step 2: organize (split >24)
        writer.update(state=State.ORGANIZING)
        prepared = organize_root(
            config.paths.work,
            max_files=config.max_files_per_folder,
        )
        writer.append_log(
            f"Organized into {len(prepared)} folder(s): "
            f"{[p.name for p in prepared]}"
        )

        # --- Step 3: rename to video_NNN.mp4
        writer.update(state=State.RENAMING)
        renamed = rename_root(config.paths.work)
        writer.append_log(f"Renamed {len(renamed)} mp4 file(s)")

        # --- Step 4: ffmpeg merge (parallel)
        writer.update(state=State.MERGING)
        results = merge_all(prepared, config)

        failed = [r for r in results if not r.success]
        succeeded = [r for r in results if r.success]
        for r in succeeded:
            writer.append_log(f"OK     {r.folder.name} -> {r.output.name}")
        for r in failed:
            writer.append_log(
                f"FAIL   {r.folder.name} rc={r.returncode} "
                f"stderr={r.stderr.strip()[:200]}"
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
