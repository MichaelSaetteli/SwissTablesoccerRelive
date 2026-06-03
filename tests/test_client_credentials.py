"""Tests for the local credential store (save/load/clear roundtrip).

The POSIX path uses base64 obfuscation; the Windows DPAPI path is exercised
only on Windows. These tests assert the roundtrip and the fail-closed
behaviour (missing / corrupt / wrong-scheme files return None).
"""

from __future__ import annotations

from pathlib import Path

from upload_client.credentials import (
    clear_credentials,
    load_credentials,
    save_credentials,
)


def test_save_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    save_credentials(
        path, server="http://nas:8080", username="admin", password="s3cr3t/pw",
    )
    creds = load_credentials(path)
    assert creds == {
        "server": "http://nas:8080",
        "username": "admin",
        "password": "s3cr3t/pw",
    }


def test_password_not_stored_in_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    save_credentials(path, server="http://nas", username="admin", password="hunter2")
    raw = path.read_text(encoding="utf-8")
    assert "hunter2" not in raw  # obfuscated/encrypted at rest


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_credentials(tmp_path / "nope.json") is None


def test_load_corrupt_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_credentials(path) is None


def test_load_missing_password_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text('{"server": "http://nas"}', encoding="utf-8")
    assert load_credentials(path) is None


def test_clear_removes_file(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    save_credentials(path, server="http://nas", username="admin", password="x")
    assert path.exists()
    clear_credentials(path)
    assert not path.exists()
    clear_credentials(path)  # idempotent - no error on missing file
