# Installation auf der Synology DS1522+

Diese Anleitung beschreibt die einmalige Einrichtung der Video-Pipeline auf
einer Synology DS1522+ (DSM 7.2 oder neuer). Sie folgt der Build-Reihenfolge
aus `PROJEKT_BRIEFING.md` Abschnitt 9.

> Voraussetzung: Du hast Admin-Zugriff auf das DSM-Webinterface und kennst
> das Passwort des `admin`-Accounts.

---

## 1. Voraussetzungen pruefen

| Komponente | Anforderung |
|---|---|
| DSM | 7.2 oder neuer |
| Container Manager | Aus dem **Paket-Zentrum** installiert |
| Speicher | Mindestens 100 GB frei auf `/volume1/` (Roh-Footage 1-2 TB pro Lauf) |
| RAM | 8 GB (vorhanden auf DS1522+) |
| Netzwerk | 10GbE-Karte (E10G22-T1-Mini) installiert und konfiguriert |

Pruefe DSM-Version: **DSM-Systemsteuerung -> Info-Center -> Allgemein -> DSM-Version**.

---

## 2. SSH auf der DS1522+ aktivieren

DSM-Systemsteuerung -> **Terminal & SNMP** -> **SSH-Dienst aktivieren**, Port 22.
Anschliessend von einem Laptop verbinden:

```bash
ssh admin@<NAS-IP>
sudo -i      # Root-Shell fuer die folgenden Schritte
```

---

## 3. Ordner-Struktur anlegen

Auf der NAS einmalig die komplette Verzeichnishierarchie anlegen (alle
Pfade liegen unter `/volume1/video-pipeline/`):

```bash
mkdir -p /volume1/video-pipeline/{eingang_doppel,eingang_einzel}
mkdir -p /volume1/video-pipeline/{work_doppel,work_einzel}
mkdir -p /volume1/video-pipeline/{output_doppel,output_einzel}
mkdir -p /volume1/video-pipeline/logs

# Container laeuft als uid 1000 (siehe Dockerfile) - muss schreiben duerfen.
chown -R 1000:1000 /volume1/video-pipeline
```

---

## 4. SMB-Freigaben fuer die Laptops

DSM-Systemsteuerung -> **Freigegebener Ordner** -> **Erstellen**:

| Name | Pfad | Berechtigung |
|---|---|---|
| `eingang_doppel` | `/volume1/video-pipeline/eingang_doppel` | Lese-/Schreib-Zugriff fuer Aufnahme-Laptop |
| `eingang_einzel` | `/volume1/video-pipeline/eingang_einzel` | Lese-/Schreib-Zugriff fuer Aufnahme-Laptop |

Anschliessend auf dem Aufnahme-Laptop einbinden:

* macOS: **Finder -> Go -> Connect to Server** -> `smb://<NAS-IP>/eingang_doppel`
* Windows: `\\<NAS-IP>\eingang_doppel` als Netzlaufwerk verbinden

> Tipp: ueber 10GbE schiebt der Aufnahme-Laptop 1-2 TB in unter 30 Minuten.

---

## 5. Repository auf die DS1522+ holen

```bash
cd /volume1/docker         # uebliches Verzeichnis fuer Container-Quellen
git clone https://github.com/MichaelSaetteli/SwissTablesoccerRelive.git
cd SwissTablesoccerRelive
```

(Alternativ: Repository als ZIP herunterladen und entpacken.)

---

## 6. Configs anpassen

Beispiel-Configs aus dem Repo ins `/data`-Verzeichnis kopieren und
anpassen:

```bash
cp config/config_doppel.json /volume1/video-pipeline/config_doppel.json
cp config/config_einzel.json /volume1/video-pipeline/config_einzel.json
```

In jeder Datei:

* `filename_constants.jahr`, `sts_nummer`, `turniername`, `disziplin`, `part`
  passen die Standardwerte fuer die Output-Dateinamen an. Diese koennen
  spaeter auch komfortabel ueber das Web-Interface aenderbar gemacht
  werden ("Datei-Benennung"-Sektion).
* `paths.*` muss auf `/data/...` zeigen (NICHT `/volume1/...`) - das ist
  die Sicht aus dem Container.
* `enabled: false` setzen, wenn die jeweilige Disziplin gerade nicht
  produziert wird (Tab erscheint dann grau).

---

## 7. docker-compose.yml: Passwoerter setzen

In `docker-compose.yml` die folgenden Felder anpassen:

```yaml
environment:
  WEB_USERNAME: admin              # gewuenschter Login-Name
  WEB_PASSWORD: <starkes-passwort> # ZWINGEND aendern!
  WEB_SECRET_KEY: <zufaelliger-string-mind-32-zeichen>
```

`WEB_SECRET_KEY` mit einem zufaelligen Wert generieren:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 8. Docker-Image bauen und Container starten

```bash
cd /volume1/docker/SwissTablesoccerRelive
docker compose build
docker compose up -d
docker compose logs -f       # zum Mitschauen, Ctrl-C beendet das Tailing
```

Die Logs sollten zeigen:

```
[watcher] Doppel: started on /data/eingang_doppel
[watcher] Einzel: started on /data/eingang_einzel
[web] waitress serving on http://0.0.0.0:5000
```

Im DSM **Container Manager -> Container** sollte `video-pipeline` als
"laeuft" auftauchen mit Healthcheck-Status "healthy" (nach ca. 30s).

---

## 9. Lokaler Web-Zugriff

Im Browser auf einem Geraet im gleichen Netz:

```
http://<NAS-IP>:5000/
```

Login mit dem in Schritt 7 gesetzten Benutzer + Passwort. Du siehst die
beiden Tabs **Doppel** und **Einzel**, beide auf Status `idle`.

---

