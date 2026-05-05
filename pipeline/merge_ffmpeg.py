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

import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

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


def _default_runner(cmd: List[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def merge_folder(
    folder: Path,
    config: PipelineConfig,
    runner: Optional[Runner] = None,
) -> MergeResult:
    """Merge all ``video_*.mp4`` files in *folder* into a single output mp4.

    *runner* defaults to ``subprocess.run``; tests inject a fake to avoid
    actually invoking FFmpeg.
    """
    if not folder.is_dir():
        raise MergeError(f"Folder is not a directory: {folder}")

    files = list_video_files(folder)
    if not files:
        raise MergeError(f"Folder has no video_*.mp4 files: {folder}")

    output_dir = config.paths.output
    output_dir.mkdir(parents=True, exist_ok=True)

    output_name = build_output_filename(config.filename_constants, folder.name)
    output_path = output_dir / output_name

    concat_list = write_concat_list(folder, files)
    cmd = build_ffmpeg_command(concat_list, output_path)

    run = runner or _default_runner
    completed = run(cmd)
    success = completed.returncode == 0
    stderr = completed.stderr or ""

    return MergeResult(
        folder=folder,
        output=output_path,
        success=success,
        returncode=completed.returncode,
        stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Parallel batch
# ---------------------------------------------------------------------------

def merge_all(
    folders: Iterable[Path],
    config: PipelineConfig,
    runner: Optional[Runner] = None,
) -> List[MergeResult]:
    """Run ``merge_folder`` over *folders* in parallel using ThreadPoolExecutor."""
    folders = list(folders)
    if not folders:
        return []

    results: List[MergeResult] = []
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_folder = {
            executor.submit(merge_folder, folder, config, runner): folder
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
