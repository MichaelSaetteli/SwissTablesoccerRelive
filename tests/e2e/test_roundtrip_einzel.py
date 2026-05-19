"""End-to-end roundtrip for the Einzel pipeline.

Same shape as the Doppel test - the point is to verify that the two
disciplines remain strictly independent: configs do not leak, lock
files are per-discipline, output filenames carry the right "Einzel"
constant.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.config_loader import load_config
from tests.e2e.conftest import ffprobe_duration, seed_camera_folder
from watcher.pipeline_runner import run_pipeline
from watcher.status import State


def test_roundtrip_einzel(tmp_path: Path, einzel_config_path: Path) -> None:
    cfg = load_config(einzel_config_path)
    assert cfg.discipline == "Einzel"
    assert cfg.filename_constants.disziplin == "Einzel"

    seed_camera_folder(cfg.paths.eingang, "ET07", n_files=2)

    writer = run_pipeline(cfg)
    status = writer.status

    assert status.state == State.DONE, status.error
    expected = cfg.paths.output / "2026 STS2 T07 E2eTest Einzel.mp4"
    assert expected.is_file(), \
        f"expected output {expected} not found. dir contents: " \
        f"{[p.name for p in cfg.paths.output.iterdir()]}"

    duration = ffprobe_duration(expected)
    assert 5.0 <= duration <= 7.5, f"expected ~6s, got {duration:.2f}s"


def test_einzel_doppel_dont_share_output(
    doppel_config_path: Path, einzel_config_path: Path,
) -> None:
    """Two independent runs must each write only to their own output dir."""
    doppel = load_config(doppel_config_path)
    einzel = load_config(einzel_config_path)

    seed_camera_folder(doppel.paths.eingang, "ET01", n_files=1)
    seed_camera_folder(einzel.paths.eingang, "ET02", n_files=1)

    run_pipeline(doppel)
    run_pipeline(einzel)

    doppel_files = sorted(p.name for p in doppel.paths.output.glob("*.mp4"))
    einzel_files = sorted(p.name for p in einzel.paths.output.glob("*.mp4"))

    assert all("Doppel" in n for n in doppel_files), doppel_files
    assert all("Einzel" in n for n in einzel_files), einzel_files
    assert set(doppel_files).isdisjoint(set(einzel_files))
