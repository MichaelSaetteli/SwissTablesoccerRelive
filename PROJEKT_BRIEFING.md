# Projekt-Briefing: Video-Pipeline NAS-Erweiterung
> Diese Datei ist das vollständige Briefing für Claude Code.
> Alle Architektur-Entscheide sind bereits getroffen. Stelle keine Rückfragen
> zu den hier dokumentierten Entscheidungen – beginne direkt mit der Umsetzung
> gemäss der Build-Reihenfolge am Ende dieses Dokuments.

---

## 1. Ausgangslage – Lokale Pipeline (v6) auf Windows

### Was bereits existiert und funktioniert

Eine vollständige Video-Verarbeitungs-Pipeline auf einem Windows-PC (Laufwerk D:\).
Der Code ist in Python 3.13 geschrieben, die GUI in tkinter.

### Pipeline-Ablauf (Windows, bleibt unverändert)

```
SD-Karten einstecken
        │
        ▼
21_SD-Card-Copy-Tool.py         Robocopy parallel → D:\21_Import-SD-Cards
        │
        ▼
21a_SD-Card-Copy-Move-to-Work.py  → D:\22_Import-SD-Cards-WORK
        │
        ▼
22_SD-Card-WORK-after-copy.py   MP4s aus DCIM extrahieren, Ordner umbenennen
                                 Ergebnis: Ordner ET03, ET04...ET18 mit MP4s
        │
        ▼
22a_SD-Card-Copy-Move-away-from-Work.py → D:\2_OrganizeFolders
        │
        ▼
[AB HIER ÜBERNIMMT DER NAS – Schnittstelle: ET-Ordner mit MP4s]
```

### Wichtig: Was auf dem NAS NICHT mehr existiert

- Kein tkinter (wird durch Flask Web-Interface ersetzt)
- Kein Robocopy (wird durch SMB-Netzlaufwerk-Upload ersetzt)
- Kein pywin32 / wmic / PowerShell
- Kein SD-Karten-Handling (bleibt dauerhaft auf Windows-PC)

### Bestehende Skripte, die auf den NAS portiert werden (Linux-Anpassung nötig)

```
config_loader.py       Zentraler Pfad-/Config-Loader (Pfad-Separator anpassen)
MoveFiles.py           Universelles Move-Skript (python MoveFiles.py <src> <dst>)
2_OrganizeFolders.py   Ordner mit >24 MP4s in Gruppen à 24 aufteilen
3_rename_mp4.py        MP4s umbenennen: video_001.mp4, video_002.mp4...
5_MergeFFmpeg.py       FFmpeg Stream-Copy, parallel via ThreadPoolExecutor
```

### Dateimengen und Performance-Kontext

- **Volumen:** 1–2 TB pro Durchlauf
- **Ordnerstruktur:** ET03, ET04...ET18 (Kamera-IDs)
- **Pro Ordner:** 21–23 MP4-Dateien, max. 24 (OrganizeFolders teilt auf)
- **FFmpeg:** Nur Stream-Copy (`-c copy`), kein Re-Encoding → CPU-Last minimal
- **Parallelität:** 4 Worker (ThreadPoolExecutor)
- **Hardware NAS:** Synology DS1522+, AMD Ryzen R1600, 8 GB ECC RAM
- **Netzwerk:** 10GbE-Karte vorhanden (E10G22-T1-Mini)
- **Upload-Strategie:** SMB-Netzlaufwerk (nicht Browser-Upload) – besser für 1–2 TB

### Dateiname-Logik (FFmpeg Output)

Konfigurierbar über config.json:
```
Konstante1 Konstante2 variableA Konstante4 Konstante5 Konstante6 variableB.mp4
Beispiel:  "2026 STS02 T03 Doppel Part 1.mp4"
```
- **4-stellige Ordner** (z.B. ET03): kürzeres Schema ohne Konstante6/variableB
- **6-stellige Ordner**: volles Schema mit variableB als letztem Zeichen
- Leere Konstanten werden gefiltert (`if str(p).strip()`) → kein doppelter Leerzeichen

---

## 2. Ziel-Architektur NAS

