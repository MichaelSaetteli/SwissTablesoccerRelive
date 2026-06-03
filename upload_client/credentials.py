"""Local credential store so operators don't retype the password each launch.

3-5 trusted operators share one server password; typing it on every start is
pure friction. With the "Angemeldet bleiben" option the credentials are saved
under ``~/.sts_upload/credentials.json`` and the next launch auto-logs-in,
skipping the dialog entirely.

The password is protected at rest:
  * Windows: DPAPI (``CryptProtectData``) - encrypted to the current Windows
    user account, so another user on the same machine cannot read it. No key
    management, no extra dependency.
  * POSIX (dev/CI): base64 + ``0600`` file mode. This is obfuscation, not
    strong crypto; the real target platform is Windows.

The scheme is recorded as a prefix (``dpapi:`` / ``b64:``) so a file written
on one platform fails closed (returns None) rather than handing back garbage.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """Encrypt/decrypt *data* with the Windows DPAPI (per-user key)."""
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _Blob()
    fn = (
        ctypes.windll.crypt32.CryptProtectData if protect  # type: ignore[attr-defined]
        else ctypes.windll.crypt32.CryptUnprotectData  # type: ignore[attr-defined]
    )
    ok = fn(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore[attr-defined]


def _protect(secret: str) -> str:
    raw = secret.encode("utf-8")
    if sys.platform.startswith("win"):
        return "dpapi:" + base64.b64encode(_dpapi(raw, protect=True)).decode("ascii")
    return "b64:" + base64.b64encode(raw).decode("ascii")


def _unprotect(token: str) -> Optional[str]:
    try:
        scheme, _, payload = token.partition(":")
        blob = base64.b64decode(payload)
        if scheme == "dpapi":
            return _dpapi(blob, protect=False).decode("utf-8")
        if scheme == "b64":
            return blob.decode("utf-8")
    except Exception:  # noqa: BLE001 - any failure -> treat as no creds
        return None
    return None


def save_credentials(
    path: Path, *, server: str, username: str, password: str,
) -> None:
    """Persist credentials (password protected) atomically, owner-only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "server": server,
        "username": username,
        "password": _protect(password),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best-effort on platforms without POSIX modes


def load_credentials(path: Path) -> Optional[Dict[str, str]]:
    """Return {server, username, password} or None if absent/unreadable."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    password = _unprotect(str(doc.get("password", "")))
    server = doc.get("server")
    if not server or password is None:
        return None
    return {
        "server": str(server),
        "username": str(doc.get("username", "admin")),
        "password": password,
    }


def clear_credentials(path: Path) -> None:
    """Forget saved credentials (used when 'remember' is unchecked)."""
    try:
        Path(path).unlink()
    except OSError:
        pass