## 10. Synology QuickConnect aktivieren (optional, Internet-Zugriff)

DSM-Systemsteuerung -> **Externer Zugriff** -> **QuickConnect**:

1. Bei Synology-Konto anmelden (oder neues anlegen)
2. **QuickConnect aktivieren** ankreuzen
3. Eindeutige `quickconnect.to`-ID waehlen (z.B. `tfcsg-pipeline`)

QuickConnect bringt Port 5000 ohne Portforwarding aus dem Internet
erreichbar. URL danach: `https://<id>.quickconnect.to:5000` (oder ueber
das Reverse-Proxy-Feature im DSM).

> Sicherheitshinweis: das Web-Interface schuetzt nur ein einfaches
> Login-Formular. Das gewaehlte Passwort sollte entsprechend stark sein.
> Sessions werden mit `WEB_SECRET_KEY` signiert - rotiert man diesen,
> werden alle aktiven Sessions ungueltig.

---

## 11. Google OAuth fuer YouTube-Upload einrichten

Die DS1522+ hat keinen Browser, deshalb wird der einmalige OAuth-Flow auf
einem Laptop ausgefuehrt und das Ergebnis-Token aufs NAS kopiert.

### 11a. Google-Cloud-Projekt anlegen

1. https://console.cloud.google.com/ -> neues Projekt **"video-pipeline-nas"**
2. **APIs & Dienste -> Bibliothek** -> *YouTube Data API v3* aktivieren
3. **APIs & Dienste -> Anmeldedaten** -> **OAuth-Client-ID erstellen**:
   * Anwendungstyp: **Desktop**
   * Name: `video-pipeline-nas`
4. JSON-Datei herunterladen, lokal als `client_secrets.json` speichern

### 11b. Token-Erzeugung auf dem Laptop

```bash
git clone https://github.com/MichaelSaetteli/SwissTablesoccerRelive.git
cd SwissTablesoccerRelive
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m youtube.oauth_setup client_secrets.json youtube_token.json
```

Das oeffnet im Standardbrowser den Google-Anmeldebildschirm. Nach dem
Bestaetigen landet `youtube_token.json` lokal.

### 11c. Token aufs NAS kopieren

```bash
scp youtube_token.json admin@<NAS-IP>:/volume1/video-pipeline/youtube_token.json
ssh admin@<NAS-IP> "sudo chown 1000:1000 /volume1/video-pipeline/youtube_token.json"
```

> Das Token enthaelt einen Refresh-Token mit unbegrenzter Lebensdauer.
> **Niemals** ins Repository einchecken - die `.gitignore` blockt es
> standardmaessig.

---

## 12. Erster Test-Durchlauf

1. Auf dem Aufnahme-Laptop einen kleinen Test-Ordner `ET01` mit 2-3
   MP4-Dateien per SMB nach `eingang_doppel` kopieren
2. Im Web-Interface auf Tab **Doppel** der Status sollte innerhalb von
   ca. 10 Sekunden (Quiescence-Window) auf `moving` -> `organizing` ->
   `renaming` -> `merging` -> `done` wechseln
3. In Sektion **Download** erscheint die fertige Datei
   `2026 STS2 T01 Seetal Doppel.mp4` (Schema-Werte aus deiner Config)
4. In **Datei-Benennung** koennen die Konstanten fuer den naechsten
   Lauf angepasst werden (z.B. `Turniername`)
5. In **YouTube-Upload** auf "Vorschau aktualisieren", die generierten
   Titel pruefen und mit "Upload starten" zu YouTube hochladen

---

## 13. Wartung

| Aufgabe | Befehl |
|---|---|
| Logs anschauen | `docker compose logs -f` |
| Container neu starten | `docker compose restart` |
| Image neu bauen (nach Update) | `docker compose build --pull && docker compose up -d` |
| Speicherplatz pruefen | `du -sh /volume1/video-pipeline/*` |
| Pipeline-Status auf Disk | `cat /volume1/video-pipeline/status_doppel.json` |

### YouTube-Quota

Standard-Quota: 10'000 Units pro Tag. Ein Video-Upload kostet 1'600 Units
-> ca. 6 Videos pro Tag. Das Web-Interface zeigt einen Hinweis vor jedem
Batch. Bei groesseren Turnieren ueber die Google-Cloud-Console eine
Quota-Erhoehung beantragen (Formular unter "APIs & Dienste -> Kontingente").

### Backups

Empfohlen: das gesamte Verzeichnis `/volume1/video-pipeline/` (insbesondere
die Configs + das `youtube_token.json`) regelmaessig per Hyper-Backup auf
ein externes Ziel sichern.

---

## 14. Troubleshooting

**Container startet nicht:**
```bash
docker compose logs --tail 50
```
Haeufige Ursachen: fehlende Configs in `/volume1/video-pipeline/`,
fehlende Schreibrechte (Schritt 3 nochmal: `chown -R 1000:1000 ...`).

**Watcher loest nicht aus:**
Pruefe in den Logs auf `[watcher] Doppel: started on ...`. Falls die
Quiescence-Erkennung haengt: SMB-Client schliesst evtl. Files nicht
sauber. Manuell ueber den "Pipeline starten"-Button im Web-Interface
ausloesen.

**FFmpeg-Fehler:**
Status springt auf `error`. Im Web-Interface (Sektion Steuerung -> Log)
oder via `docker compose logs` die FFmpeg-Stderr nachvollziehen. Meist
ist eine MP4 korrupt - die betreffende Datei aus `work_*` entfernen und
neu starten.

**YouTube-Upload-Fehler "Login required":**
`youtube_token.json` ist abgelaufen oder fehlt. Schritt 11 wiederholen.

---

Fertig. Bei Fragen / Issues: <https://github.com/MichaelSaetteli/SwissTablesoccerRelive/issues>