### Gesamtübersicht

```
Laptop A/B/C/D (beliebig, 1-4 Personen)
────────────────────────────────────────
  │  SMB-Netzlaufwerk mounten
  │  \\NAS-IP\eingang_doppel  oder  \\NAS-IP\eingang_einzel
  │  ET-Ordner hineinkopieren (10GbE)
  │
  ▼
DS1522+ – Docker Container: video-pipeline
  │
  ├── Folder-Watcher (watchdog)
  │   erkennt Upload-Abschluss → startet Pipeline automatisch
  │
  ├── Pipeline-Engine (identisch für Doppel und Einzel)
  │   OrganizeFolders → Rename → FFmpeg Merge
  │
  ├── Flask Web-Interface (erreichbar via Synology QuickConnect)
  │   ├── Tab: Doppel
  │   └── Tab: Einzel
  │
  └── YouTube-Modul
      Metadaten konfigurieren → per Klick bestätigen → Upload

Browser (Laptop A/B/C/D)
  │  Synology QuickConnect (kein Portforwarding nötig)
  ├── Status verfolgen
  ├── YouTube-Metadaten ausfüllen & bestätigen
  └── Fertige Videos herunterladen
```

### Ordner-Struktur auf dem NAS (Volume)

```
/volume1/video-pipeline/
├── eingang_doppel/     ← SMB-Freigabe: Upload-Ziel für Laptops
├── eingang_einzel/     ← SMB-Freigabe: Upload-Ziel für Laptops
├── work_doppel/        ← Pipeline läuft hier durch (intern)
├── work_einzel/        ← Pipeline läuft hier durch (intern)
├── output_doppel/      ← Fertige Videos, download-bereit
├── output_einzel/      ← Fertige Videos, download-bereit
├── config_doppel.json  ← Eigene Konstanten, Pfade, YT-Metadaten
├── config_einzel.json  ← Eigene Konstanten, Pfade, YT-Metadaten
└── logs/
```

---

## 3. Architektur-Entscheide (bereits getroffen, nicht mehr diskutieren)

| Thema | Entscheidung | Begründung |
|---|---|---|
| Web-Framework | Flask | Leichtgewichtig, gut auf Synology/Docker |
| Deployment | Docker Container | Einfaches Update, isoliert, Standard auf Synology |
| Folder-Watcher | watchdog (Python) | Bewährt, einfach, kein Polling nötig |
| Upload-Methode | SMB Netzlaufwerk | Besser für 1–2 TB als Browser-Upload |
| Erreichbarkeit | Synology QuickConnect | Kein Portforwarding, Internet-Zugriff möglich |
| Zugriffsschutz | Login/Passwort | Einfacher Schutz im Web-Interface |
| Einzel/Doppel | Gleiche Engine, 2x unabhängige Config | Kein Durcheinander, je eigene Konstanten |
| Gleichzeitigkeit | Beide Pipelines können parallel laufen | Sequenz optional (Doppel zuerst für YT) |
| YouTube-Upload | Option C: Halbautomatisch | NAS bereitet vor, Mensch bestätigt per Klick |
| YouTube-Playlist | Neu anlegen pro Turnier | 2 Playlists: Doppel + Einzel, getrennt |
| Encoding | UTF-8 durchgehend | Wie auf Windows-Version |
| subprocess | subprocess.run(), nie os.system() | Wie auf Windows-Version |
| Logging | Datei offen halten | Wie auf Windows-Version |
| Parallelität FFmpeg | ThreadPoolExecutor, 4 Workers | Wie auf Windows-Version |

### Schnittstelle Windows ↔ NAS

**Eingang NAS = Ausgang Windows-Schritt 22a**
Die Laptops kopieren fertig extrahierte ET-Ordner (ET03, ET04... mit MP4s direkt
drin, ohne DCIM-Unterstruktur) auf das NAS-Netzlaufwerk.
Der NAS führt ab diesem Punkt die restliche Pipeline durch.

---

## 4. Zwei Disziplinen: Doppel und Einzel

### Zeitlicher Kontext

