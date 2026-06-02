"""PyInstaller entry point for the STS-Upload operator GUI (Issue #15).

PyInstaller needs a real script path (not ``python -m``); this thin
launcher is what the spec bundles. It is equivalent to
``python -m upload_client.ui``.
"""

from __future__ import annotations

from upload_client.ui.app import run

if __name__ == "__main__":
    raise SystemExit(run())
