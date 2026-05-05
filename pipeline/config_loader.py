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
REQUIRED_CONSTANTS = ("jahr", "sts_nummer", "turniername", "disziplin", "part")
ALLOWED_DISCIPLINES = ("Doppel", "Einzel")


class ConfigError(ValueError):
    """Raised when a config file is missing keys or has invalid values."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FilenameConstants:
    """User-defined constants that build the output filename.

    Schema agreed with the operator:

        {jahr} {sts_nummer} {tischnummer} {turniername} {disziplin} [{part}]
        2026   STS2          T01           Seetal        Doppel       Part 1

    ``tischnummer`` is *not* in this dataclass - it is the variable part
    extracted from the folder name (see ``parse_folder_name``).
    ``part`` is optional; when empty it is filtered out.
    """
    jahr: str
    sts_nummer: str
    turniername: str
    disziplin: str
    part: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "jahr": self.jahr,
            "sts_nummer": self.sts_nummer,
            "turniername": self.turniername,
            "disziplin": self.disziplin,
            "part": self.part,
        }


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
        jahr=str(data["filename_constants"]["jahr"]),
        sts_nummer=str(data["filename_constants"]["sts_nummer"]),
        turniername=str(data["filename_constants"]["turniername"]),
        disziplin=str(data["filename_constants"]["disziplin"]),
        part=str(data["filename_constants"]["part"]),
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

    Used by the Web-Interface to persist filename and YouTube edits
    without losing the rest of the file (paths, ffmpeg settings).
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
        "filename_constants": config.filename_constants.as_dict(),
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
    """Return ``(tischnummer, split_index)`` for an ET-folder name.

    Examples:
        ``ET03``   -> ``("T03", None)``
        ``ET01``   -> ``("T01", None)``
        ``ET03_1`` -> ``("T03", "1")``   (split by organize_folders.py)
        ``ET03_2`` -> ``("T03", "2")``

    The leading ``E`` is stripped so ``ETxx`` becomes ``Txx``. Any
    ``_<n>`` suffix (the split index) is returned separately so the
    filename builder can apply Option B: auto-emit ``Part 1``,
    ``Part 2`` for split folders, overriding the user's ``part`` field.
    """
    name = folder_name.strip()
    if not name.startswith("ET"):
        raise ValueError(
            f"Folder name {folder_name!r} does not match ET-pattern "
            f"(must start with 'ET')"
        )
    rest = name[1:]
    if len(rest) < 3 or rest[0] != "T" or not rest[1:3].isdigit():
        raise ValueError(
            f"Folder name {folder_name!r} does not match ET-pattern "
            f"(expected ETxx or ETxx_<suffix>)"
        )
    if len(rest) == 3:
        return rest, None  # ET03 -> T03
    if rest[3] == "_" and len(rest) > 4:
        return rest[:3], rest[4:]  # ET03_1 -> T03, 1
    raise ValueError(
        f"Folder name {folder_name!r} has unexpected suffix; "
        f"expected ETxx or ETxx_<suffix>"
    )


def build_output_filename(
    constants: FilenameConstants,
    folder_name: str,
) -> str:
    """Build the merged-output filename.

    Schema:
        {jahr} {sts_nummer} {tischnummer} {turniername} {disziplin} [{part}].mp4

    Split folders (Option B): when the folder name carries a ``_<n>``
    suffix from organize_folders.py, the ``part`` field is auto-set to
    ``"Part <n>"`` and the user's configured ``part`` is overridden.
    Otherwise the configured ``part`` (possibly empty) is used as-is.
    Empty fields are filtered out so we never emit double spaces.
    """
    tischnummer, split_index = parse_folder_name(folder_name)
    if split_index is not None:
        effective_part = f"Part {split_index}"
    else:
        effective_part = constants.part

    parts: List[str] = [
        constants.jahr,
        constants.sts_nummer,
        tischnummer,
        constants.turniername,
        constants.disziplin,
        effective_part,
    ]
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