- Doppel wird am **Samstag** gespielt, Einzel am **Sonntag**
- Manchmal werden alle Daten erst **Sonntag Abend gemeinsam** verarbeitet
- Videos müssen eindeutig als Doppel bzw. Einzel erkennbar sein
- YouTube: **getrennte Playlists** pro Disziplin

### Technische Umsetzung

- Gleicher Pipeline-Code, zwei unabhängige Instanzen
- `config_doppel.json`: eigene Konstanten → Dateinamen enthalten "Doppel"
- `config_einzel.json`: eigene Konstanten → Dateinamen enthalten "Einzel"
- Zwei separate Eingangsordner → kein Durcheinander möglich
- Wenn **nur eine Disziplin** vorhanden ist: die andere bleibt deaktiviert
  (konfigurierbar im Web-Interface, kein Fehler)

---

## 5. Web-Interface – Anforderungen

### Allgemein

- **Technologie:** Flask (Python), läuft im Docker Container
- **Erreichbarkeit:** Via Synology QuickConnect (Internet-Zugriff möglich)
- **Zugriffsschutz:** Einfacher Login mit Benutzername + Passwort
- **Clients:** 1–4 Laptops, normale Desktop-Browser

### Struktur: Zwei Tabs (Doppel / Einzel)

Jeder Tab enthält:

1. **Upload-Status**
   - Anzeige: Wartend / Dateien erkannt / Verarbeitung läuft / Fertig / Fehler
   - Liste der erkannten ET-Ordner mit Dateianzahl
   - Fortschrittsbalken pro Pipeline-Schritt

2. **Pipeline-Steuerung**
   - Pipeline manuell starten / stoppen (Fallback, falls Watcher nicht greift)
   - Log-Ausgabe (letzte N Zeilen, live aktualisiert)

3. **YouTube-Konfiguration** (Formular, pro Disziplin unabhängig)
   ```
   Turniername:        [________________]   z.B. "STS Bern 2026"
   Datum:              [________________]   z.B. "17./18. Mai 2026"
   Ort:                [________________]   z.B. "Bern, Schweiz"
   Disziplin:          [Doppel ▾]           auto-gesetzt, überschreibbar
   Titel-Template:     [________________]   Platzhalter: {turniername} {disziplin} {kamera}
   Beschreibungs-      [________________]
   Template:           [________________]   Freitext mit Platzhaltern
   Playlist:           ○ Neu anlegen: [___] ○ Bestehende ID: [___]
   Aktiviert:          [✓] Diese Disziplin ist aktiv
   ```

4. **YouTube-Upload**
   - Vorschau: generierte Titel für alle Videos (überprüfbar vor Upload)
   - Button: "Upload starten" (erst aktiv wenn Pipeline fertig)
   - Upload-Fortschritt pro Video

5. **Download**
   - Liste der fertigen Videos mit Dateigrösse
   - Einzeln oder als ZIP herunterladen

### Technische Details Web-Interface

- Thread-sicheres Logging (Queue + Background-Thread, nicht Flask-Thread blockieren)
- Pipeline-Status wird in JSON-Datei persistiert (überlebt Server-Neustart)
- Live-Updates via Server-Sent Events (SSE) oder Polling alle 3 Sekunden

---

## 6. YouTube-Modul – Anforderungen

### Funktionsweise (Option C: Halbautomatisch)

1. Pipeline läuft durch → Videos in `output_doppel/` oder `output_einzel/`
2. Web-Interface zeigt Benachrichtigung: "Videos bereit"
3. User füllt YouTube-Metadaten-Formular aus (oder sind bereits vorausgefüllt)
4. Vorschau der generierten Titel überprüfen
5. Klick auf "Upload starten" → NAS lädt direkt via YouTube Data API v3 hoch
6. Upload-Fortschritt sichtbar im Web-Interface

### YouTube Data API v3

