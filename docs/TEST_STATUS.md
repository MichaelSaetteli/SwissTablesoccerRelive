# Test-Status

Stand: 2026-05-19, Branch `claude/read-briefing-start-build-2wOz7`,
163 Tests, alle grün. Full log in `install_logs/pytest_initial.log`.

---

## Übersicht

| Test-Bucket | Files | Tests | Status |
|---|---|---|---|
| Pipeline (FFmpeg, Move, Organize, Rename, Filename) | 5 | 53 | ✅ |
| Watcher (Status, Pipeline-Runner, Folder-Watcher) | 3 | 25 | ✅ |
| YouTube (Metadata-Builder, Upload-Status, Uploader) | 3 | 25 | ✅ |
| Web (Flask Test-Client) | 1 | 34 | ✅ |
| Optimierungen + App-Main | 2 | 17 | ✅ |
| Save-Config-Round-Trip | 1 | 4 | ✅ |
| **End-to-End (echtes FFmpeg)** | 3 | 5 | ✅ |
| **Total** | **18** | **163** | **alle grün** |

---

## Was Auftrag 3a gefunden hat

**Initiale Baseline (vor Auftrag 3):** 158 Tests, alle grün — keine
kaputten Tests in `tests/`. Auftrag 3a hatte sich damit erübrigt; es
gab nichts zu reparieren.

**Aber:** das E2E-Test-Framework aus Auftrag 3d hat einen echten Bug
in der Atomic-Output-Logik aufgedeckt (Commit `f6f97db`). FFmpeg konnte
das Ausgabeformat nicht ableiten weil die Partial-Datei mit `.partial`
statt `.mp4` endet:

```
Unable to choose an output format for '.../E2eTest Doppel.mp4.partial';
use a standard extension for the filename or specify the format manually.
```

**Fix** (siehe `pipeline/merge_ffmpeg.py:merge_folder`):
* Partial-Filename ist jetzt `.<stem>.partial.mp4` (statt `.<name>.partial`)
* Hidden-Dot-Prefix bleibt, `.mp4`-Suffix erlaubt FFmpeg-Auto-Detection
* Unit-Test entsprechend angepasst (`test_merge_ffmpeg.py`)

Dieser Bug wäre in Produktion erst beim ersten echten Pipeline-Run
aufgefallen (1-2 TB Daten investieren, dann Fehler). Genau das was
E2E-Tests einfangen sollen.

---

## Test-Strategie (was läuft wo)

| Test-Typ | Was getestet | Externe Abhängigkeiten | CI? |
|---|---|---|---|
| Unit (`tests/test_*.py`) | Module einzeln, FakeRunner für FFmpeg, FakeYouTubeService | keine | ✅ ja, bei jedem Push |
| E2E (`tests/e2e/test_*.py`) | Volle Pipeline + Upload-Flow | echter FFmpeg, ~17 KB Fixture-MP4 | ❌ manuell (lokal oder NAS) |
| Smoke-Import | Jedes Modul importierbar | keine | ✅ separater Job |
| docker-compose validate | Syntax + Schema | docker | ✅ separater Job |
| Shellcheck `bench_io.sh` | Warnings only | shellcheck | ✅ separater Job |

E2E-Tests laufen **nicht in CI** weil:
1. Sie brauchen FFmpeg im PATH (in CI installierbar, ja, aber langsam)
2. Sie testen echte Datei-Operationen, kein Code-Pfad-Issue
3. Sie sollen *vor jedem Release* manuell laufen, nicht bei jedem Branch-Push

---

## Tests pro Modul

### `pipeline/` — Engine

| File | Tests | Was abgedeckt ist |
|---|---|---|
| `test_config_loader.py` | 17 | Schema-Validation, Filename-Builder, Split-Folder-Schema (Option B), Edge-Cases |
| `test_movefiles.py` | 5 | File/Dir-Move, atomarer Fast-Path, EXDEV-Fallback |
| `test_organize_folders.py` | 8 | Chunk-Splitting, Erhalt von Reihenfolge, Edge-Cases |
| `test_rename_mp4.py` | 7 | `video_NNN.mp4`-Schema, Idempotenz, Kollisions-Sicherheit (2-Phasen-Rename) |
| `test_merge_ffmpeg.py` | 11 | FFmpeg-Concat, parallel via ThreadPoolExecutor, atomarer `.partial`-Rename, Concat-List-Cleanup |
| `test_save_config.py` | 4 | Round-Trip via `save_config`, atomarer Write, Trailing-Newline |

