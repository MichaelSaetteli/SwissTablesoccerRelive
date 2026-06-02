"""PySide6 GUI for the STS-Upload tool (Issue #15, Slice 2).

A thin view over the headless core (``upload_client``): the window polls
``UploadManager.snapshot()`` on a timer and renders it; all upload work
happens in the manager's background thread pool. Importing this package
pulls in PySide6, so it is kept separate from the GUI-free core.
"""

from __future__ import annotations