- **Authentifizierung:** Google OAuth 2.0 (einmalige Einrichtung, Token wird gespeichert)
- **Operationen:** Video hochladen, Playlist anlegen, Video zu Playlist hinzufügen
- **Einschränkung:** YouTube Standard-Quota = 10'000 Units/Tag
  (1 Video-Upload = 1'600 Units → ca. 6 Videos/Tag mit Standard-Quota)
  → **Hinweis im Interface anzeigen**, ggf. Quota-Erhöhung bei Google beantragen

### Metadaten-Platzhalter

```python
# Verfügbare Platzhalter im Titel-/Beschreibungs-Template:
{turniername}    # "STS Bern 2026"
{disziplin}      # "Doppel" oder "Einzel"
{datum}          # "17./18. Mai 2026"
{ort}            # "Bern, Schweiz"
{kamera}         # "T03" (aus Ordnername)
{nummer}         # "1", "2", "3"... (laufende Nummer)
```

### Playlist-Logik

- Pro Turnier 2 Playlists: eine für Doppel, eine für Einzel
- Option "Neu anlegen": Playlist wird beim ersten Upload erstellt
- Option "Bestehende ID": existierende Playlist-ID aus YouTube eintragen
- Wenn Disziplin deaktiviert: kein Playlist-Eintrag

---

## 7. Technische Constraints und Learnings (aus v6 übernehmen)

```python
# Encoding – immer am Anfang jedes Skripts
import sys
sys.stdout.reconfigure(encoding="utf-8")

# subprocess – immer so, nie os.system()
import subprocess
result = subprocess.run(["ffmpeg", ...], capture_output=True, text=True)

# os.walk – nur einmal pro Ordner
for root, dirs, files in os.walk(folder):
    mp4s = [f for f in files if f.lower().endswith(".mp4")]

# Leere Konstanten filtern
parts = [str(p) for p in [k1, k2, varA, k4, k5, k6, varB] if str(p).strip()]
filename = " ".join(parts) + ".mp4"

# Logging – Datei offen halten
log_file = open("pipeline.log", "a", encoding="utf-8")
log_file.write(f"{timestamp} {message}\n")
log_file.flush()  # nicht bei jeder Zeile neu öffnen

# ThreadPoolExecutor für FFmpeg
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(merge_folder, folder) for folder in folders]
```

### Linux-spezifische Anpassungen (gegenüber Windows v6)

| Windows | Linux/NAS |
|---|---|
| `D:\1_Scripts\` | `/volume1/video-pipeline/scripts/` |
| `os.sep` = `\` | `os.sep` = `/` – immer `os.path.join()` verwenden |
| Robocopy | `shutil.copy2()` oder `rsync` via subprocess |
| pywin32 / wmic | `psutil` für Disk-Stats |
| PowerShell | bash / Python-native |
| `cp1252` Terminal | UTF-8 nativ |

---

## 8. Docker-Setup

### Anforderungen an das Docker-Image

```dockerfile
# Basis: Python 3.11 slim (stabil, klein)
FROM python:3.11-slim

# System-Pakete
RUN apt-get update && apt-get install -y ffmpeg

# Python-Pakete
# flask, watchdog, psutil, google-api-python-client,
# google-auth-oauthlib, requests
```

### docker-compose.yml (Ziel)

```yaml
services:
  video-pipeline:
    image: video-pipeline:latest
    ports:
      - "5000:5000"
    volumes:
      - /volume1/video-pipeline:/data
    restart: unless-stopped
    environment:
      - TZ=Europe/Zurich
```

### Synology Container Manager

- DS1522+ unterstützt Docker via "Container Manager" (DSM 7.2+)
- Deployment: docker-compose.yml über Container Manager UI
- Alternativ: SSH + `docker compose up -d`

---

## 9. Installationsanleitung (muss mitgeliefert werden)

Die fertige Lösung muss eine `INSTALL.md` enthalten mit:

1. Voraussetzungen prüfen (DSM-Version, Container Manager installieren)
2. SSH auf DS1522+ aktivieren
3. Ordner-Struktur anlegen (`/volume1/video-pipeline/...`)
4. SMB-Freigaben einrichten (eingang_doppel, eingang_einzel)
5. Docker Image bauen oder laden
6. docker-compose.yml anpassen (Pfade, Passwort)
7. Container starten
8. Google OAuth einrichten (YouTube API, einmalig)
9. Synology QuickConnect aktivieren
10. Erster Test-Durchlauf

---

## 10. Build-Reihenfolge für Claude Code

Baue in dieser Reihenfolge. Jeder Schritt soll einzeln testbar sein.

### Schritt 1 – Pipeline-Engine (Linux-portiert)
Dateien: `config_loader.py`, `MoveFiles.py`, `2_OrganizeFolders.py`,
`3_rename_mp4.py`, `5_MergeFFmpeg.py`

- Windows-spezifisches entfernen (Robocopy, pywin32, etc.)
- Pfade auf Linux anpassen (os.path.join durchgehend)
- Dual-Config vorbereiten (config_doppel.json / config_einzel.json)
- Unit-Tests für jeden Schritt

### Schritt 2 – Folder-Watcher + Pipeline-Runner
Datei: `pipeline_runner.py`, `folder_watcher.py`

- watchdog überwacht eingang_doppel/ und eingang_einzel/
- Erkennt "Upload abgeschlossen" (keine neuen Dateien für X Sekunden)
- Startet Pipeline-Engine als Subprocess
- Status wird in `status_doppel.json` / `status_einzel.json` persistiert

### Schritt 3 – Flask Web-Interface
Dateien: `app.py`, `templates/index.html`, `static/`

- Login-Schutz (einfaches Passwort in config)
- Zwei Tabs: Doppel / Einzel
- Status-Anzeige (liest status_*.json)
- Live-Log via SSE oder Polling
- Download-Bereich (fertige Videos)
- Pipeline manuell starten/stoppen

### Schritt 4 – YouTube-Modul
Dateien: `youtube_uploader.py`, ergänzt `app.py` und `templates/`

- Google OAuth 2.0 Setup (Token-Speicherung)
- Metadaten-Formular im Web-Interface
- Titel-Generierung aus Template + Platzhalter
- Vorschau vor Upload
- Upload-Funktion mit Fortschrittsanzeige
- Playlist anlegen / auswählen

### Schritt 5 – Docker + Installationsanleitung
Dateien: `Dockerfile`, `docker-compose.yml`, `INSTALL.md`

- Dockerfile mit Python 3.11 + FFmpeg
- docker-compose.yml mit Volume-Mounts
- `INSTALL.md` Schritt-für-Schritt für DS1522+
- `requirements.txt`

---

## 11. Projekt-Struktur (Ziel)

```
video-pipeline-nas/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── INSTALL.md
├── README.md
│
├── config/
│   ├── config_doppel.json
│   ├── config_einzel.json
│   └── config_template.json    ← Vorlage mit Kommentaren
│
├── pipeline/
│   ├── config_loader.py
│   ├── MoveFiles.py
│   ├── organize_folders.py     (= 2_OrganizeFolders.py, umbenannt)
│   ├── rename_mp4.py           (= 3_rename_mp4.py)
│   └── merge_ffmpeg.py         (= 5_MergeFFmpeg.py)
│
├── watcher/
│   ├── folder_watcher.py
│   └── pipeline_runner.py
│
├── web/
│   ├── app.py                  (Flask App)
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html          (Haupt-Interface, zwei Tabs)
│   │   └── login.html
│   └── static/
│       ├── style.css
│       └── app.js
│
└── youtube/
    ├── youtube_uploader.py
    ├── oauth_setup.py
    └── metadata_builder.py
```

---

## 12. Qualitäts-Anforderungen

- Kein Durcheinander zwischen Doppel und Einzel: **strikte Trennung** auf Ordner- und Config-Ebene
- Pipeline-Status überlebt Server-Neustart (JSON-Persistenz)
- Fehlerbehandlung: wenn FFmpeg fehlschlägt → Status "Fehler", Log-Eintrag, keine weiteren Schritte
- Kein direktes Schreiben in `eingang_*` während Watcher aktiv (Race Condition vermeiden)
- Alle Pfade über `os.path.join()`, nie String-Konkatenation
- Secrets (Passwort, Google OAuth Token) nie in Code hardcodiert → `.env` Datei
