"""STS-Upload client tool (Issue #15, Slice 2) - headless core.

This package is the platform-independent, GUI-free core of the operator
upload tool. It owns everything that must stay unit-testable without a
display: reading SD-card markers, computing the upload manifest, talking
to the server contract (the 7 ``/api/upload/*`` endpoints from Slice 1),
and driving the resumable per-card upload state machine.

The PySide6 GUI (next block) binds to this core as a thin view; the
hardware mount/unmount monitoring and the PyInstaller packaging are
separate later blocks. Keeping Qt out of here is intentional - see
``docs/SESSION_HANDOFF.md`` Slice-2 plan.

The client builds *exclusively* against the documented server contract
(``docs/UPLOAD_CLIENT_HOWTO.md``); it never reaches into the server's
database or filesystem directly.
"""

from __future__ import annotations

from upload_client.api_client import (
    ApiClient,
    ApiError,
    AuthError,
    ManifestRejected,
)
from upload_client.card_scanner import (
    CARD_EMPTY,
    CARD_NO_MARKER,
    CARD_READY,
    CARD_WRONG_TOURNAMENT,
    ScannedCard,
    merge_scans,
    scan_mounts,
)
from upload_client.manifest import (
    IGNORE_NAMES,
    IGNORE_SUFFIXES,
    CardManifest,
    FileEntry,
    build_manifest,
)
from upload_client.mount_watcher import MountChange, MountWatcher
from upload_client.marker import (
    MARKER_NAME,
    CardMarker,
    MarkerError,
    has_marker,
    read_marker,
)
from upload_client.state_store import StateStore
from upload_client.upload_engine import (
    CLIENT_CANCELLED,
    CLIENT_FAILED,
    CLIENT_INTERRUPTED,
    CLIENT_PENDING,
    CLIENT_RELEASED,
    CLIENT_UPLOADING,
    CLIENT_VERIFIED,
    CardProgress,
    EngineError,
    UploadEngine,
    UploadInterrupted,
)

__all__ = [
    "ApiClient",
    "ApiError",
    "AuthError",
    "ManifestRejected",
    "CardMarker",
    "MarkerError",
    "MARKER_NAME",
    "has_marker",
    "read_marker",
    "CardManifest",
    "FileEntry",
    "build_manifest",
    "IGNORE_NAMES",
    "IGNORE_SUFFIXES",
    "ScannedCard",
    "scan_mounts",
    "merge_scans",
    "MountWatcher",
    "MountChange",
    "CARD_READY",
    "CARD_NO_MARKER",
    "CARD_WRONG_TOURNAMENT",
    "CARD_EMPTY",
    "StateStore",
    "UploadEngine",
    "CardProgress",
    "EngineError",
    "UploadInterrupted",
    "CLIENT_PENDING",
    "CLIENT_UPLOADING",
    "CLIENT_VERIFIED",
    "CLIENT_RELEASED",
    "CLIENT_FAILED",
    "CLIENT_INTERRUPTED",
    "CLIENT_CANCELLED",
]
