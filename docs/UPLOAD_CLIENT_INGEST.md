# STS-Upload — Einlese-Modell (Ingest)

Design + Begruendung des Einlese-Workflows des Operator-Tools (Issue #15).
Beschlossen mit dem Operator am **2026-06-02** nach einem realen Durchgang;
revidiert das urspruengliche marker-basierte Modell aus
[`SESSION_HANDOFF.md`](SESSION_HANDOFF.md) §6a.

## Warum die Aenderung

Das urspruengliche Modell verlangte, dass der Admin **jede Karte vor dem
Turnier** mit einer `.sts-card.json`-Markierung beschriftet (Tisch +
Disziplin + Turnier). Der reale Ablauf des Operators ist anders:

* Karten werden **nach** dem Turnier verarbeitet, ein Schwung pro Disziplin
  (bis zu ~24 Einzel + ~24 Doppel), bis zu 20 gleichzeitig im Reader.
* Es ist vorgekommen, dass Einzel-Karten in Doppel-Kameras (und umgekehrt)
  verwendet wurden — der Operator muss die **Disziplin uebersteuern** koennen.
* Auf den Karten liegen manchmal **alte Aufnahmen** aus frueheren Events.
* **Kamera-Uhren stimmen absolut oft nicht** — ein Datum gegen das
  Turnierdatum zu pruefen ist wertlos.

## Quellen der Wahrheit (neu)

| Information | Quelle |
|---|---|
| **Tisch** (`ET01`…`ET99`) | Ziffern im **Windows-Datentraegernamen** der Karte (`E01` → `ET01`). Karten-intern, unabhaengig vom Laufwerksbuchstaben (`E:` vs `G:`). |
| **Disziplin** | **Operator-Batch-Auswahl** (Einzel/Doppel), **pro Karte korrigierbar**. Der Anfangsbuchstabe (`E`/`D`) ist nur ein Vorschlag. |
| **Turnier** | Server, `/api/upload/active-tournament` — das aktive Turnier der gewaehlten Disziplin. |
| **Karten-Identitaet** (Resume) | Volume-**Seriennummer** → sonst Name → sonst Pfad. Disziplin-unabhaengig, damit eine Karte ihre Identitaet (und Zeile) behaelt, wenn die Disziplin korrigiert wird. |

Ein `.sts-card.json`-Marker ist **nicht mehr noetig**. Liegt einer vor,
wird er ignoriert (das Volume-Label gewinnt). `scripts/prepare_card.py`
bleibt als Legacy bestehen, ist aber fuer den neuen Ablauf ueberfluessig.

## Was hochgeladen wird

Nur **Videodateien (`.mp4`)** aus den `DCIM/<sub>`-Unterordnern.
Kamera-Housekeeping (`BACKUP.HST`, `BACKUP.TMP`, `INDEX.DAT`, …) wird
**nicht** hochgeladen und **nicht** fuer die Datumsanalyse betrachtet.
Weitere Containerformate (`.mov`, `.mts`) koennen bei Bedarf in die
Whitelist (`upload_client/manifest.py::VIDEO_SUFFIXES`) aufgenommen werden.

Die Zusammenfuehrung bleibt unveraendert: alle `.mp4` eines Tisches werden
flach in einen `ETxx`-Ordner gezogen und in Walk-Reihenfolge gemerged
(die MP4-Namen sind alphabetisch korrekt sortierbar).

## DCIM-Datums-Check (Alt-Daten-Schutz)

Da absolute Datumswerte unzuverlaessig sind, zaehlt nur der **relative
Abstand** der DCIM-Unterordner:

* Pro Unterordner das **Erstelldatum** (unter Windows zuverlaessig) lesen
  und anzeigen.
* Unterordner nach Datum sortieren und in Cluster gruppieren. Ein neuer
  Cluster beginnt, wenn der Abstand **mehr als 3 Tage** betraegt.
* **Mehr als ein Cluster → ⚠ Alarm** (vermutlich alte Aufnahme dabei).
* **Default-Auswahl**: nur der **neueste Cluster** ist aktiv, aeltere aus.
  Der Operator kann die Auswahl pro Karte aendern; hochgeladen werden nur
  `.mp4` aus den ausgewaehlten Unterordnern.

Beispiel: `227XDPHH` (22.05.2026 17:31) + `228XDPHH` (22.05.2026 17:32) →
1 Minute Abstand → ein Cluster, kein Alarm, beide Ordner werden eingelesen.

## Module (Umsetzung, alles client-seitig)

| Modul | Aufgabe |
|---|---|
| `upload_client/card_identity.py` | Tisch aus Label, Disziplin-Hinweis, stabile Karten-ID. |
| `upload_client/mounts.py` | Volume-Name + Seriennummer (Win32 `GetVolumeInformationW`, POSIX-Fallback = Mount-Verzeichnisname), injizierbar. |
| `upload_client/dcim.py` | DCIM-Unterordner mit Erstelldatum, Clustering, Alarm, Default-Auswahl. |
| `upload_client/manifest.py` | `.mp4`-Whitelist, DCIM-Unterordner-Auswahl, mtime-Erfassung. |
| `upload_client/card_scanner.py` | Baut pro Karte einen **synthetischen `CardMarker`** aus Label + Disziplin + aktivem Turnier; Status `ready`/`empty`/`no_table`/`no_tournament`. |
| `upload_client/ui/app.py` | `IngestState` (Batch-Disziplin + Overrides), reicht es an `scan_mounts`. |
| `upload_client/ui/main_window.py` | Batch-Disziplin-Auswahl (re-scant), Datum-Spalte + Alarm-Markierung. |

Der **Server-Vertrag ist unveraendert**: `start_upload` akzeptiert
`discipline`, `table_name`, `card_uuid` bereits als Parameter — der
synthetische Marker liefert dieselben Felder wie zuvor ein echter Marker.

## Status / offene Punkte

Umgesetzt + getestet (headless + offscreen-GUI):

* Tisch aus Label, Karten-Identitaet, Disziplin-Hinweis.
* DCIM-Clustering + Alarm + Default-Auswahl (neuester Cluster).
* `.mp4`-Whitelist + Unterordner-Auswahl im Manifest.
* Scanner mit synthetischem Marker; Batch-Disziplin-Auswahl in der GUI;
  Datum-Spalte + Alarm-Anzeige.
* **Per-Karte-Bedienelemente**: Disziplin-Dropdown pro Zeile (nur vor
  Upload aenderbar) und DCIM-Auswahl-Dialog per Doppelklick auf die Zeile
  (`ui/dcim_dialog.py`). Eine Korrektur aktualisiert nur noch-nicht-
  hochgeladene Karten (`UploadManager.update_pending`); laufende/fertige
  bleiben unangetastet.

Noch offen:

* **Visueller/funktionaler Check auf Windows** (GUI bisher nur offscreen
  getestet) inkl. echtem Auslesen von Volume-Name + Seriennummer.
