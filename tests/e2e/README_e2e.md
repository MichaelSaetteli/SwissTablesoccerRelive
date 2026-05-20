# End-to-End Tests

Diese Tests verifizieren den vollständigen Pipeline-Roundtrip:
**Eingang → Watcher → Merge → Output**. Sie laufen **nicht** in der
GitHub-Actions-CI — sie brauchen einen lebenden FFmpeg-Binary und
echte Filesystem-Operationen, die im Container-Image kein Problem
sind, aber den CI-Job unnötig kostspielig machen würden.

## Wann ausführen

* Vor jedem Release / vor jedem Merge nach `main`
* Nach Änderungen an `pipeline/` oder `watcher/` (Sanity-Check)
* Wenn auf der NAS etwas Merkwürdiges passiert: gleicher Test
  lokal reproduzieren

## Voraussetzungen

* Python 3.9+
* FFmpeg im `$PATH` (Debian/Ubuntu: `apt install ffmpeg`)
* `pytest` aus `requirements-dev.txt`

## Lokales Ausführen (vom Repo-Root)

```bash
pytest tests/e2e/ -v -s
```

Die Tests benutzen `tmp_path`-Fixtures, schreiben also nichts ausserhalb
des `/tmp/pytest-*`-Workspace. Aufräumen passiert automatisch.

## Ausführen auf der NAS

Die E2E-Tests laufen auch direkt im laufenden Container, gegen den
echten Filesystem-Mount:

```bash
# Im DSM Aufgabenplaner als root:
docker compose exec video-pipeline pytest tests/e2e/ -v -s \
    > /volume1/SDD/install_logs/e2e_$(date +%Y%m%d-%H%M%S).log 2>&1
```

Die Tests legen ihre eigenen Temp-Verzeichnisse unter `/tmp/` im
Container an — keine Manipulation der produktiven `eingang_*`,
`work_*`, `output_*`-Ordner.

## Fixture

`fixtures/sample_clip.mp4` ist ein 3-Sekunden, 640×360, H.264-Clip
(~17 KB). Generiert mit:

```bash
ffmpeg -f lavfi -i "color=c=blue:size=640x360:duration=3:rate=15" \
    -f lavfi -i "sine=frequency=440:duration=3" \
    -c:v libx264 -preset ultrafast -crf 35 -pix_fmt yuv420p \
    -c:a aac -b:a 32k \
    sample_clip.mp4
```

Falls die Datei korrupt wird: einfach mit dem obigen Befehl neu erzeugen.

## Test-Inhalt

| Datei | Was wird getestet |
|---|---|
| `test_roundtrip_doppel.py` | Pipeline-Run für Doppel: Eingang → Output mit korrektem Filename |
| `test_roundtrip_einzel.py` | Pipeline-Run für Einzel: gleiches Spiel, andere Disziplin |
| `test_youtube_mock.py` | YouTube-Upload-Flow mit gefälschtem `service`-Objekt (kein echter Upload) |

Die ersten beiden Tests benutzen **echtes FFmpeg** — sie können also
echte Bugs in unserer Concat-Demuxer-Logik fangen, im Gegensatz zu den
Unit-Tests die FFmpeg per `FakeRunner` mocken.

`test_youtube_mock.py` benutzt absichtlich keinen echten YouTube-Service
(das wäre Quota-verschwendend und non-reproduzierbar). Stattdessen ein
Fake-Service-Objekt, das den Aufruf-Pfad verifiziert.
