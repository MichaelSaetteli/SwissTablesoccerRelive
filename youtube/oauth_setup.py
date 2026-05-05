"""Google OAuth 2.0 setup for YouTube Data API v3.

Two flows:

  * **First-time interactive**: ``run_setup_flow`` opens a local-server
    OAuth flow that requires a browser. Run this once on a laptop, then
    copy the resulting ``token.json`` to the NAS (the DS1522+ has no
    browser of its own). ``INSTALL.md`` (Schritt 5) documents the steps.

  * **Headless runtime**: ``load_credentials`` reads the saved token,
    refreshes it transparently when expired, and persists the refreshed
    blob atomically. The pipeline never needs interactive auth at runtime.

The Google libs (``google-auth``, ``google-auth-oauthlib``,
``google-api-python-client``) are imported lazily inside each helper so
the rest of the codebase can be unit-tested without them installed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, List, Sequence

sys.stdout.reconfigure(encoding="utf-8")


# Briefing s.6: only what we need - upload videos + manage playlists.
SCOPES: List[str] = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------

def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save_credentials(token_path: Path, creds: Any) -> None:
    """Persist *creds* (Google Credentials object) to *token_path*."""
    _atomic_write_text(token_path, creds.to_json())


def load_credentials(token_path: Path, scopes: Sequence[str] = SCOPES) -> Any:
    """Load the saved credentials and refresh them if needed.

    Returns ``None`` if the token file is missing or invalid.
    Re-saves the token after a successful refresh so subsequent calls
    use the new access token immediately.
    """
    if not token_path.is_file():
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.auth.exceptions import RefreshError

    try:
        creds = Credentials.from_authorized_user_file(
            str(token_path), list(scopes)
        )
    except (ValueError, json.JSONDecodeError):
        return None

    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        save_credentials(token_path, creds)
        return creds
    return None


# ---------------------------------------------------------------------------
# First-time interactive flow (run on a laptop with a browser)
# ---------------------------------------------------------------------------

def run_setup_flow(
    client_secrets_path: Path,
    token_path: Path,
    scopes: Sequence[str] = SCOPES,
    port: int = 0,
) -> Any:
    """Walk the operator through a one-time OAuth flow.

    Briefing recommends doing this on a normal laptop (the DS1522+ has
    no browser). The resulting ``token.json`` is then copied to the
    NAS's config dir.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_path), list(scopes)
    )
    creds = flow.run_local_server(port=port)
    save_credentials(token_path, creds)
    return creds


# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------

def build_youtube_service(creds: Any) -> Any:
    """Return a ready-to-use ``googleapiclient`` YouTube service object."""
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Sequence[str]) -> int:
    if len(argv) != 3:
        print("Usage: python -m youtube.oauth_setup "
              "<client_secrets.json> <token.json>")
        return 2
    secrets_path = Path(argv[1])
    token_path = Path(argv[2])
    if not secrets_path.is_file():
        print(f"ERROR: client secrets not found: {secrets_path}",
              file=sys.stderr)
        return 1
    print("Opening browser for one-time YouTube authorisation...")
    run_setup_flow(secrets_path, token_path)
    print(f"Token saved to {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
