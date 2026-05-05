"""Central config loader for the video pipeline.

Loads either ``config_doppel.json`` or ``config_einzel.json`` and exposes a
typed ``PipelineConfig`` object. All paths are resolved as ``pathlib.Path``
so callers can stay platform-agnostic (the Windows v6 used os.sep concat;
on the NAS we are strictly POSIX).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

REQUIRED_TOP_LEVEL = ("discipline", "paths", "filename_constants")
REQUIRED_PATHS = ("eingang", "work", "output", "logs")
REQUIRED_CONSTANTS = ("k1", "k2", "k4", "k5", "k6")
ALLOWED_DISCIPLINES = ("Doppel", "Einzel")


class ConfigError(ValueError):
    """Raised when a config file is missing keys or has invalid values."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FilenameConstants:
    """Static parts of the output filename (per discipline)."""
    k1: str
    k2: str
    k4: str
    k5: str
    k6: str

    def as_list(self) -> List[str]:
        return [self.k1, self.k2, self.k4, self.k5, self.k6]


@dataclass(frozen=True)
class PipelinePaths:
    """All filesystem locations used by one discipline pipeline."""
    eingang: Path
    work: Path
    output: Path
    logs: Path

    def all(self) -> List[Path]:
        return [self.eingang, self.work, self.output, self.logs]


@dataclass
class PipelineConfig:
    """In-memory representation of one discipline's config file."""
    discipline: str
    enabled: bool
    paths: PipelinePaths
    filename_constants: FilenameConstants
    ffmpeg: Dict[str, Any] = field(default_factory=dict)
    youtube: Dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None

    @property
    def max_workers(self) -> int:
        return int(self.ffmpeg.get("max_workers", 4))

    @property
    def max_files_per_folder(self) -> int:
        return int(self.ffmpeg.get("max_files_per_folder", 24))


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _validate(data: Dict[str, Any], source: Path) -> None:
    missing = [key for key in REQUIRED_TOP_LEVEL if key not in data]
    if missing:
        raise ConfigError(f"{source}: missing top-level keys {missing}")

    discipline = data["discipline"]
    if discipline not in ALLOWED_DISCIPLINES:
        raise ConfigError(
            f"{source}: discipline must be one of {ALLOWED_DISCIPLINES}, "
            f"got {discipline!r}"
        )

    missing_paths = [key for key in REQUIRED_PATHS if key not in data["paths"]]
    if missing_paths:
        raise ConfigError(f"{source}: missing path keys {missing_paths}")

    missing_consts = [
        key for key in REQUIRED_CONSTANTS if key not in data["filename_constants"]
    ]
    if missing_consts:
        raise ConfigError(
            f"{source}: missing filename_constants {missing_consts}"
        )


def load_config(path: str | os.PathLike) -> PipelineConfig:
    """Read a config file from disk and return a validated PipelineConfig."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Config file not found: {source}")

    with source.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    _validate(data, source)

    paths = PipelinePaths(
        eingang=Path(data["paths"]["eingang"]),
        work=Path(data["paths"]["work"]),
        output=Path(data["paths"]["output"]),
        logs=Path(data["paths"]["logs"]),
    )
    constants = FilenameConstants(
        k1=str(data["filename_constants"]["k1"]),
        k2=str(data["filename_constants"]["k2"]),
        k4=str(data["filename_constants"]["k4"]),
        k5=str(data["filename_constants"]["k5"]),
        k6=str(data["filename_constants"]["k6"]),
    )

    return PipelineConfig(
        discipline=data["discipline"],
        enabled=bool(data.get("enabled", True)),
        paths=paths,
        filename_constants=constants,
        ffmpeg=data.get("ffmpeg", {}),
        youtube=data.get("youtube", {}),
        source_path=source,
    )


def ensure_pipeline_dirs(config: PipelineConfig) -> None:
    """Create eingang/work/output/logs directories if they do not exist."""
    for directory in config.paths.all():
        directory.mkdir(parents=True, exist_ok=True)


def save_config(config: PipelineConfig) -> None:
    """Atomically write *config* back to its source file.

    Used by the Web-Interface to persist YouTube metadata edits without
    losing the rest of the file (paths, constants).
    """
    if config.source_path is None:
        raise ConfigError("config.source_path is None - cannot save")

    data = {
        "discipline": config.discipline,
        "enabled": config.enabled,
        "paths": {
            "eingang": str(config.paths.eingang),
            "work": str(config.paths.work),
            "output": str(config.paths.output),
            "logs": str(config.paths.logs),
        },
        "filename_constants": {
            "k1": config.filename_constants.k1,
            "k2": config.filename_constants.k2,
            "k4": config.filename_constants.k4,
            "k5": config.filename_constants.k5,
            "k6": config.filename_constants.k6,
        },
        "ffmpeg": dict(config.ffmpeg),
        "youtube": dict(config.youtube),
    }

    path = config.source_path
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Filename builder
# ---------------------------------------------------------------------------

def parse_folder_name(folder_name: str) -> tuple[str, str | None]:
    """Extract (variableA, variableB) from an ET-folder name.

    Convention from PROJEKT_BRIEFING.md section 1:
      * 4-character folders (e.g. ``ET03``)         -> variableA only
      * 6-character folders (e.g. ``ET03_1``)       -> variableA + variableB

    The leading ``E`` is stripped so ``ET03`` becomes ``T03`` (matches the
    example "2026 STS02 T03 Doppel Part 1.mp4").
    """
    name = folder_name.strip()
    if len(name) == 4 and name.startswith("ET"):
        return name[1:], None
    if len(name) == 6 and name.startswith("ET") and name[4] == "_":
        return name[1:4], name[5]
    raise ValueError(
        f"Folder name {folder_name!r} does not match ET-pattern "
        f"(expected ETxx or ETxx_y)"
    )


def build_output_filename(
    constants: FilenameConstants,
    folder_name: str,
) -> str:
    """Build the FFmpeg output filename per the briefing rules.

    Empty constants are filtered out so we never emit double spaces.
    """
    variable_a, variable_b = parse_folder_name(folder_name)
    constants_list = constants.as_list()
    # Schema order: k1, k2, variableA, k4, k5, k6, variableB
    parts: List[str] = [
        constants_list[0],   # k1
        constants_list[1],   # k2
        variable_a,
        constants_list[2],   # k4
        constants_list[3],   # k5
        constants_list[4],   # k6
    ]
    if variable_b is not None:
        parts.append(variable_b)

    filtered = [str(p) for p in parts if str(p).strip()]
    return " ".join(filtered) + ".mp4"


# ---------------------------------------------------------------------------
# CLI entrypoint (for quick smoke testing)
# ---------------------------------------------------------------------------

def _main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m pipeline.config_loader <path-to-config.json>")
        return 2
    config = load_config(argv[1])
    print(f"Loaded config: {config.discipline} (enabled={config.enabled})")
    print(f"  source : {config.source_path}")
    print(f"  paths  : {config.paths}")
    print(f"  consts : {config.filename_constants}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
