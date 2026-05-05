"""Tests for youtube.metadata_builder."""

from __future__ import annotations

from pathlib import Path

from pipeline.config_loader import load_config
from youtube.metadata_builder import (
    DAILY_QUOTA,
    UNITS_PER_UPLOAD,
    build_context,
    build_upload_batch,
    build_video_metadata,
    extract_kamera,
    quota_hint,
    safe_format,
)


# ---- safe_format ----------------------------------------------------------

def test_safe_format_substitutes_known_keys() -> None:
    out = safe_format(
        "{turniername} {disziplin} {kamera} Part {nummer}",
        {"turniername": "Seetal", "disziplin": "Doppel",
         "kamera": "T01", "nummer": "1"},
    )
    assert out == "Seetal Doppel T01 Part 1"


def test_safe_format_unknown_keys_become_empty() -> None:
    out = safe_format("a={a} b={b}", {"a": "X"})
    assert out == "a=X b="


def test_safe_format_handles_no_placeholders() -> None:
    assert safe_format("plain text", {}) == "plain text"


# ---- extract_kamera -------------------------------------------------------

def test_extract_kamera_from_briefing_filename() -> None:
    assert extract_kamera("2026 STS2 T01 Seetal Doppel.mp4") == "T01"


def test_extract_kamera_with_part() -> None:
    assert extract_kamera("2026 STS2 T03 Seetal Doppel Part 2.mp4") == "T03"


def test_extract_kamera_short_filename() -> None:
    assert extract_kamera("toofew.mp4") == ""


# ---- build_context --------------------------------------------------------

def test_build_context_uses_yt_tournament_when_set(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["tournament_name"] = "STS Bern 2026"
    cfg.youtube["date"] = "17. Mai 2026"
    cfg.youtube["location"] = "Bern, Schweiz"
    ctx = build_context(cfg, "2026 STS2 T01 Seetal Doppel.mp4", nummer=3)

    assert ctx == {
        "turniername": "STS Bern 2026",
        "disziplin": "Doppel",
        "datum": "17. Mai 2026",
        "ort": "Bern, Schweiz",
        "kamera": "T01",
        "nummer": "3",
    }


def test_build_context_falls_back_to_filename_constant_turniername(
    doppel_config_path: Path,
) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["tournament_name"] = ""  # not configured for YouTube
    ctx = build_context(cfg, "2026 STS2 T07 Seetal Doppel.mp4", nummer=1)
    # Falls back to the filename constant "Seetal".
    assert ctx["turniername"] == "Seetal"


# ---- build_video_metadata --------------------------------------------------

def test_build_video_metadata_renders_full_template(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["tournament_name"] = "STS Bern 2026"
    cfg.youtube["date"] = "17. Mai 2026"
    cfg.youtube["location"] = "Bern"
    cfg.youtube["title_template"] = "{turniername} {disziplin} {kamera} Part {nummer}"
    cfg.youtube["description_template"] = (
        "Aufnahme vom {datum} in {ort}. Kamera: {kamera}."
    )
    meta = build_video_metadata(cfg, "2026 STS2 T01 Seetal Doppel.mp4", nummer=2)

    assert meta.title == "STS Bern 2026 Doppel T01 Part 2"
    assert meta.description == "Aufnahme vom 17. Mai 2026 in Bern. Kamera: T01."
    assert meta.context["nummer"] == "2"


def test_build_video_metadata_truncates_title(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["title_template"] = "X" * 200
    meta = build_video_metadata(cfg, "2026 STS2 T01 a b.mp4", nummer=1)
    assert len(meta.title) == 100  # YouTube hard limit


# ---- build_upload_batch ----------------------------------------------------

def test_build_upload_batch_assigns_sequential_nummer(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    cfg.youtube["title_template"] = "{kamera}#{nummer}"
    files = [
        "2026 STS2 T01 Seetal Doppel.mp4",
        "2026 STS2 T02 Seetal Doppel.mp4",
        "2026 STS2 T03 Seetal Doppel.mp4",
    ]
    batch = build_upload_batch(cfg, files)
    titles = [m.title for m in batch]
    assert titles == ["T01#1", "T02#2", "T03#3"]


def test_build_upload_batch_empty_input(doppel_config_path: Path) -> None:
    cfg = load_config(doppel_config_path)
    assert build_upload_batch(cfg, []) == []


# ---- quota_hint -----------------------------------------------------------

def test_quota_hint_zero() -> None:
    assert quota_hint(0) == ""


def test_quota_hint_within_limit() -> None:
    msg = quota_hint(3)
    assert "3 Uploads" in msg or "3 von" in msg or "(3 " in msg
    assert str(3 * UNITS_PER_UPLOAD) in msg


def test_quota_hint_above_limit_is_warning() -> None:
    n = (DAILY_QUOTA // UNITS_PER_UPLOAD) + 1
    msg = quota_hint(n)
    assert "Achtung" in msg or "Quota" in msg
