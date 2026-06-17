"""Tests for the archive service layer (web.services).

Regression coverage for Luecke B: after the pipeline moves originals
from eingang_ to work_, the archive flow must still find them.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.config_loader import load_config
from web import services


def _make_config(tmp_path: Path):
    cfg = {
        "discipline": "Einzel",
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / "eingang_einzel"),
            "work":    str(tmp_path / "work_einzel"),
            "output":  str(tmp_path / "output_einzel"),
            "logs":    str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026", "sts_nummer": "STS2",
            "turniername": "T", "disziplin": "Einzel", "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
        "tiering": {},
    }
    p = tmp_path / "config_einzel.json"
    p.write_text(json.dumps(cfg))
    return load_config(p)


def test_archive_sources_include_work_for_originals(tmp_path):
    cfg = _make_config(tmp_path)
    sources = services._archive_sources_for(cfg)

    discipline_sources = sources[cfg.discipline]
    assert "eingang" in discipline_sources
    assert "work" in discipline_sources, (
        "work_ must be a source - originals live there after the "
        "pipeline's eingang->work move"
    )
    assert "output" in discipline_sources
    assert discipline_sources["work"] == cfg.paths.work


def test_archive_plan_walks_originals_from_work(tmp_path):
    """End-to-end: drop a renamed original into work_/ETxx and confirm
    build_archive_plan finds it via _archive_sources_for."""
    from archive import build_archive_plan

    cfg = _make_config(tmp_path)

    work_table = Path(cfg.paths.work) / "ET20"
    work_table.mkdir(parents=True)
    (work_table / "video_001.mp4").write_bytes(b"x" * 1024)
    (work_table / "video_002.mp4").write_bytes(b"y" * 2048)

    output_dir = Path(cfg.paths.output)
    output_dir.mkdir(parents=True)
    (output_dir / "2026 STS2 T20 Demo Einzel.mp4").write_bytes(b"m" * 512)

    Path(cfg.paths.eingang).mkdir(parents=True)

    archive_root = tmp_path / "hdd_archive"
    archive_root.mkdir()

    plan = build_archive_plan(
        tournament_name="Demo",
        archive_root=archive_root,
        sources=services._archive_sources_for(cfg),
    )

    sources_by_path = {entry.source for entry in plan.files}
    assert str(work_table / "video_001.mp4") in sources_by_path
    assert str(work_table / "video_002.mp4") in sources_by_path
    assert str(output_dir / "2026 STS2 T20 Demo Einzel.mp4") in sources_by_path
    assert plan.total_files == 3
    assert plan.total_bytes == 1024 + 2048 + 512
