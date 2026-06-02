"""Server-side orchestration of the per-card upload lifecycle.

Each function maps to one HTTP endpoint in web/app.py. They are kept here
(not in web/services.py) because the upload flow is large enough to
warrant its own module and has no Flask-specific dependencies.

The functions are deliberately thin wrappers around db.uploads + the
filesystem helpers; the heavy lifting (manifest, atomic_release) lives
in dedicated modules so each piece is testable on its own.
"""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from db import uploads as db_uploads
from db.tournaments import get_tournament
from upload_staging.atomic_handoff import HandoffError, atomic_release
from upload_staging.manifest import ManifestError, verify_manifest
from upload_staging.paths import (
    StagingPathError,
    final_path_for,
    staging_path_for,
    validate_staging_root,
)


class UploadServiceError(RuntimeError):
    """Service-layer envelope - maps to 4xx in the HTTP layer."""


def _staging_root_for(eingang_root: Path) -> Path:
    """Sibling of eingang, on the same volume: /volume1/SDD/staging_<disc>.

    We derive instead of taking a separate config knob to make INV-1
    impossible to misconfigure: if eingang is on SSD, staging is too.
    """
    eingang_root = Path(eingang_root)
    return eingang_root.with_name(
        eingang_root.name.replace("eingang_", "staging_", 1)
    )


def start_upload(
    conn: sqlite3.Connection,
    *,
    eingang_root: Path,
    tournament_id: int,
    table_name: str,
    discipline: str,
    expected_files: int,
    expected_bytes: int,
    auto_release: bool = False,
    card_uuid: Optional[str] = None,
) -> db_uploads.UploadCard:
    """Reserve a staging dir + DB row for a new card upload.

    If `card_uuid` is omitted, a fresh one is minted server-side. Callers
    that want resume semantics (client crashed and is re-trying) should
    pass the same uuid they used originally; we will refuse if a card
    with that uuid already exists (the client must explicitly cancel +
    restart instead, to keep the state machine clean).
    """
    if get_tournament(conn, tournament_id) is None:
        raise UploadServiceError(f"tournament id={tournament_id} not found")

    if expected_files <= 0 or expected_bytes <= 0:
        raise UploadServiceError(
            f"expected_files and expected_bytes must be > 0; "
            f"got files={expected_files}, bytes={expected_bytes}"
        )

    staging_root = _staging_root_for(eingang_root)
    try:
        validate_staging_root(staging_root)
    except StagingPathError as exc:
        raise UploadServiceError(str(exc)) from exc

    cuuid = card_uuid or uuid.uuid4().hex
    if db_uploads.get_upload_card_by_uuid(conn, cuuid) is not None:
        raise UploadServiceError(
            f"card_uuid {cuuid!r} already exists - explicitly cancel "
            f"the previous attempt before retrying"
        )

    try:
        staging_dir = staging_path_for(staging_root, cuuid)
    except StagingPathError as exc:
        raise UploadServiceError(str(exc)) from exc

    staging_dir.mkdir(parents=True, exist_ok=False)

    try:
        card = db_uploads.create_upload_card(
            conn,
            card_uuid=cuuid,
            tournament_id=tournament_id,
            table_name=table_name,
            discipline=discipline,
            expected_files=expected_files,
            expected_bytes=expected_bytes,
            staging_path=str(staging_dir),
            auto_release=auto_release,
        )
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return card


def store_chunk(
    conn: sqlite3.Connection,
    card_id: int,
    *,
    relative_name: str,
    stream: BinaryIO,
) -> db_uploads.UploadCard:
    """Write one file from the upload stream into the staging dir.

    `relative_name` is the path the client wants under staging_path. We
    sanitise it so the client cannot escape the staging dir via
    ``../../etc/passwd``. The progress counters are updated in the same
    transaction as the actual write.

    Existing files with the same name are overwritten (clients may retry
    a chunk after a transient error). We do NOT support partial chunk
    appends in the MVP - a chunk equals one file.
    """
    card = db_uploads.get_upload_card(conn, card_id)
    if card is None:
        raise UploadServiceError(f"upload card id={card_id} not found")
    if card.state != db_uploads.STATE_UPLOADING:
        raise UploadServiceError(
            f"card id={card_id} is in state {card.state!r}; "
            f"cannot accept chunks (must be 'uploading')"
        )

    safe = _sanitise_relative(relative_name)
    if safe is None:
        raise UploadServiceError(
            f"relative_name {relative_name!r} is not safe (path traversal?)"
        )

    target = Path(card.staging_path) / safe
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".partial")

    written = 0
    with tmp.open("wb") as fh:
        while True:
            buf = stream.read(1024 * 1024)
            if not buf:
                break
            fh.write(buf)
            written += len(buf)

    # Replace any prior version of this file. We do not silently dedupe -
    # if the same name is sent twice with different bytes, the second wins
    # and the verify step will catch a size mismatch at finish-time.
    if target.exists():
        target.unlink()
    tmp.rename(target)

    received_files = card.received_files + 1
    received_bytes = card.received_bytes + written
    updated = db_uploads.update_progress(
        conn, card_id,
        received_files=received_files,
        received_bytes=received_bytes,
    )
    assert updated is not None
    return updated


