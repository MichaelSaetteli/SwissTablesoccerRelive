"""Pure metadata generation - no Google deps, fully unit-testable.

Briefing s.6 placeholders for title and description templates::

    {turniername}    "STS Bern 2026"
    {disziplin}      "Doppel" or "Einzel"
    {datum}          "17./18. Mai 2026"
    {ort}            "Bern, Schweiz"
    {kamera}         "T03" (extracted from the output filename)
    {nummer}         "1", "2", "3" ... (1-based index in the upload batch)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from pipeline.config_loader import PipelineConfig

sys.stdout.reconfigure(encoding="utf-8")


# YouTube API limits (briefing s.6).
TITLE_MAX_LEN = 100        # YouTube hard limit
DESCRIPTION_MAX_LEN = 5000

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class VideoMetadata:
    """One video's resolved metadata, ready to send to YouTube."""
    file: str
    title: str
    description: str
    context: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "file": self.file,
            "title": self.title,
            "description": self.description,
            "context": dict(self.context),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_format(template: str, context: Dict[str, str]) -> str:
    """Replace ``{key}`` placeholders without raising on unknown keys.

    Unknown keys collapse to ``""``; unmatched braces stay intact (this
    avoids surprises if the operator types ``{`` or ``}`` literally).
    """
    return PLACEHOLDER_RE.sub(
        lambda match: str(context.get(match.group(1), "")),
        template,
    )


def extract_kamera(filename: str) -> str:
    """Return the *Tischnummer* word ('T01', 'T03', ...) from an output file.

    Output files follow the schema
        ``{jahr} {sts_nummer} {tischnummer} {turniername} {disziplin} [{part}].mp4``
    so the third whitespace-separated token is the camera.
    Returns ``""`` if the filename is too short or oddly shaped (the caller
    can decide whether that is an error).
    """
    stem = Path(filename).stem
    parts = stem.split(" ")
    if len(parts) < 3:
        return ""
    return parts[2]


def build_context(
    config: PipelineConfig,
    filename: str,
    nummer: int,
) -> Dict[str, str]:
    """Build the placeholder context for one file."""
    yt = config.youtube
    consts = config.filename_constants
    return {
        # Prefer the YouTube-specific tournament name; fall back to the
        # filename constant so the operator does not have to fill the
        # same field twice for the common case.
        "turniername": str(yt.get("tournament_name", "") or consts.turniername),
        "disziplin": consts.disziplin,
        "datum": str(yt.get("date", "") or ""),
        "ort": str(yt.get("location", "") or ""),
        "kamera": extract_kamera(filename),
        "nummer": str(nummer),
    }


def build_video_metadata(
    config: PipelineConfig,
    filename: str,
    nummer: int,
) -> VideoMetadata:
    yt = config.youtube
    context = build_context(config, filename, nummer)
    title = safe_format(str(yt.get("title_template", "")), context).strip()
    description = safe_format(
        str(yt.get("description_template", "")), context,
    ).strip()
    if len(title) > TITLE_MAX_LEN:
        title = title[:TITLE_MAX_LEN]
    if len(description) > DESCRIPTION_MAX_LEN:
        description = description[:DESCRIPTION_MAX_LEN]
    return VideoMetadata(
        file=filename,
        title=title,
        description=description,
        context=context,
    )


def build_upload_batch(
    config: PipelineConfig,
    files: Sequence[str],
) -> List[VideoMetadata]:
    """Generate the per-file metadata for an upload preview."""
    return [
        build_video_metadata(config, fname, idx)
        for idx, fname in enumerate(files, start=1)
    ]


# ---------------------------------------------------------------------------
# Quota hint (briefing s.6: 10'000 units/day standard, 1'600 per upload)
# ---------------------------------------------------------------------------

UNITS_PER_UPLOAD = 1600
DAILY_QUOTA = 10000


def quota_hint(num_files: int) -> str:
    """Return a German-language hint string about the daily quota."""
    cost = num_files * UNITS_PER_UPLOAD
    cap = DAILY_QUOTA // UNITS_PER_UPLOAD
    if num_files == 0:
        return ""
    if cost <= DAILY_QUOTA:
        return (
            f"YouTube-Tageskontingent: {cost} von {DAILY_QUOTA} Units "
            f"({num_files} Uploads). Standard erlaubt ca. {cap} Videos/Tag."
        )
    return (
        f"Achtung: {num_files} Uploads benoetigen {cost} Units "
        f"(Tageslimit {DAILY_QUOTA}). Maximal {cap} Videos pro Tag - "
        f"Quota-Erhoehung bei Google beantragen."
    )
