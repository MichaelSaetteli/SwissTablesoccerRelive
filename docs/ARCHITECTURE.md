# SwissTablesoccerRelive — Architecture

Last updated: 2026-05-19, branch `claude/read-briefing-start-build-2wOz7`.

This document is the source of truth for what currently exists in the
code and what is still on the roadmap. If something is documented here
that does not match the code, the **code is wrong** — open an issue.

---

## 1. High-level data flow

```
                       Aufnahme-Laptop (1-4 Geraete)
                       +-------------------------+
                       | 4-6 SD-Karten kopieren  |
                       | + ET01..ET18 Ordner     |
                       +-----------+-------------+
                                   |
                       SMB ueber 10GbE (1-2 TB / Turnier)
                                   v
+--------------------------------------------------------------------+
| DS1522+ Docker-Container "video-pipeline"                          |
|                                                                    |
|  /volume1/SDD/eingang_doppel/ETxx/    /volume1/SDD/eingang_einzel/ |
|        |                                                           |
|        v  watcher.folder_watcher (watchdog Observer, Quiescence    |
|        v  via single-shot Timer auf quiet_seconds nach letztem     |
|        v  Event)                                                   |
|        v                                                           |
|  watcher.pipeline_runner.run_pipeline(config):                     |
|    1. detect_folders(eingang)                                      |
|    2. check_disk_space(folders, output)   <- abort vor Move        |
|    3. _step_move(folders -> work)         <- atomarer os.rename    |
|    4. organize_folders.organize_root(work, max=24)                 |
|    5. rename_mp4.rename_root(work)                                 |
|    6. merge_ffmpeg.merge_all(folders, config)                      |
|       - ThreadPoolExecutor(max_workers)                            |
|       - ffmpeg -f concat -c copy ".name.partial" -> rename         |
|       - per-folder stderr -> /logs/ffmpeg_*_<ts>.log               |
|                                                                    |
|  /volume1/SDD/work_<disziplin>/      <- temporaer waehrend Run     |
|  /volume1/SDD/output_<disziplin>/    <- fertige Match-Videos       |
|                                                                    |
|  Status: /volume1/SDD/video-pipeline-config/status_<disziplin>.json|
|          (atomar geschrieben, fsync nur bei terminalen Wechseln)   |
|                                                                    |
|  Web (Flask + Waitress, Port 5000 im Container, 8080 am Host):     |
|    - /login                          public                        |
|    - / (HTML)                        login required                |
|    - /api/state/<discipline>         combined pipeline+upload+files|
|    - /api/run/<discipline>           POST -> kick off run          |
|    - /api/upload-preview/<d>         titles + quota hint           |
|    - /api/upload/<discipline>        POST -> kick off YouTube      |
|    - /api/filename-config/<d>        GET/POST persisted constants  |
|    - /api/youtube-config/<d>         GET/POST persisted YT config  |
|    - /download/<d>/<file>            single mp4                    |
|    - /download/<d>/all.zip           ZIP of every output file      |
|                                                                    |
|  YouTube-Upload (Option C - halbautomatisch):                      |
|    - metadata_builder    Title/Description aus Templates           |
|    - oauth_setup         Token laden + refresh                     |
|    - youtube_uploader    resumable upload + playlist anlegen       |
+--------------------------------------------------------------------+
        |
        |  YouTube Data API v3 (10'000 units/Tag, 1'600 / Upload)
        v
+--------------------------------------------------------------------+
| youtube.com - Playlists "STS <Turnier> Doppel" / "Einzel"          |
+--------------------------------------------------------------------+
```

---

## 2. Code layout

| Paket | Zweck | Wichtigste Files |
|---|---|---|
| `pipeline/` | FFmpeg-Engine (reine I/O + subprocess) | `MoveFiles.py`, `organize_folders.py`, `rename_mp4.py`, `merge_ffmpeg.py`, `config_loader.py`, `status_file.py` |
| `watcher/` | watchdog Observer + Orchestrator | `folder_watcher.py`, `pipeline_runner.py`, `status.py` |
| `web/` | Flask Web-Interface | `app.py` (factory + routes), `services.py`, `auth.py`, `templates/`, `static/` |
| `youtube/` | YouTube Data API integration | `metadata_builder.py`, `oauth_setup.py`, `upload_status.py`, `youtube_uploader.py` |
| `scripts/` | Ops helpers | `bench_io.sh` |
| `tests/` | pytest, ohne externe Dependencies | 16 Files, 158 Tests |

---

