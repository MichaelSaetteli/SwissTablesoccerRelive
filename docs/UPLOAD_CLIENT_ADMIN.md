# STS-Upload — Anleitung fuer Admins

Vorbereitung des Turniers und Verteilung des Operator-Tools (Issue #15).
Der Operator-Teil steht in [`UPLOAD_CLIENT_OPERATOR.md`](UPLOAD_CLIENT_OPERATOR.md),
der Server-Vertrag in [`UPLOAD_CLIENT_HOWTO.md`](UPLOAD_CLIENT_HOWTO.md).

Drei einmalige Schritte vor jedem Turnier: **Turnier anlegen**, **SD-Karten
beschriften**, **Tool verteilen**.

## 1. Turnier anlegen + erwartete Karten-Anzahl setzen

Die erwartete Karten-Anzahl pro Disziplin treibt den Vollstaendigkeits-Check
im Tool („18 / 30 Karten verifiziert"). Ohne sie weiss niemand, ob am Ende
eine Karte fehlt.

Im Web-UI-Tab **„Turniere"** ein Turnier anlegen. Die erwartete Anzahl wird
ueber die Tournament-API gesetzt (Felder `expected_cards_einzel` /
`expected_cards_doppel`):

```bash
# Login (Session-Cookie)
curl -c cookies.txt -X POST -d "username=admin&password=<pw>" \
    http://192.168.1.159:8080/login

# Turnier anlegen MIT erwarteter Karten-Anzahl
curl -b cookies.txt -X POST -H "Content-Type: application/json" \
    -d '{"name": "Seetal 2026 STS2",
         "expected_cards_einzel": 30,
         "expected_cards_doppel": 24}' \
    http://192.168.1.159:8080/api/tournaments/Einzel
# -> Antwort enthaelt die Turnier-"id" - die brauchst du fuer Schritt 2.

# Als aktives Turnier setzen (pro Disziplin)
curl -b cookies.txt -X POST \
    http://192.168.1.159:8080/api/tournaments/Einzel/<id>/activate
curl -b cookies.txt -X POST \
    http://192.168.1.159:8080/api/tournaments/Doppel/<id>/activate
```

Nachtraeglich aendern (z.B. eine Karte weniger):

```bash
curl -b cookies.txt -X PATCH -H "Content-Type: application/json" \
    -d '{"expected_cards_einzel": 28}' \
    http://192.168.1.159:8080/api/tournaments/Einzel/<id>
```

Das Tool fragt beim Start `GET /api/upload/active-tournament` ab und kennt
damit Name + erwartete Anzahl automatisch — der Operator waehlt **nie** ein
Turnier aus.

## 2. SD-Karten benennen (einmalig)

> **Geaendert 2026-06-02:** Es ist **kein** `.sts-card.json`-Marker pro Karte
> mehr noetig. Der Tisch kommt aus dem **Datentraegernamen** der Karte, die
> Disziplin waehlt der Operator beim Einlesen. Vollstaendige Begruendung:
> [`UPLOAD_CLIENT_INGEST.md`](UPLOAD_CLIENT_INGEST.md).

Gib jeder Karte **einmalig** einen Datentraegernamen mit der Tischnummer:

* Einzel-Tische: `E01`, `E02`, … `E30`
* Doppel-Tische: `D01`, `D02`, … `D30`

Unter Windows: Explorer → Rechtsklick auf das Laufwerk → **Umbenennen** (oder
in den Eigenschaften das Namensfeld). Der Name gehoert zur Karte, nicht zum
Laufwerksbuchstaben — dieselbe Karte heisst in jedem Slot gleich.

* Das Tool liest die Ziffern aus dem Namen (`E01` → Tisch `ET01`); der
  Buchstabe `E`/`D` ist nur ein Vorschlag fuer die Disziplin — massgebend
  ist die Batch-Auswahl des Operators (pro Karte korrigierbar).
* Karten mit Nummer im Namen koennen wiederverwendet werden; alte Aufnahmen
  auf der Karte fangen wir ueber den DCIM-Datums-Alarm ab (siehe Ingest-Doc).

Das Legacy-Skript `scripts/prepare_card.py` (Marker schreiben) bleibt
bestehen, ist fuer den neuen Ablauf aber **nicht mehr noetig**.

## 3. Tool bauen + verteilen

Das Tool ist eine einzelne Datei (`STS-Upload.exe` auf Windows). Es gibt
kein Auto-Update — pro Turnier die aktuelle Version verteilen.

### Bauen

Eine **Windows-`.exe` muss auf Windows gebaut werden** (PyInstaller
cross-kompiliert nicht). Auf dem Build-Rechner mit ausgechecktem Repo:

```bash
python -m pip install -r requirements-client.txt pyinstaller
pyinstaller upload_client.spec --noconfirm
# Ergebnis: dist/STS-Upload.exe  (bzw. dist/STS-Upload unter Linux/macOS)
```

Die Build-Definition steht in [`upload_client.spec`](../upload_client.spec)
(single-file, windowed). `dist/` und `build/` sind in `.gitignore` — die
gebaute Datei wird **nicht** eingecheckt, sondern direkt verteilt.

### Ohne eigenen Windows-Rechner: GitHub Actions

Zwei Wege, die `.exe` ganz ohne lokalen Windows-Build zu bekommen:

* **Artifact pro Build** — die Action **„Build Upload Client (Windows)"**
  laeuft bei jeder Aenderung am Client und manuell (Actions-Tab →
  „Run workflow"). Den gruenen Run oeffnen → unten unter **Artifacts** →
  `STS-Upload-windows` herunterladen → entpacken.
* **Versionierter Release per Tag** (empfohlen fuer die Verteilung) — einen
  Tag `upload-client-v<version>` pushen; die Action **„Release Upload Client"**
  baut die `.exe` und haengt sie an einen automatisch erstellten
  GitHub-Release:

  ```bash
  git tag upload-client-v1
  git push origin upload-client-v1
  ```

  Danach liegt die `STS-Upload.exe` auf der **Releases**-Seite des Repos —
  die Operatoren laden sie dort direkt herunter.

### Verteilen

* `STS-Upload.exe` an die Operatoren geben (USB, Share, Download).
* Dazu die **Zugangsdaten**: Server-URL (`http://<nas>:8080`), Benutzer,
  Passwort (dasselbe `WEB_PASSWORD` wie das Dashboard).
* Die Operator-Anleitung ([`UPLOAD_CLIENT_OPERATOR.md`](UPLOAD_CLIENT_OPERATOR.md))
  beilegen.

## Was der Operator NICHT kann (Schutzschicht)

* Kein Pfad, kein Tisch, kein Turnier zur Auswahl — alles aus der Markierung.
* Karten ohne Markierung oder fuer ein anderes Turnier werden angezeigt,
  aber **gesperrt**. Beschriften ist Admin-Aufgabe vor dem Turnier.
* Die NAS sieht einen Ordner erst, wenn er vollstaendig hochgeladen,
  verifiziert und freigegeben ist (atomarer Handoff) — kein halber Upload
  triggert die Pipeline.

## Mehrere Aufnahme-Sessions pro Karte (DCIM)

Hat eine Karte mehrere `DCIM/`-Unterordner (z.B. nach Kartenwechsel der
Kamera), zieht das Tool **alle** `.MP4` flach in den einen Tisch-Ordner und
behaelt die zeitliche Reihenfolge — kein Aufteilen, kein manuelles
Flatten noetig (Operator-Entscheid, siehe `SESSION_HANDOFF.md` §6a).
