"""Resume-aware per-card upload orchestration.

Drives one card through the server contract - start -> chunk* -> finish
-> (release) - while persisting progress to a local ``StateStore`` after
every step so a crash / card-pull / network drop can be resumed instead
of restarted (Issue #15 §6a, "Resumability" + "Hardware-State-Patterns").

Client-side state vs. server state
----------------------------------
The server only knows ``uploading / verified / released / failed /
cancelled``. ``interrupted`` is a *client-local* concept: the card row
stays ``uploading`` on the server while the client pauses (card pulled /
network gone). Resume = send the remaining files against the same
``card_id`` and call ``finish``. A *failed* card (manifest mismatch) is
retried in place via ``POST /api/upload/<id>/reopen`` (failed ->
uploading): the ``card_uuid`` and the already-staged chunks are kept, the
client re-sends and re-``finish``es. Only a server row that is genuinely
``cancelled`` forces a fresh start under a new id.

The engine is transport-agnostic (it talks only to ``ApiClient``) and has
no GUI / threading concerns, so it is fully unit-testable against a fake
HTTP session.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from upload_client.api_client import ApiClient, ApiError, ManifestRejected
from upload_client.manifest import CardManifest
from upload_client.marker import CardMarker
from upload_client.state_store import StateStore

# Client-side card states.
CLIENT_PENDING = "pending"          # scanned, nothing sent yet
CLIENT_UPLOADING = "uploading"
CLIENT_VERIFIED = "verified"
CLIENT_RELEASED = "released"
CLIENT_FAILED = "failed"            # manifest mismatch - retryable
CLIENT_INTERRUPTED = "interrupted"  # paused mid-upload - resumable
CLIENT_CANCELLED = "cancelled"

# States in which the card is safe to physically remove (§6a Safe-to-remove).
SAFE_TO_REMOVE_STATES = frozenset({CLIENT_VERIFIED, CLIENT_RELEASED})

logger = logging.getLogger("sts_upload.engine")


class EngineError(RuntimeError):
    """Base class for engine-level failures."""


class UploadInterrupted(EngineError):
    """A transport error paused the upload; it is resumable.

    Carries the persisted ``CardProgress`` so the caller (GUI worker) can
    show the red "Nicht entfernen / fortsetzen" affordance.
    """

    def __init__(self, progress: "CardProgress", cause: Exception) -> None:
        super().__init__(str(cause))
        self.progress = progress
        self.cause = cause


@dataclass
class CardProgress:
    """Local upload record for one card, keyed by the marker ``card_uuid``."""

    card_uuid: str                      # marker uuid = stable local key
    table: str
    discipline: str
    tournament_id: int
    expected_files: int
    expected_bytes: int
    state: str = CLIENT_PENDING
    server_card_id: Optional[int] = None
    server_card_uuid: Optional[str] = None
    staging_path: Optional[str] = None
    final_path: Optional[str] = None
    auto_release: bool = False
    sent: List[str] = field(default_factory=list)
    received_bytes: int = 0
    error: Optional[str] = None
    updated_at: Optional[str] = None
    # Wall-clock epoch seconds; persisted so the GUI "Dauer" survives a resume
    # (tool restart mid-upload) instead of restarting from zero.
    started_at: Optional[float] = None    # first time the card began uploading
    finished_at: Optional[float] = None   # first time it reached verified/released

    @property
    def sent_files(self) -> int:
        return len(self.sent)

    @property
    def safe_to_remove(self) -> bool:
        return self.state in SAFE_TO_REMOVE_STATES

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "CardProgress":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})

    @classmethod
    def from_marker(cls, marker: CardMarker, manifest: CardManifest) -> "CardProgress":
        return cls(
            card_uuid=marker.card_uuid,
            table=marker.table,
            discipline=marker.discipline,
            tournament_id=marker.tournament_id,
            expected_files=manifest.total_files,
            expected_bytes=manifest.total_bytes,
        )


# progress_cb(progress, event) - event in {started, chunk, verified,
# released, failed, interrupted, cancelled}.
ProgressCb = Callable[["CardProgress", str], None]


class UploadEngine:
    """Orchestrates uploads and persists resumable progress."""

    def __init__(
        self,
        api: ApiClient,
        store: StateStore,
        *,
        progress_cb: Optional[ProgressCb] = None,
    ) -> None:
        self._api = api
        self._store = store
        self._cb = progress_cb

    # -- public API --------------------------------------------------------

    def load(self, card_uuid: str) -> Optional[CardProgress]:
        raw = self._store.get(card_uuid)
        return CardProgress.from_dict(raw) if raw is not None else None

    def resumable(self) -> List[CardProgress]:
        """Cards that were mid-upload when the tool last stopped."""
        out: List[CardProgress] = []
        for raw in self._store.all().values():
            p = CardProgress.from_dict(raw)
            if p.state in (CLIENT_UPLOADING, CLIENT_INTERRUPTED):
                out.append(p)
        return out

    def reset_local_state(self) -> None:
        """Forget all local resume records (the ``state.json`` reset button).

        Clears every persisted ``CardProgress`` so the next scan starts from
        a clean slate. Server-side rows are untouched - this only drops the
        client's local resume bookkeeping, which is the manual
        "delete state.json" step turned into a safe in-app action.
        """
        self._store.clear()

    def mark_interrupted(self, card_uuid: str) -> Optional[CardProgress]:
        """Client-local uploading -> interrupted (card physically removed).

        Only flips a card that is currently ``uploading``; the server row
        stays ``uploading`` (interrupted is a client concept). Resuming
        later re-sends the remaining files against the same card.
        """
        p = self.load(card_uuid)
        if p is None or p.state != CLIENT_UPLOADING:
            return p
        p.state = CLIENT_INTERRUPTED
        self._save(p, "interrupted")
        return p

    def upload(
        self,
        marker: CardMarker,
        manifest: CardManifest,
        *,
        auto_release: bool = False,
    ) -> CardProgress:
        """Upload one card end to end, resuming if a record exists.

        Returns the final ``CardProgress``. Raises ``UploadInterrupted`` on
        a transport failure during start/chunk (resumable). A manifest
        mismatch is not raised - it returns a record in ``failed`` so a
        batch can continue and the UI can offer "Erneut hochladen".
        """
        progress = self.load(marker.card_uuid) or CardProgress.from_marker(
            marker, manifest,
        )
        # Refresh expectations from the current manifest (card contents are
        # the source of truth) and the requested release mode.
        progress.expected_files = manifest.total_files
        progress.expected_bytes = manifest.total_bytes
        progress.auto_release = auto_release

        if progress.state == CLIENT_RELEASED:
            return progress

        self._ensure_started(progress, marker, manifest)
        if progress.state in (CLIENT_VERIFIED, CLIENT_RELEASED):
            return progress  # adopted an already-finished server card

        self._send_remaining(progress, manifest)
        return self._finish(progress)

    def release(self, card_uuids: List[str]) -> List[CardProgress]:
        """Release verified cards in one atomic batch (§6a batch release)."""
        wanted: Dict[int, CardProgress] = {}
        for cu in card_uuids:
            p = self.load(cu)
            if p is None or p.state != CLIENT_VERIFIED or p.server_card_id is None:
                continue
            wanted[p.server_card_id] = p
        if not wanted:
            return []

        released = self._api.release(list(wanted.keys()))
        results: List[CardProgress] = []
        for card in released:
            cid = card.get("id")
            p = wanted.get(cid)
            if p is None:
                continue
            self._apply_server_card(p, card)
            p.state = CLIENT_RELEASED
            self._save(p, "released")
            results.append(p)
        return results

    def cancel(self, card_uuid: str) -> Optional[CardProgress]:
        """Cancel an in-flight card and wipe its server staging dir."""
        p = self.load(card_uuid)
        if p is None or p.server_card_id is None:
            return p
        card = self._api.cancel(p.server_card_id)
        self._apply_server_card(p, card)
        p.state = CLIENT_CANCELLED
        self._save(p, "cancelled")
        return p

    def retry(
        self,
        marker: CardMarker,
        manifest: CardManifest,
        *,
        auto_release: bool = False,
    ) -> CardProgress:
        """Retry a failed card. Explicit alias for ``upload``.

        ``upload`` already reopens a ``failed`` record in place (re-send +
        finish) via the server's reopen endpoint, so retry is just the same
        call - kept as a named entry point for the GUI's "Erneut hochladen".
        """
        return self.upload(marker, manifest, auto_release=auto_release)

    # -- internals ---------------------------------------------------------

    def _ensure_started(
        self, progress: CardProgress, marker: CardMarker, manifest: CardManifest,
    ) -> None:
        """Make sure there is a server card to upload into.

        Reuses an existing server card_id (resume), adopts a matching
        server row if our local record was lost, or starts a fresh upload.
        """
        if progress.server_card_id is not None:
            if progress.state == CLIENT_FAILED:
                # Reopen in place. The mismatch means staging is not what we
                # think, so clear `sent` and re-send everything; the server
                # overwrites and re-walks staging at finish.
                card = self._api.reopen(progress.server_card_id)
                self._apply_server_card(progress, card)
                progress.sent = []
            progress.state = CLIENT_UPLOADING
            return

        try:
            card = self._api.start_upload(
                tournament_id=progress.tournament_id,
                discipline=progress.discipline,
                table_name=progress.table,
                expected_files=manifest.total_files,
                expected_bytes=manifest.total_bytes,
                auto_release=progress.auto_release,
                card_uuid=marker.card_uuid,
            )
        except ApiError as exc:
            adopted = self._adopt_or_restart(progress, marker, exc)
            if adopted is not None:
                return
            self._interrupt(progress, exc)
            raise UploadInterrupted(progress, exc)

        self._apply_server_card(progress, card)
        progress.server_card_uuid = card.get("card_uuid", marker.card_uuid)
        progress.sent = []
        progress.state = CLIENT_UPLOADING
        self._save(progress, "started")

    def _adopt_or_restart(
        self, progress: CardProgress, marker: CardMarker, exc: ApiError,
    ) -> Optional[CardProgress]:
        """Handle a 'card_uuid already exists' start failure.

        Find the existing server row; resume it if still uploadable, treat
        it as done if already verified/released, otherwise cancel the stale
        row and start a fresh upload (new server uuid).
        """
        if not _is_already_exists(exc):
            return None

        existing = self._find_server_card(marker)
        if existing is None:
            return None

        state = existing.get("state")
        if state == "released":
            self._apply_server_card(progress, existing)
            progress.state = CLIENT_RELEASED
            self._save(progress, "released")
            return progress
        if state == "verified":
            self._apply_server_card(progress, existing)
            progress.state = CLIENT_VERIFIED
            self._save(progress, "verified")
            return progress
        if state == "uploading":
            # Local record lost: adopt and re-send everything (the server
            # re-walks staging at finish, so re-sends are harmless).
            self._apply_server_card(progress, existing)
            progress.sent = []
            progress.state = CLIENT_UPLOADING
            self._save(progress, "started")
            return progress

        cid = existing.get("id")
        if state == "failed" and cid is not None:
            # Reopen in place rather than abandoning the staged chunks.
            card = self._api.reopen(int(cid))
            self._apply_server_card(progress, card)
            progress.server_card_uuid = existing.get("card_uuid")
            progress.sent = []
            progress.state = CLIENT_UPLOADING
            self._save(progress, "started")
            return progress

        # cancelled (terminal) or anything else: cancel + fresh start.
        if cid is not None:
            try:
                self._api.cancel(int(cid))
            except ApiError:
                pass
        card = self._api.start_upload(
            tournament_id=progress.tournament_id,
            discipline=progress.discipline,
            table_name=progress.table,
            expected_files=progress.expected_files,
            expected_bytes=progress.expected_bytes,
            auto_release=progress.auto_release,
        )  # no card_uuid -> server mints a fresh one
        self._apply_server_card(progress, card)
        progress.server_card_uuid = card.get("card_uuid")
        progress.sent = []
        progress.state = CLIENT_UPLOADING
        self._save(progress, "started")
        return progress

    def _find_server_card(self, marker: CardMarker) -> Optional[dict]:
        try:
            cards = self._api.status(discipline=marker.discipline)
        except ApiError:
            return None
        for card in cards:
            if card.get("card_uuid") == marker.card_uuid:
                return card
        return None

    def _send_remaining(self, progress: CardProgress, manifest: CardManifest) -> None:
        sent = set(progress.sent)
        for entry in manifest.files:
            if entry.relative_name in sent:
                continue
            try:
                with entry.path.open("rb") as fh:
                    card = self._api.upload_chunk(
                        progress.server_card_id,  # type: ignore[arg-type]
                        relative_name=entry.relative_name,
                        fileobj=fh,
                        filename=entry.path.name,
                    )
            except (ApiError, OSError) as exc:
                self._interrupt(progress, exc)
                raise UploadInterrupted(progress, exc)
            progress.sent.append(entry.relative_name)
            progress.received_bytes = int(
                card.get("received_bytes", progress.received_bytes + entry.size)
            )
            progress.state = CLIENT_UPLOADING
            self._save(progress, "chunk")

    def _finish(self, progress: CardProgress) -> CardProgress:
        try:
            card = self._api.finish_upload(progress.server_card_id)  # type: ignore[arg-type]
        except ManifestRejected as exc:
            self._apply_server_card(progress, exc.card)
            progress.state = CLIENT_FAILED
            progress.error = exc.payload.get("error_message") if isinstance(
                exc.payload, dict
            ) else str(exc)
            self._save(progress, "failed")
            return progress
        except (ApiError, OSError) as exc:
            self._interrupt(progress, exc)
            raise UploadInterrupted(progress, exc)

        self._apply_server_card(progress, card)
        if card.get("state") == "released":
            progress.state = CLIENT_RELEASED
            self._save(progress, "released")
        else:
            progress.state = CLIENT_VERIFIED
            self._save(progress, "verified")
        return progress

    def _interrupt(self, progress: CardProgress, cause: Exception) -> None:
        # Log the real cause: an interrupt is NOT always a pulled card - it is
        # any start/chunk/finish transport failure. The UI used to mask all of
        # these as "card removed", which hid server-side rejections.
        logger.warning(
            "Card %s (%s) interrupted: %s: %s",
            progress.table, progress.card_uuid,
            type(cause).__name__, cause,
            exc_info=cause,
        )
        progress.state = CLIENT_INTERRUPTED
        progress.error = f"{type(cause).__name__}: {cause}"
        self._save(progress, "interrupted")

    def _apply_server_card(self, progress: CardProgress, card: dict) -> None:
        if card.get("id") is not None:
            progress.server_card_id = int(card["id"])
        if card.get("staging_path"):
            progress.staging_path = card["staging_path"]
        if card.get("final_path"):
            progress.final_path = card["final_path"]
        if card.get("error_message"):
            progress.error = card["error_message"]

    def _save(self, progress: CardProgress, event: str) -> None:
        progress.updated_at = _now()
        # Stamp the elapsed-time anchors once, on the first transition into
        # each phase, so the GUI duration clock is correct across a resume.
        if progress.state == CLIENT_UPLOADING and progress.started_at is None:
            progress.started_at = time.time()
        if (
            progress.state in (CLIENT_VERIFIED, CLIENT_RELEASED)
            and progress.finished_at is None
        ):
            progress.finished_at = time.time()
        logger.info(
            "Card %s: %s (state=%s, %d/%d files, server_id=%s)",
            progress.table, event, progress.state,
            progress.sent_files, progress.expected_files,
            progress.server_card_id,
        )
        self._store.put(progress.card_uuid, progress.to_dict())
        if self._cb is not None:
            self._cb(progress, event)


def _is_already_exists(exc: ApiError) -> bool:
    payload = exc.payload
    msg = ""
    if isinstance(payload, dict):
        msg = str(payload.get("error", ""))
    return "already exists" in msg or "already exists" in str(exc)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