## 3. Web-Endpoints

**Alle `/api/*` und `/download/*` Routen erfordern den `<discipline>` Pfad-Parameter** — Aufrufe ohne ihn (z.B. `GET /api/state`) liefern 404. Das ist der Grund warum „API-Endpoints `/api/*` antworten alle mit 404" wirkt: ohne `<discipline>` matched keine Route.

`<discipline>` ist immer entweder `Doppel` oder `Einzel` (case-sensitive). Andere Werte → 404 `{"error":"unknown discipline"}`.

### Auth + UI

| Methode | Pfad | Auth | Antwort | Zweck |
|---|---|---|---|---|
| GET | `/login` | public | 200 HTML | Login-Formular |
| POST | `/login` | public | 302 → `/` oder 200 mit Fehlermeldung | Authentifizierung (form: `username`, `password`) |
| GET | `/logout` | login | 302 → `/login` | Session löschen |
| GET | `/` | login | 200 HTML | Hauptseite mit Tabs für Doppel + Einzel |
| GET | `/favicon.ico` | public | 204 | Stub (kein Icon mitgeliefert) |

### API (JSON)

| Methode | Pfad | Auth | Antwort | Zweck |
|---|---|---|---|---|
| GET | `/api/state/<discipline>` | login | 200 `{pipeline, upload, files}` | Combined Status-Poll (UI-Polling alle 3s) |
| POST | `/api/run/<discipline>` | login | 202 `{status, discipline}` / 409 | Pipeline manuell auslösen |
| GET | `/api/upload-preview/<discipline>` | login | 200 `{files[], quota_hint, total}` | Vorschau Titel + YouTube-Quota |
| POST | `/api/upload/<discipline>` | login | 202 `{status, discipline}` / 409 | Upload-Batch starten |
| GET | `/api/filename-config/<discipline>` | login | 200 `{jahr, sts_nummer, turniername, disziplin, part}` | Aktuelle Konstanten |
| POST | `/api/filename-config/<discipline>` | login | 200 (aktualisierte Werte) | Konstanten persistieren |
| GET | `/api/youtube-config/<discipline>` | login | 200 (YouTube-Metadaten) | Aktueller YT-Config-Block |
| POST | `/api/youtube-config/<discipline>` | login | 200 (aktualisierte Werte) | YT-Config persistieren |

### Downloads

| Methode | Pfad | Auth | Antwort | Zweck |
|---|---|---|---|---|
| GET | `/download/<discipline>/<filename>` | login | 200 (file stream) / 404 | Einzelne Output-Datei |
| GET | `/download/<discipline>/all.zip` | login | 200 (ZIP stream) / 404 | Alle Output-Dateien als ZIP |

Path-Traversal ist auf Service-Ebene blockiert (`services.resolve_output_file`); `../` → 404.

### Auth-Verhalten

`auth.login_required` unterscheidet:

- Browser-Anfragen (`/`, `/login`, …) → 302 Redirect nach `/login`
- API + Download (`/api/*`, `/download/*`) → 401 Status

---

## 4. Pipeline-Phasen

Jeder Run für eine Disziplin durchläuft fünf Phasen. Die State-Machine ist in `watcher.status.State`.

| State | Was passiert | Input | Output |
|---|---|---|---|
| `idle` | Watcher aktiv, kein Run läuft | — | — |
| `detecting` | Quiescence-Trigger ausgelöst, Folder-Scan | `eingang/ETxx/` | Liste der erkannten Ordner |
| `moving` | `move_path` von eingang → work | `eingang/ETxx/` | `work/ETxx/` |
| `organizing` | Ordner mit >24 MP4s aufsplitten | `work/ETxx/` mit n>24 Files | `work/ETxx_1/`, `ETxx_2/` à 24 Files |
| `renaming` | MP4s in `video_001.mp4`, `video_002.mp4`, … umbenennen | `work/ETxx/*.mp4` | `work/ETxx/video_NNN.mp4` |
| `merging` | FFmpeg concat (`-f concat -c copy`), parallel | `work/ETxx/video_*.mp4` | `output/<schema>.mp4` |
| `done` | Erfolgreich, alle Files in output/ | — | — |
| `error` | FFmpeg failed oder Disk-Full | — | Fehlertext + Log-Pfad |

**Pre-flight vor Move**: `check_disk_space(folders, output)` — verweigert den Run wenn `output_dir` nicht ≥ 1.05 × Σ Input-Bytes frei hat. Inputs bleiben dann unangetastet in `eingang/`.

