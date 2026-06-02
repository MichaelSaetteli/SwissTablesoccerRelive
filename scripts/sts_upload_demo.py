"""End-to-end CLI demo client for the STS-Upload contract (Issue #15).

This is NOT the polished GUI tool described in the issue — it is the
minimal proof-of-concept that exercises the server contract from start
to finish so the contract can be tested without a GUI.

Reads a card root (must contain ``.sts-card.json``), walks its files,
uploads them through the public HTTP API, calls finish + release.

Usage::

    python -m scripts.sts_upload_demo \\
        --card E:\\ \\
        --server http://192.168.1.159:8080 \\
        --username admin \\
        --password <pw> \\
        [--auto-release]

Authentication uses the standard login form so the same session cookie
the web UI uses also authorises the uploads. No new credentials needed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

import requests  # type: ignore[import-not-found]

sys.stdout.reconfigure(encoding="utf-8")

MARKER_NAME = ".sts-card.json"
# Match upload_staging.manifest IGNORE_*; the client must not count
# housekeeping files in expected_files/expected_bytes.
IGNORE_NAMES = {MARKER_NAME, ".upload_meta.json"}
IGNORE_SUFFIXES = (".partial",)


def _read_marker(card_root: Path) -> dict:
    marker_path = card_root / MARKER_NAME
    if not marker_path.is_file():
        raise SystemExit(
            f"No {MARKER_NAME} on {card_root}. "
            f"Use prepare_card.py to label the card first."
        )
    with marker_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _walk_files(card_root: Path) -> Iterable[Tuple[Path, int, str]]:
    for entry in sorted(card_root.rglob("*")):
        if not entry.is_file():
            continue
        if entry.name in IGNORE_NAMES:
            continue
        if any(entry.name.endswith(s) for s in IGNORE_SUFFIXES):
            continue
        rel = entry.relative_to(card_root).as_posix()
        yield entry, entry.stat().st_size, rel


def _summarise(card_root: Path) -> Tuple[int, int, List[Tuple[Path, str]]]:
    total_files = 0
    total_bytes = 0
    items: List[Tuple[Path, str]] = []
    for path, size, rel in _walk_files(card_root):
        total_files += 1
        total_bytes += size
        items.append((path, rel))
    return total_files, total_bytes, items


def _login(session: requests.Session, base: str, user: str, pw: str) -> None:
    resp = session.post(
        f"{base}/login",
        data={"username": user, "password": pw},
        allow_redirects=False,
    )
    if resp.status_code not in (302, 303):
        raise SystemExit(f"login failed: {resp.status_code} {resp.text[:200]}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="STS-Upload demo CLI client.")
    parser.add_argument("--card", type=Path, required=True,
                        help="SD card root, must contain .sts-card.json.")
    parser.add_argument("--server", type=str, required=True,
                        help="Base URL of the NAS, e.g. http://192.168.1.159:8080")
    parser.add_argument("--username", type=str, default="admin")
    parser.add_argument("--password", type=str, required=True)
    parser.add_argument("--auto-release", action="store_true",
                        help="Pass auto_release=true so the server skips the "
                             "explicit release step.")
    args = parser.parse_args(argv)

    marker = _read_marker(args.card)
    print(f"Card marker: {marker['table']} / {marker['discipline']} / "
          f"{marker['tournament_name']}")

    total_files, total_bytes, items = _summarise(args.card)
    print(f"Walking card: {total_files} files, "
          f"{total_bytes / 1024 / 1024:.1f} MB total")

    if total_files == 0:
        raise SystemExit("no files to upload (empty card or all filtered)")

    base = args.server.rstrip("/")
    session = requests.Session()
    _login(session, base, args.username, args.password)

    # ---- start ----
    start_payload = {
        "tournament_id": marker["tournament_id"],
        "discipline": marker["discipline"],
        "table_name": marker["table"],
        "expected_files": total_files,
        "expected_bytes": total_bytes,
        "auto_release": args.auto_release,
        "card_uuid": marker["card_uuid"],
    }
    resp = session.post(f"{base}/api/upload/start", json=start_payload)
    resp.raise_for_status()
    card = resp.json()
    card_id = card["id"]
    print(f"Upload started: card id={card_id}, staging={card['staging_path']}")

    # ---- chunks ----
    sent_files = 0
    sent_bytes = 0
    for path, rel in items:
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "application/octet-stream")}
            data = {"relative_name": rel}
            resp = session.post(
                f"{base}/api/upload/{card_id}/chunk",
                data=data, files=files,
            )
            resp.raise_for_status()
        sent_files += 1
        sent_bytes += path.stat().st_size
        print(f"  [{sent_files}/{total_files}] {rel} "
              f"({sent_bytes / 1024 / 1024:.1f} MB sent)")

    # ---- finish ----
    resp = session.post(f"{base}/api/upload/{card_id}/finish")
    if resp.status_code == 422:
        print(f"FINISH failed (manifest mismatch): {resp.json()}")
        return 2
    resp.raise_for_status()
    finished = resp.json()
    print(f"Finished: state={finished['state']}")
    if finished["state"] == "released":
        # Auto-release path; we are done.
        print(f"Released atomically into {finished['final_path']}")
        return 0
    if args.auto_release:
        # Should not happen if the server respected the flag.
        print("WARNING: auto-release requested but state is not released.")

    # ---- release (only if not auto-released) ----
    resp = session.post(
        f"{base}/api/upload/release", json={"card_ids": [card_id]},
    )
    resp.raise_for_status()
    released = resp.json()["released"][0]
    print(f"Released: state={released['state']} "
          f"final={released.get('final_path')}")
    return 0 if released["state"] == "released" else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
