# Video-Pipeline NAS

Halbautomatische Pipeline fuer Schweizer Tischfussball-Aufnahmen (Doppel +
Einzel) auf einer Synology DS1522+. Ueberwacht zwei SMB-Eingangsordner,
schneidet die MP4s mit FFmpeg zu fertigen Match-Videos zusammen und laedt
sie ueber die YouTube Data API v3 in eine Turnier-Playlist hoch.

## Architektur

```
Aufnahme-Laptop                       Synology DS1522+
 (4-6 SD-Karten)                      (Docker-Container)
       |                                        |
       |  SMB 10GbE  (1-2 TB pro Lauf)          |
       v                                        v
  +--------------------------+         +--------------------------------+
  | /eingang_doppel/ETxx/    | ------> | watchdog erkennt Quiescence   |
  | /eingang_einzel/ETxx/    |         | -> verschiebt nach work_*     |
  +--------------------------+         | -> organize_folders (>24)     |
                                       | -> rename to video_NNN.mp4    |
                                       | -> ffmpeg concat (stream-copy)|
                                       | -> /output_*/                 |
                                       +---------------+----------------+
                                                       |
                                                       v
                                       +--------------------------------+
                                       | Flask Web-Interface (port 5000)|
                                       | - Live-Status pro Disziplin   |
                                       | - YouTube-Vorschau + Upload   |
                                       | - Download als ZIP            |
                                       +--------------------------------+
```

## Module

| Package | Verantwortung |
|---|---|
| `pipeline/` | FFmpeg-Engine (Linux-Port der Windows-v6-Skripte) |
| `watcher/` | watchdog-Folder-Watcher + Pipeline-Runner mit Status-Persistenz |
| `web/` | Flask-Web-Interface (Login, 2 Tabs, Download, Konstanten- + YouTube-Form) |
| `youtube/` | OAuth 2.0, Metadata-Builder, Resumable Upload + Playlist-Verwaltung |

## Deployment

Der Container ist fuer eine Synology DS1522+ (DSM 7.2+) gedacht. Vollstaendige
Installations-Anleitung: siehe **[INSTALL.md](INSTALL.md)**.

Quick-Start:

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

## Lokale Entwicklung

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest                                    # 141+ Tests, alle ohne Google-API
WEB_PASSWORD=dev python -m web.app        # http://127.0.0.1:5000/
```

Die Tests nutzen Fakes fuer FFmpeg, watchdog und die YouTube Data API,
laufen also komplett ohne externe Dienste oder Binaries.

## Filename-Schema

```
{jahr} {sts_nummer} {tischnummer} {turniername} {disziplin} [{part}].mp4
2026   STS2          T01           Seetal        Doppel       (optional)
```

* `tischnummer` wird aus dem Ordnernamen abgeleitet (`ET01` -> `T01`)
* `part` ist optional; bei Splits (>24 MP4s in einem Ordner) wird er
  automatisch durch `Part 1`, `Part 2`, ... ersetzt

## YouTube-Quota

Standard: 10'000 Units/Tag, ein Upload kostet 1'600 Units (ca. 6 Videos/Tag).
Bei Bedarf in der Google-Cloud-Console Quota-Erhoehung beantragen.

## Lizenz

Proprietaer / intern. Bei Fragen: <https://github.com/MichaelSaetteli/SwissTablesoccerRelive/issues>