**Output-Filename-Schema** (siehe `pipeline.config_loader.build_output_filename`):

```
{jahr} {sts_nummer} {tischnummer} {turniername} {disziplin} [{part}].mp4
2026   STS2          T01           Seetal        Doppel       Part 1
```

- `{tischnummer}` wird aus dem Ordnernamen extrahiert: `ET01` → `T01`
- Bei Splits (>24 MP4s) überschreibt das automatische `Part 1`, `Part 2`, … das konfigurierte `part`-Feld
- Leere Konstanten werden gefiltert (kein doppeltes Whitespace)

**Atomare Output-Writes**: FFmpeg schreibt nach `.<final-name>.partial`, erst nach `returncode==0` wird per `os.replace` umbenannt. Crash mitten im Merge → kein korrupter `final-name.mp4` in `output/`.

**Per-Folder FFmpeg-Log**: `<logs>/ffmpeg_<discipline>_<folder>_<ts>.log` enthält die volle stderr-Ausgabe inklusive aufgerufenem Kommando. Auf der NAS: `/volume3/HDD11TB/pipeline_logs/`.

---

## 5. Folder-Watcher (event-getrieben)

`watcher.folder_watcher.FolderWatcher`:

1. `watchdog.Observer` registriert `EingangHandler` auf `eingang/<discipline>/`.
2. Jedes Filesystem-Event (Create/Modify/Move) triggert `_on_event()`.
3. `_on_event()` cancelt einen offenen `threading.Timer` und scheduled einen neuen für `quiet_seconds` Sekunden (Default 10 s).
4. Wenn der Timer abläuft (= keine neuen Events für `quiet_seconds`), startet er `check_and_trigger()` in einem eigenen Daemon-Thread.
5. `check_and_trigger()` ruft `pipeline_runner.run_pipeline(config)` auf.
6. Bei `PipelineRunError` (z.B. "already running") wird der Fehler im Status-Log notiert und der Trigger ignoriert.

**Vorteile gegenüber Polling**: 0 % Idle-CPU, sofortige Reaktion ohne Polling-Latenz. **Test-Hook**: `check_and_trigger` ist `public`, Tests rufen es ohne watchdog auf.

---

## 6. YouTube-Upload (Option C — halbautomatisch)

```
output/*.mp4
   |
   v  metadata_builder.build_upload_batch(config, files)
   v    - safe_format Placeholders: {turniername} {disziplin} {datum}
   v      {ort} {kamera} {nummer}
   v    - 1..N VideoMetadata Objekte
   |
   v  Web-UI zeigt Vorschau (Titel + Quota-Hint: 1'600 Units / Upload,
   |  10'000 Units / Tag = max. ~6 Videos)
   |
   v  Operator klickt "Upload starten" -> POST /api/upload/<discipline>
   |
   v  youtube_uploader.upload_batch(service, config, writer):
   v    1. _resolve_playlist  (neu anlegen oder bestehende ID)
   v    2. fuer jede Datei:
   v       - upload_video (resumable, progress_callback)
   v       - add_to_playlist (falls Playlist gesetzt)
   v    3. UploadStatusWriter.finish() bzw. fail()
   |
   v  YouTube Data API v3 -> Playlist + Videos (alle privat by default)
```

**OAuth-Flow** (in `youtube.oauth_setup`):

- **Setup-Phase (einmalig)**: `run_setup_flow(client_secrets, token_path)` öffnet einen lokalen Browser. Da die DS1522+ keinen Browser hat, **muss dieser Schritt auf einem Laptop ausgeführt werden**. Das resultierende `youtube_token.json` wird per `scp` auf die NAS in den Config-Dir kopiert.
- **Runtime**: `load_credentials(token_path)` liest das Token; abgelaufene Access-Tokens werden automatisch via Refresh-Token erneuert und das aktualisierte JSON wird zurückgeschrieben (atomar).

**API Scopes** (siehe `youtube.oauth_setup.SCOPES`):

```python
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",  # Video hochladen
    "https://www.googleapis.com/auth/youtube",          # Playlists verwalten
]
```

**Privacy-Default**: alle Uploads gehen mit `privacyStatus: "private"` ins YouTube Studio. Der Operator gibt sie dort manuell frei. Das schützt vor versehentlichen Live-Veröffentlichungen.

---

## 7. Persistenz-Layout (auf der NAS)

