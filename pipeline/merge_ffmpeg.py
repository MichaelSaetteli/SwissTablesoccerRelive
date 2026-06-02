"""FFmpeg stream-copy merge step (Linux port of ``5_MergeFFmpeg.py``).

For each prepared folder the script:

1. Builds an FFmpeg ``concat`` list from all ``video_*.mp4`` entries.
2. Runs ``ffmpeg -f concat -safe 0 -i list.txt -c copy <output>`` so no
   re-encoding happens (CPU-light, important on the DS1522+).
3. Writes the output into ``config.paths.output`` using the filename schema
   defined in ``config_loader.build_output_filename``.

Folders are processed in parallel via ``ThreadPoolExecutor`` (default 4
workers, configurable in ``config.ffmpeg.max_workers``).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

from pipeline.status_file import now_iso

from .config_loader import PipelineConfig, build_output_filename

sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class MergeResult:
    folder: Path
    output: Path
    success: bool
    returncode: int
    stderr: str = ""
    log_path: Optional[Path] = None
    started_at: Optional[str] = None     # ISO-8601 with TZ, set by merge_folder
    duration_s: float = 0.0
    input_bytes: int = 0                 # Sum of all video_*.mp4 in the folder
    output_bytes: int = 0                # Size of the merged output (0 on fail)


class MergeError(RuntimeError):
    """Raised when the FFmpeg invocation cannot even be assembled."""


# ---------------------------------------------------------------------------
# Concat-list helpers
# ---------------------------------------------------------------------------

def list_video_files(folder: Path) -> List[Path]:
    """Return ``video_*.mp4`` files inside *folder*, sorted by filename."""
    return sorted(
        entry for entry in folder.iterdir()
        if entry.is_file()
        and entry.suffix.lower() == ".mp4"
        and entry.stem.startswith("video_")
    )


def write_concat_list(folder: Path, files: Sequence[Path]) -> Path:
    """Write the FFmpeg concat list and return its path.

    The concat demuxer expects lines like ``file '/abs/path/video_001.mp4'``.
    Single-quotes inside the path are escaped per FFmpeg's rule
    (``'`` -> ``'\\''``).
    """
    list_path = folder / "concat_list.txt"
    with list_path.open("w", encoding="utf-8") as fh:
        for entry in files:
            absolute = str(entry.resolve())
            escaped = absolute.replace("'", "'\\''")
            fh.write(f"file '{escaped}'\n")
    return list_path


def build_ffmpeg_command(concat_list: Path, output: Path) -> List[str]:
    """Build the ffmpeg argv for stream-copy concat."""
    return [
        "ffmpeg",
        "-y",                # overwrite output if it exists
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(output),
    ]


# ---------------------------------------------------------------------------
# Single-folder merge
# ---------------------------------------------------------------------------

Runner = Callable[[List[str]], "subprocess.CompletedProcess[str]"]
# Probes a media file's duration in seconds, or None if undeterminable.
Prober = Callable[[Path], Optional[float]]


def _default_runner(cmd: List[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _default_prober(path: Path) -> Optional[float]:
    """Return the container duration in seconds via ffprobe, else None.

    Best-effort: any failure (ffprobe missing, non-zero exit, unparseable
    output) returns None so the caller treats the merge as *unverified*
    rather than failed - we never block a good merge just because probing
    was unavailable.
    """
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except (TypeError, ValueError):
        return None


def verify_output_duration(
    inputs: Sequence[Path],
    output: Path,
    prober: Prober,
    *,
    min_tolerance_s: float = 1.0,
    tolerance_frac: float = 0.01,
) -> Optional[str]:
    """Catch silent stream-copy failures (ffmpeg rc=0 but truncated output).

    A stream-copy concat aborts on a corrupt input but can still exit 0,
    leaving a short/broken file that would otherwise be "successfully"
    uploaded. We compare the merged duration against the sum of the input
    durations.

    Returns ``None`` when the check passes OR cannot be performed (any
    duration undeterminable - we never block on missing ffprobe); returns a
    human-readable reason string when the durations disagree beyond
    ``max(min_tolerance_s, tolerance_frac * expected)``.
    """
    out_duration = prober(output)
    if out_duration is None:
        return None  # cannot verify -> do not block
    input_durations = [prober(p) for p in inputs]
    if any(d is None for d in input_durations):
        return None  # cannot compute the expectation -> do not block
    expected = sum(d for d in input_durations if d is not None)
    tolerance = max(min_tolerance_s, tolerance_frac * expected)
    if abs(out_duration - expected) > tolerance:
        return (
            f"output duration {out_duration:.1f}s deviates from expected "
            f"{expected:.1f}s by more than {tolerance:.1f}s - the merge likely "
            f"produced a truncated/corrupt file despite ffmpeg returncode 0"
        )
    return None


def _write_ffmpeg_log(
    config: PipelineConfig,
    folder: Path,
    cmd: List[str],
    completed: "subprocess.CompletedProcess[str]",
) -> Optional[Path]:
    """Persist the full FFmpeg invocation log for one folder.

    Briefing s.7: "Logging - Datei offen halten". We write one log per
    merge invocation under ``<config.paths.logs>/`` so a stderr blob
    from a failed merge is preserved for post-mortem debugging instead
    of being truncated to 200 chars in status.log_tail.
    """
    logs_dir = config.paths.logs
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    # Microsecond suffix so two merges in the same second do not collide.
    timestamp = (
        datetime.now(timezone.utc)
        .astimezone()
        .strftime("%Y%m%d-%H%M%S-%f")
    )
    name = f"ffmpeg_{config.discipline.lower()}_{folder.name}_{timestamp}.log"
    path = logs_dir / name
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write(f"# {' '.join(cmd)}\n")
            fh.write(f"# returncode={completed.returncode}\n")
            fh.write("# --- stdout ---\n")
            fh.write(completed.stdout or "")
            fh.write("\n# --- stderr ---\n")
            fh.write(completed.stderr or "")
    except OSError:
        return None
    return path


def merge_folder(
    folder: Path,
    config: PipelineConfig,
    runner: Optional[Runner] = None,
    prober: Optional[Prober] = None,
) -> MergeResult:
    """Merge all ``video_*.mp4`` files in *folder* into a single output mp4.

    *runner* defaults to ``subprocess.run``; tests inject a fake to avoid
    actually invoking FFmpeg.

    Atomicity: FFmpeg writes to a hidden ``.<name>.partial`` file. Only
    after a clean exit is the file renamed to its final name, so the
    Web-Interface never serves a half-written output. On failure the
    partial is removed so the work folder stays clean.
    """
    if not folder.is_dir():
        raise MergeError(f"Folder is not a directory: {folder}")

    files = list_video_files(folder)
    if not files:
        raise MergeError(f"Folder has no video_*.mp4 files: {folder}")

    output_dir = config.paths.output
    output_dir.mkdir(parents=True, exist_ok=True)

    output_name = build_output_filename(config.filename_constants, folder.name)
    final_path = output_dir / output_name
    # Partial keeps the .mp4 extension so ffmpeg can auto-detect the
    # muxer; leading dot hides it from File Station / glob("*.mp4").
    partial_path = output_dir / f".{final_path.stem}.partial{final_path.suffix}"

    concat_list = write_concat_list(folder, files)
    cmd = build_ffmpeg_command(concat_list, partial_path)

    input_bytes = sum((f.stat().st_size for f in files), 0)
    started_at = now_iso()
    t0 = time.monotonic()

    run = runner or _default_runner
    completed = run(cmd)
    duration_s = time.monotonic() - t0

    success = completed.returncode == 0
    stderr = completed.stderr or ""

    # Guard against silent stream-copy failures: ffmpeg can exit 0 while
    # producing a truncated file when an input aborts mid-stream. Verify the
    # merged duration against the inputs before promoting the partial.
    if success and partial_path.is_file():
        probe = prober or _default_prober
        duration_problem = verify_output_duration(files, partial_path, probe)
        if duration_problem is not None:
            success = False
            stderr = (f"{stderr}\n" if stderr else "") + \
                f"[duration-check] {duration_problem}"

    log_path = _write_ffmpeg_log(config, folder, cmd, completed)

    output_bytes = 0
    if success and partial_path.is_file():
        os.replace(partial_path, final_path)
        try:
            output_bytes = final_path.stat().st_size
        except OSError:
            output_bytes = 0
    else:
        # Clean up the partial so a re-run starts fresh.
        try:
            partial_path.unlink()
        except OSError:
            pass

    # Drop the concat list - it is regenerated on every run anyway and
    # accumulating these in work_*/ETxx/ adds clutter.
    try:
        concat_list.unlink()
    except OSError:
        pass

    return MergeResult(
        folder=folder,
        output=final_path,
        success=success,
        returncode=completed.returncode,
        stderr=stderr,
        log_path=log_path,
        started_at=started_at,
        duration_s=duration_s,
        input_bytes=input_bytes,
        output_bytes=output_bytes,
    )


# ---------------------------------------------------------------------------
# Parallel batch
# ---------------------------------------------------------------------------

def merge_all(
    folders: Iterable[Path],
    config: PipelineConfig,
    runner: Optional[Runner] = None,
    prober: Optional[Prober] = None,
) -> List[MergeResult]:
    """Run ``merge_folder`` over *folders* in parallel using ThreadPoolExecutor."""
    folders = list(folders)
    if not folders:
        return []

    results: List[MergeResult] = []
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_folder = {
            executor.submit(merge_folder, folder, config, runner, prober): folder
            for folder in folders
        }
        for future in as_completed(future_to_folder):
            folder = future_to_folder[future]
            try:
                results.append(future.result())
            except Exception as exc:  # pragma: no cover - defensive
                results.append(MergeResult(
                    folder=folder,
                    output=Path(),
                    success=False,
                    returncode=-1,
                    stderr=f"{type(exc).__name__}: {exc}",
                ))
    # Sort for stable test/log output
    results.sort(key=lambda r: r.folder.name)
    return results


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _main(argv: Sequence[str]) -> int:
    if len(argv) != 3:
        print("Usage: python merge_ffmpeg.py <config.json> <work-dir>")
        return 2

    from .config_loader import load_config

    config = load_config(argv[1])
    work_dir = Path(argv[2])
    if not work_dir.is_dir():
        print(f"ERROR: work dir not found: {work_dir}", file=sys.stderr)
        return 1

    folders = sorted(p for p in work_dir.iterdir() if p.is_dir())
    print(f"Merging {len(folders)} folder(s) with {config.max_workers} workers")
    for folder in folders:
        cmd = build_ffmpeg_command(folder / "concat_list.txt",
                                   config.paths.output / "<computed>.mp4")
        print(f"  {folder.name} -> {shlex.join(cmd)}")

    results = merge_all(folders, config)
    failed = [r for r in results if not r.success]
    for r in results:
        status = "OK" if r.success else f"FAIL rc={r.returncode}"
        print(f"  [{status}] {r.folder.name} -> {r.output.name}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
