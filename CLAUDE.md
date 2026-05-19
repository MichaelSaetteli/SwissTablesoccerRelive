# CLAUDE.md — SwissTablesoccerRelive

## Projektübersicht

Swiss Tischfussball Relive-Plattform: halbautomatische Pipeline für
Tischfussball-Turnier-Aufnahmen. 1-2 TB Rohvideo (Doppel + Einzel) werden
auf einer Synology DS1522+ per Stream-Copy concateniert und über die
YouTube Data API in die Swisstablesoccer-Playlists hochgeladen.

Vollständige Architektur-Doku: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Fachliches Briefing: [`PROJEKT_BRIEFING.md`](PROJEKT_BRIEFING.md).

## Tech Stack

* **Python 3.9+** (auf der NAS 3.9, im Container 3.11-slim)
* **Flask 3** + **Waitress** WSGI-Server (Web-UI auf Port 5000 im Container)
* **watchdog** für Folder-Watcher (event-driven Quiescence-Detection)
* **FFmpeg** für Stream-Copy concat (kein Re-Encoding)
* **google-api-python-client** + **google-auth-oauthlib** für YouTube
* **pytest** für Tests (158 Unit-Tests + 5 E2E-Tests, alle ohne externe Abhängigkeiten)
* **Docker** + **Container Manager** auf Synology DSM 7.2+
* Vanilla JS für das Frontend (kein Framework, ein einziger Poll alle 3 s)

## Coding-Konventionen

* Sprache im Code: **Englisch** (Variablen, Funktionen, Kommentare, Tests)
* Sprache in der UI: **Deutsch**, Schweizer-freundlich (kein `ß`, immer `ss`)
* Python-Einrückung: **4 Spaces** (PEP 8)
* JS/HTML/CSS-Einrückung: 2 Spaces
* Python-Dateinamen: **snake_case** (z.B. `merge_ffmpeg.py`)
  - Ausnahme: `MoveFiles.py` (Briefing-vorgegeben)
* JS/CSS/HTML-Dateinamen: kebab-case
* Type-Hints überall, `from __future__ import annotations` für 3.9-Kompat
* Atomare File-Writes: temp file + `os.replace`, fsync nur bei terminalen Wechseln

## Projektstruktur

```
pipeline/       FFmpeg-Engine + atomare File-Operationen
watcher/        Folder-Watcher + Pipeline-Runner + Status-Persistenz
web/            Flask-Web-Interface (Login, 2 Tabs, Status, Forms, Download)
youtube/        YouTube Data API integration + OAuth 2.0
scripts/        Ops-Tools: bench_io.sh (Hardware-Benchmark)
tests/          158 Unit-Tests (pytest, kein Internet, kein FFmpeg)
tests/e2e/      5 End-to-End-Tests (echtes FFmpeg, fake YouTube-Service)
docs/           ARCHITECTURE.md, YOUTUBE_SETUP.md, TEST_STATUS.md
config/         Beispiel-Configs (config_doppel.json, config_einzel.json)
.github/        GitHub Actions CI (pytest auf 3.9 + 3.11)
```

## Wichtige Befehle

```bash
# Tests lokal (kein Docker, kein FFmpeg in CI nötig)
pytest                                 # 158 Unit-Tests, ~2 s
pytest tests/e2e/                      # 5 E2E-Tests, brauchen FFmpeg

# Dev-Server (lokal, ohne Docker)
WEB_PASSWORD=dev python -m web.app     # http://127.0.0.1:5000/

# Bench-Skript auf der NAS
WORK_DIR=/volume1/SDD/bench bash scripts/bench_io.sh

# Container neu bauen + starten (auf der NAS)
docker compose build && docker compose up -d
docker compose logs -f video-pipeline

# Code-Qualität-Smoke (auch in CI)
python -c "import pipeline.config_loader, watcher.folder_watcher, web.app, youtube.youtube_uploader"
```

## Hinweise für Claude (und für Menschen, die hier reinkommen)

* Beim Schreiben von UI-Texten: kein `ß`, stattdessen `ss`
* Commits + PR-Beschreibungen auf Englisch (Code-Sprache)
* Vor `git commit`: `pytest` muss grün sein
* `WEB_PASSWORD`, `WEB_SECRET_KEY` und `youtube_token.json` **niemals**
  ins Git einchecken — sind alle in `.gitignore` blockiert
* Status-Files (`status_*.json`, `upload_status_*.json`) sind Runtime-
  Artefakte und ebenfalls in `.gitignore`
* `from __future__ import annotations` in jeder Datei mit Type-Hints
  (sonst bricht 3.9-Kompatibilität)
* Privacy-Default für YouTube-Uploads: **immer `private`** — Operator
  schaltet manuell auf öffentlich im YouTube-Studio

## Constraints (NAS-spezifisch)

| Constraint | Detail |
|---|---|
| Port 5000+5001 blockiert | DSM-intern → Host nutzt **8080** |
| Container als root | uid-Mismatch mit Synology Shared Folders |
| 10'000 Units/Tag YouTube | ~6 Uploads/Tag mit Standard-Quota |
| Stream-Copy only | `ffmpeg -c copy`, kein Re-Encoding |
| 1-2 TB pro Turnier | I/O-bound → SSD für work_*, HDD für Archiv |

## Status-Überblick (Implementierungs-Roadmap)

Siehe Abschnitt 8 in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Kurz: Pipeline + Watcher + Web + YouTube-Modul sind implementiert
(13 Commits ahead of `main`). Offen: Dashboard mit Performance-Metriken
und Archivierungs-Flow (Auftrag 4 + 5 im Operator-Briefing).