```
/volume1/SDD/
├── projects/SwissTablesoccerRelive/      Git-Repo
├── video-pipeline-config/
│   ├── config_doppel.json                  Pfade + Konstanten + YT-Config
│   ├── config_einzel.json
│   ├── status_doppel.json                  PipelineStatus (atomar)
│   ├── status_einzel.json
│   ├── upload_status_doppel.json           UploadStatus (atomar)
│   ├── upload_status_einzel.json
│   └── youtube_token.json                  OAuth-Token (NICHT im Git!)
├── eingang_doppel/                       SMB-Upload-Ziel
├── eingang_einzel/
├── work_doppel/                          Pipeline-Zwischenstand
├── work_einzel/
├── output_doppel/                        Fertige Match-Videos
└── output_einzel/

/volume3/HDD11TB/pipeline_logs/           FFmpeg-Stderr-Logs (per-merge)

/volume2/HDD12TB/archiv/                  Langzeit-Archiv nach YouTube-Upload
└── YYYY-MM-Turniername/                    (Auftrag 5, noch nicht implementiert)
    ├── eingang_doppel/
    ├── output_doppel/
    ├── eingang_einzel/
    ├── output_einzel/
    └── archiv_meta.json
```

Wichtig: Die `status_*.json` und `upload_status_*.json` liegen **neben** der Config (`<source_path>.parent`). Der pfad ergibt sich aus `VIDEO_PIPELINE_DATA_DIR`.

---

## 8. Implementierungs-Status

| Bereich | Status |
|---|---|
| Pipeline-Engine (Move/Organize/Rename/Merge) | ✅ vollständig + 158 Tests grün |
| Folder-Watcher (event-driven, Timer-basiert) | ✅ |
| Pipeline-Runner (Lock, State-Machine, atomare Schritte) | ✅ |
| Disk-Space-Preflight | ✅ |
| Atomare Output-Writes (`.partial` → rename) | ✅ |
| Per-Folder FFmpeg-Logs | ✅ |
| Web-UI: Status-Poll, Konstanten-Form, YT-Config-Form, Download | ✅ |
| Web-UI: Upload-Vorschau + Trigger + Progress | ✅ |
| YouTube-Modul: metadata_builder, uploader, oauth_setup | ✅ Code da, OAuth-Token-Datei fehlt noch |
| Bench-Skript (`scripts/bench_io.sh`) | ✅ |
| Docker (Dockerfile, docker-compose.yml, INSTALL.md) | ✅ produktiv auf NAS |
| YouTube-OAuth-Token | ⏳ Operator-Schritt offen (siehe `docs/YOUTUBE_SETUP.md`) |
| Turnier-Dashboard mit Performance-Metriken | ❌ Auftrag 4, noch nicht designed |
| Archivierung nach YouTube-Upload (rsync + Checksumme) | ❌ Auftrag 5, noch nicht implementiert |
| GitHub Actions CI | ❌ Auftrag 3c (kommt jetzt) |
| E2E-Test-Framework | ❌ Auftrag 3d (kommt jetzt) |

---

## 9. Wichtige Constraints

| Constraint | Detail |
|---|---|
| **Port 5000+5001 belegt** | DSM blockt diese auf der DS1522+ → Host nutzt 8080, im Container weiter 5000 |
| **Container als root** | uid-Mismatch zwischen Docker-User (1000) und Synology-Shared-Folder-Ownership |
| **Kein Auto-Upload** | YouTube-Upload nur nach manuellem Klick auf "Upload starten" |
| **Privacy default private** | Alle Uploads sind im YT Studio sichtbar, Operator gibt manuell frei |
| **Stream-Copy** | FFmpeg `-c copy` — kein Re-Encoding, I/O-bound, CPU-minimal |
| **Per-Disziplin-Locks** | `pipeline_runner._run_locks` und `youtube_uploader._upload_locks` — Doppel und Einzel laufen parallel, gleicher Run zweimal lösen `*Error("already running")` aus |
| **`from __future__ import annotations`** | Type-Hints sind lazy strings → Python 3.9+ läuft auch ohne Docker |

---

## 10. Wichtige Befehle

```bash
# Tests
pytest                                  # 158 Tests, ~2 s, keine externen Deps

# Lokaler Dev-Server (kein Docker)
WEB_PASSWORD=dev python -m web.app      # http://127.0.0.1:5000/

# Bench auf der NAS
WORK_DIR=/volume1/SDD/bench bash scripts/bench_io.sh

# Container neu bauen + starten (auf der NAS)
docker compose build && docker compose up -d
docker compose logs -f video-pipeline   # live tail
```
