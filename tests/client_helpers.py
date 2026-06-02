"""Shared helpers for the upload_client tests (not collected as tests).

Provides:
* ``FlaskSessionAdapter`` - makes a Flask test client quack like a
  ``requests.Session`` so ``upload_client.ApiClient`` can drive the REAL
  server contract end to end (no network, no live NAS).
* ``FakeSession`` / ``FakeResp`` - a tiny scripted HTTP double for unit
  tests that need deterministic error paths (422, auth failure).
* small builders for the Flask app, a tournament and an SD card on disk.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Dict, List, Optional

from db import open_db
from db.tournaments import create_tournament, set_active_tournament
from pipeline.config_loader import PipelineConfig, load_config
from web.app import create_app

TEST_USER = "tester"
TEST_PASS = "secret-pw"


# --------------------------------------------------------------------------
# Flask app / tournament / card builders
# --------------------------------------------------------------------------

def _config_dict(tmp_path: Path, discipline: str) -> dict:
    suffix = discipline.lower()
    return {
        "discipline": discipline,
        "enabled": True,
        "paths": {
            "eingang": str(tmp_path / f"eingang_{suffix}"),
            "work": str(tmp_path / f"work_{suffix}"),
            "output": str(tmp_path / f"output_{suffix}"),
            "logs": str(tmp_path / "logs"),
        },
        "filename_constants": {
            "jahr": "2026", "sts_nummer": "STS2",
            "turniername": "Seetal", "disziplin": discipline, "part": "",
        },
        "ffmpeg": {"max_workers": 2, "max_files_per_folder": 24},
        "youtube": {},
    }


def make_configs(tmp_path: Path) -> Dict[str, PipelineConfig]:
    out: Dict[str, PipelineConfig] = {}
    for discipline in ("Einzel", "Doppel"):
        cfg_path = tmp_path / f"config_{discipline.lower()}.json"
        cfg_path.write_text(
            json.dumps(_config_dict(tmp_path, discipline)), encoding="utf-8",
        )
        out[discipline] = load_config(cfg_path)
    return out


class _NoopRunner:
    def __call__(self, config: PipelineConfig):
        from watcher.status import StatusWriter, status_path_for
        return StatusWriter(status_path_for(config), config.discipline)


def make_app(configs: Dict[str, PipelineConfig]):
    app = create_app(
        configs,
        secret_key="test-secret",
        username=TEST_USER, password=TEST_PASS,
        runner=_NoopRunner(),
    )
    app.config["TESTING"] = True
    return app


def setup_tournament(configs: Dict[str, PipelineConfig], name: str = "Seetal 2026") -> int:
    cfg = next(iter(configs.values()))
    conn = open_db(cfg.source_path.parent / "runs.db")
    t = create_tournament(conn, name)
    assert t.id is not None
    set_active_tournament(conn, "Einzel", t.id)
    set_active_tournament(conn, "Doppel", t.id)
    return t.id


def make_card(
    root: Path,
    *,
    tournament_id: int,
    table: str = "ET01",
    discipline: str = "Einzel",
    card_uuid: str = "card-uuid-1",
    files: Optional[Dict[str, bytes]] = None,
    tournament_name: str = "Seetal 2026",
) -> Path:
    """Write a ``.sts-card.json`` marker + files to ``root``."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    marker = {
        "version": 1,
        "card_uuid": card_uuid,
        "tournament_id": tournament_id,
        "tournament_name": tournament_name,
        "discipline": discipline,
        "table": table,
    }
    (root / ".sts-card.json").write_text(
        json.dumps(marker), encoding="utf-8",
    )
    if files is None:
        files = {"video_001.mp4": b"a" * 100, "video_002.mp4": b"b" * 200}
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return root


# --------------------------------------------------------------------------
# Flask-test-client -> requests.Session adapter
# --------------------------------------------------------------------------

class _FlaskResp:
    def __init__(self, resp) -> None:
        self._resp = resp
        self.status_code = resp.status_code

    def json(self):
        return self._resp.get_json()


class FlaskSessionAdapter:
    """Translate ``requests``-style calls to a Flask test client.

    The same client instance is reused so the login session cookie carries
    across calls, exactly like ``requests.Session``.
    """

    def __init__(self, client, base_url: str = "http://server") -> None:
        self._client = client
        self._base = base_url

    def _path(self, url: str) -> str:
        return url[len(self._base):] if url.startswith(self._base) else url

    def get(self, url, params=None, timeout=None, **kw):
        return _FlaskResp(self._client.get(self._path(url), query_string=params))

    def post(self, url, json=None, data=None, files=None,
             allow_redirects=None, timeout=None, params=None, **kw):
        path = self._path(url)
        if files:
            merged = dict(data or {})
            for key, value in files.items():
                fname, fileobj, _ctype = value
                content = fileobj.read() if hasattr(fileobj, "read") else fileobj
                merged[key] = (io.BytesIO(content), fname)
            return _FlaskResp(
                self._client.post(
                    path, data=merged, content_type="multipart/form-data",
                )
            )
        if json is not None:
            return _FlaskResp(self._client.post(path, json=json))
        return _FlaskResp(
            self._client.post(path, data=data, follow_redirects=False)
        )


class FailAfterNChunks(FlaskSessionAdapter):
    """Adapter that raises on the N-th chunk POST to simulate a card pull.

    Used once per test; after ``raised`` flips True it behaves normally so a
    resume can succeed against the real server.
    """

    def __init__(self, client, *, fail_on_chunk: int, error: Exception) -> None:
        super().__init__(client)
        self._fail_on = fail_on_chunk
        self._error = error
        self._chunk_count = 0
        self.raised = False

    def post(self, url, **kw):
        if "/chunk" in url and not self.raised:
            self._chunk_count += 1
            if self._chunk_count == self._fail_on:
                self.raised = True
                raise self._error
        return super().post(url, **kw)


# --------------------------------------------------------------------------
# Scripted fake session (for deterministic error-path unit tests)
# --------------------------------------------------------------------------

class FakeResp:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeSession:
    """Returns queued responses in order; records every call."""

    def __init__(self) -> None:
        self.queue: List[FakeResp] = []
        self.calls: List[dict] = []

    def queue_resp(self, *responses: FakeResp) -> "FakeSession":
        self.queue.extend(responses)
        return self

    def _next(self, method: str, url: str, **kw) -> FakeResp:
        self.calls.append({"method": method, "url": url, **kw})
        if not self.queue:
            raise AssertionError(f"no queued response for {method} {url}")
        return self.queue.pop(0)

    def get(self, url, **kw):
        return self._next("GET", url, **kw)

    def post(self, url, **kw):
        return self._next("POST", url, **kw)
