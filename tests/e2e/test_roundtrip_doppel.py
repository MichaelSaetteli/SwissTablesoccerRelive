"""End-to-end roundtrip for the Doppel pipeline.

Drops three copies of the fixture into eingang_doppel/ET01/, runs the
pipeline runner *with real ffmpeg*, and verifies that a correctly named
output file with the expected duration appears in output_doppel/.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pipeline.config_loader import load_config
from tests.e2e.conftest import ffprobe_duration, seed_camera_folder
from watcher.pipeline_runner import run_pipeline
from watcher.status import State


def test_roundtrip_doppel(tmp_path: Path, doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)

    # Three 3-second clips -> expect ~9-second concatenated output.
    seed_camera_folder(cfg.paths.eingang, "ET01", n_files=3)

    writer = run_pipeline(cfg)
    status = writer.status

    assert status.state == State.DONE, (
        f"pipeline did not reach DONE: state={status.state}, "
        f"error={status.error!r}, log_tail={status.log_tail[-5:]}"
    )
    assert status.error is None
    assert status.folders_processed == ["ET01"]

    # Output file with the configured filename schema must exist.
    expected = cfg.paths.output / "2026 STS2 T01 E2eTest Doppel.mp4"
    assert expected.is_file(), \
        f"expected output {expected} not found. dir contents: " \
        f"{[p.name for p in cfg.paths.output.iterdir()]}"

    # ffprobe says ~9 seconds (3 clips x 3 s each, allow some demuxer slop).
    duration = ffprobe_duration(expected)
    assert 7.5 <= duration <= 11.0, f"expected ~9s, got {duration:.2f}s"


def test_roundtrip_doppel_split_folder(
    tmp_path: Path, doppel_config_path: Path,
) -> None:
    """A folder with >24 mp4s gets split and produces multiple Part-N outputs."""
    cfg = load_config(doppel_config_path)

    # 26 clips -> organize_folders splits into ET01_1 (24 files) + ET01_2 (2 files)
    seed_camera_folder(cfg.paths.eingang, "ET01", n_files=26)

    writer = run_pipeline(cfg)
    status = writer.status
    assert status.state == State.DONE, status.error

    output_dir = cfg.paths.output
    files = sorted(p.name for p in output_dir.iterdir() if p.suffix == ".mp4")
    assert files == [
        "2026 STS2 T01 E2eTest Doppel Part 1.mp4",
        "2026 STS2 T01 E2eTest Doppel Part 2.mp4",
    ], files
