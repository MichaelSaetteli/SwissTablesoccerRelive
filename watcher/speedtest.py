"""Ookla speedtest runner + last-result persistence (Dashboard M3 / B3).

A daily, idle-gated internet-speed probe for the dashboard's
"infrastructure pulse". We shell out to the official Ookla ``speedtest``
binary (installed in the Docker image) with ``--format=json`` and persist
the last measurement next to the operator's configs so the Web-UI can
render it without re-running the test on every request.

The JSON parser is pure (testable with a captured sample); the subprocess
call accepts an injected *runner* so tests never touch the network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from pipeline.status_file import now_iso

sys.stdout.reconfigure(encoding="utf-8")

OOKLA_BINARY = "speedtest"


@dataclass
class SpeedtestResult:
    download_mbit_s: float
    upload_mbit_s: float
    ping_ms: float
    server: str
    measured_at: str
    ok: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _error_result(error: str) -> SpeedtestResult:
    return SpeedtestResult(
        download_mbit_s=0.0, upload_mbit_s=0.0, ping_ms=0.0, server="",
        measured_at=now_iso(), ok=False, error=error[:300],
    )


def parse_ookla_json(raw: str) -> SpeedtestResult:
    """Parse Ookla CLI ``--format=json`` output into a SpeedtestResult.

    Ookla reports ``bandwidth`` in bytes/s; we convert to Mbit/s.
    """
    data = json.loads(raw)
    dl_bps = float(data["download"]["bandwidth"])
    ul_bps = float(data["upload"]["bandwidth"])
    ping = float(data.get("ping", {}).get("latency", 0.0))
    server = str(data.get("server", {}).get("name", ""))
    return SpeedtestResult(
        download_mbit_s=round(dl_bps * 8 / 1_000_000, 1),
        upload_mbit_s=round(ul_bps * 8 / 1_000_000, 1),
        ping_ms=round(ping, 1),
        server=server,
        measured_at=now_iso(),
    )


def run_speedtest(
    *,
    binary: str = OOKLA_BINARY,
    timeout: float = 120.0,
    runner: Optional[Callable[[list], str]] = None,
) -> SpeedtestResult:
    """Run the Ookla CLI and return a SpeedtestResult (never raises).

    *runner* (cmd -> stdout str) is injectable so tests stay offline.
    """
    cmd = [binary, "--format=json", "--accept-license", "--accept-gdpr"]
    try:
        if runner is not None:
            raw = runner(cmd)
        else:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            if proc.returncode != 0:
                return _error_result(
                    proc.stderr.strip() or f"speedtest exit {proc.returncode}"
                )
            raw = proc.stdout
        return parse_ookla_json(raw)
    except FileNotFoundError:
        return _error_result(f"speedtest binary not found: {binary}")
    except subprocess.TimeoutExpired:
        return _error_result(f"speedtest timed out after {timeout}s")
    except (ValueError, KeyError) as exc:
        return _error_result(f"unparseable speedtest output: {exc}")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def speedtest_path_for(config_dir: Path) -> Path:
    return Path(config_dir) / "speedtest_last.json"


def write_speedtest(path: Path, result: SpeedtestResult) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_speedtest(path: Path) -> Optional[SpeedtestResult]:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SpeedtestResult(**data)
    except (OSError, ValueError, TypeError):
        return None