def _sanitise_relative(name: str) -> Optional[str]:
    """Refuse names with traversal, absolute paths or NUL bytes."""
    if not name or "\x00" in name:
        return None
    norm = name.replace("\\", "/")
    if norm.startswith("/"):
        return None
    parts = norm.split("/")
    for p in parts:
        if p in ("", ".", ".."):
            return None
    return "/".join(parts)


def finish_upload(
    conn: sqlite3.Connection,
    card_id: int,
    *,
    eingang_root: Path,
) -> db_uploads.UploadCard:
    """Verify manifest. On success: state -> verified, or released if auto.

    The eingang_root is taken from the per-discipline config and not from
    the DB row because pipeline configs are the source of truth for where
    runs land.
    """
    card = db_uploads.get_upload_card(conn, card_id)
    if card is None:
        raise UploadServiceError(f"upload card id={card_id} not found")
    if card.state != db_uploads.STATE_UPLOADING:
        raise UploadServiceError(
            f"card id={card_id} is in state {card.state!r}; "
            f"cannot finish (must be 'uploading')"
        )

    try:
        verify_manifest(
            Path(card.staging_path),
            expected_files=card.expected_files,
            expected_bytes=card.expected_bytes,
        )
    except ManifestError as exc:
        return db_uploads.transition(
            conn, card_id, db_uploads.STATE_FAILED,
            error_message=str(exc),
        )

    verified = db_uploads.transition(conn, card_id, db_uploads.STATE_VERIFIED)
    if not card.auto_release:
        return verified

    return release_card(conn, card_id, eingang_root=eingang_root)


def release_card(
    conn: sqlite3.Connection,
    card_id: int,
    *,
    eingang_root: Path,
) -> db_uploads.UploadCard:
    """Atomic rename staging -> eingang_<disc>/ETxx. State -> released."""
    card = db_uploads.get_upload_card(conn, card_id)
    if card is None:
        raise UploadServiceError(f"upload card id={card_id} not found")
    if card.state != db_uploads.STATE_VERIFIED:
        raise UploadServiceError(
            f"card id={card_id} is in state {card.state!r}; "
            f"cannot release (must be 'verified')"
        )

    final_path = final_path_for(Path(eingang_root), card.table_name)
    try:
        atomic_release(Path(card.staging_path), final_path)
    except HandoffError as exc:
        return db_uploads.transition(
            conn, card_id, db_uploads.STATE_FAILED,
            error_message=str(exc),
        )

    return db_uploads.transition(
        conn, card_id, db_uploads.STATE_RELEASED,
        final_path=str(final_path),
    )


def release_many(
    conn: sqlite3.Connection,
    card_ids: List[int],
    *,
    eingang_roots: Dict[str, Path],
) -> List[Tuple[int, db_uploads.UploadCard]]:
    """Release a batch of cards. eingang_roots is keyed by discipline."""
    results: List[Tuple[int, db_uploads.UploadCard]] = []
    for cid in card_ids:
        card = db_uploads.get_upload_card(conn, cid)
        if card is None:
            raise UploadServiceError(f"upload card id={cid} not found")
        root = eingang_roots.get(card.discipline)
        if root is None:
            raise UploadServiceError(
                f"no eingang_root configured for discipline "
                f"{card.discipline!r} (card id={cid})"
            )
        results.append((cid, release_card(conn, cid, eingang_root=root)))
    return results


def reopen_upload(
    conn: sqlite3.Connection, card_id: int,
) -> db_uploads.UploadCard:
    """Reopen a failed card for another attempt: failed -> uploading.

    A manifest mismatch leaves a card in ``failed`` with its staging dir
    intact. The DB already allows failed -> uploading; exposing it lets the
    client re-send the missing/bad files and call ``finish`` again WITHOUT
    minting a new card_uuid or re-uploading what already arrived. The
    transition clears the previous error message. Idempotent if the card is
    already ``uploading``.
    """
    card = db_uploads.get_upload_card(conn, card_id)
    if card is None:
        raise UploadServiceError(f"upload card id={card_id} not found")
    if card.state == db_uploads.STATE_UPLOADING:
        return card
    if card.state != db_uploads.STATE_FAILED:
        raise UploadServiceError(
            f"card id={card_id} is in state {card.state!r}; "
            f"only 'failed' cards can be reopened"
        )
    return db_uploads.transition(conn, card_id, db_uploads.STATE_UPLOADING)


def cancel_upload(
    conn: sqlite3.Connection, card_id: int,
) -> db_uploads.UploadCard:
    """Remove staging dir + mark cancelled. Idempotent for safety."""
    card = db_uploads.get_upload_card(conn, card_id)
    if card is None:
        raise UploadServiceError(f"upload card id={card_id} not found")
    if card.state == db_uploads.STATE_RELEASED:
        raise UploadServiceError(
            f"card id={card_id} is already released - cannot cancel"
        )
    if card.state != db_uploads.STATE_CANCELLED:
        shutil.rmtree(card.staging_path, ignore_errors=True)
        return db_uploads.transition(
            conn, card_id, db_uploads.STATE_CANCELLED,
        )
    return card


def status_snapshot(
    conn: sqlite3.Connection,
    *,
    tournament_id: Optional[int] = None,
    discipline: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """JSON-friendly snapshot for client UI polling."""
    cards = db_uploads.list_upload_cards(
        conn, tournament_id=tournament_id, discipline=discipline,
    )
    return [c.to_dict() for c in cards]