### `watcher/` — Orchestrator

| File | Tests | Was abgedeckt ist |
|---|---|---|
| `test_status.py` | 12 | `JsonStatusFile` Base + `StatusWriter`, Log-Tail-Cap, Lifecycle (`begin_run`/`finish_run`/`fail_run`) |
| `test_pipeline_runner.py` | 8 | End-to-End-Pipeline mit Mock-FFmpeg, Split-Folder-Handling, Per-Disziplin-Lock, Disk-Full-Abort |
| `test_folder_watcher.py` | 7 | `QuiescenceDetector`-Timer mit `FakeTimer`, `check_and_trigger`, `_on_event`-Reschedule |

### `youtube/` — Upload

| File | Tests | Was abgedeckt ist |
|---|---|---|
| `test_metadata_builder.py` | 12 | Placeholder-Safety (`safe_format`), `extract_kamera`, Context-Building, Title-Truncate |
| `test_upload_status.py` | 8 | `UploadStatusWriter`-Lifecycle, durable=False Optimierung, Progress-Tracking |
| `test_youtube_uploader.py` | 5 | `FakeYouTubeService` mit chained API, Resumable-Upload-Loop, Playlist-Logik, Error-Path |

### `web/` — Flask

`test_web_app.py` (34 Tests):
* Auth-Flow: Login-Redirect, 401 für `/api/*`, 401 für `/download/*`, Logout
* `/api/state/<discipline>` Combined-Poll
* `/api/run/<discipline>` async-Trigger
* `/api/upload/<discipline>` mit injizierten Runner
* `/api/filename-config/<discipline>` GET + POST + Unknown-Key-Rejection
* `/api/youtube-config/<discipline>` GET + POST + Whitespace-Strip
* `/download/<d>/<file>` und `/download/<d>/all.zip` (ZIP-Stream)
* Path-Traversal-Protection (`../secret.mp4` → 404)
* Disabled-Discipline → 409
* Unknown-Discipline → 404

### `tests/e2e/` — End-to-End

| File | Tests | Was abgedeckt ist |
|---|---|---|
| `test_roundtrip_doppel.py` | 2 | Echter FFmpeg-Concat (3 Clips → ~9 s), Split-Folder mit 26 Clips → 2 Part-Outputs |
| `test_roundtrip_einzel.py` | 2 | Selbes für Einzel + Isolation-Check (Doppel-Outputs gehen nicht in Einzel-Output-Dir) |
| `test_youtube_mock.py` | 1 | Full pipeline run → mocked YouTube-Upload (Playlist-Creation + Video-Insert + Playlist-Items) |

Fixture: `tests/e2e/fixtures/sample_clip.mp4`, 17 KB, 3 s blau-Audio-Pieper.

---

## Coverage (approximativ)

* `pipeline/` ~95 % Line-Coverage (alle Pfade ausser EXDEV-Fallback in CI)
* `watcher/` ~90 % (RECEIVE-Watchdog-Pfad ohne CI-Trigger)
* `youtube/` ~85 % (real-OAuth-Flow nicht in CI)
* `web/` ~90 % (alle Routen, alle Auth-Pfade)

Genaue Werte: `pytest --cov` Output via CI Artefakt `test-results-py3.11`.

---

## Lücken (TODO für Auftrag 3b später)

Was *nicht* getestet ist:

* **Performance-Metriken-Logging** (kommt mit Auftrag 4 — Dashboard)
* **Archivierung + Checksumme** (kommt mit Auftrag 5)
* **Echter YouTube-OAuth-Refresh-Path** (würde Quota verbrauchen, nur manuell)
* **Watchdog-Observer auf echtem Filesystem** (E2E manuell; in CI nicht
  zuverlässig wegen Inotify-Latenz)
* **Concurrent Doppel+Einzel-Pipelines parallel** — wir testen Lock-
  Verhalten, aber nicht echte Zwei-Threads-im-gleichen-Prozess-Last

Diese Lücken sind dokumentiert, nicht kritisch für den Release.
