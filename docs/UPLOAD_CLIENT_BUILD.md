# STS-Upload — `.exe` holen, ohne lokal zu bauen

Kurz-Spickzettel: Wo die fertige Windows-`.exe` herkommt und wie man sie an
die Operatoren verteilt. **Du musst PyInstaller nie lokal auf Windows
laufen lassen** — GitHub baut die `.exe` bei jedem Push automatisch.

## TL;DR

| Ich will… | So geht's |
|---|---|
| Die **neueste** `.exe` zum Testen | Actions-Tab → letzter „Build Upload Client"-Run → unten **Artifacts** → `STS-Upload-windows` (Zip) herunterladen, entpacken. |
| Eine **stabile Version für Kollegen** | Versions-Tag pushen (`upload-client-vN`) → GitHub veroeffentlicht eine **Release-Seite mit der `.exe` direkt dran** (kein Zip, feste URL, laeuft nicht ab). |

## 1. Neueste `.exe` zum Testen (Artifact)

Jeder Push auf einen Branch, der `upload_client/**` aendert, startet
automatisch den Workflow **Build Upload Client (Windows)**
(`.github/workflows/build-upload-client.yml`).

1. Repo → **Actions** → links **Build Upload Client (Windows)**.
2. Obersten Run anklicken (gruener Haken = fertig, ~3 min).
3. Ganz unten unter **Artifacts**: `STS-Upload-windows` herunterladen.
4. Das ist ein **Zip** (GitHub zippt Artifacts immer) → entpacken →
   `STS-Upload.exe`.

> Artifacts laufen nach **30 Tagen** ab. Fuer etwas Dauerhaftes → Release.

Ohne Code-Aenderung trotzdem bauen: Actions → Workflow → **Run workflow**
(manueller Dispatch).

## 2. Stabile Version fuer die Operatoren (Release)

Fuer die Weitergabe an Kollegen ist ein **getaggter Release** der saubere
Weg — eine feste Download-Seite, die `.exe` direkt drangehaengt (kein Zip),
laeuft nicht ab. Workflow: `.github/workflows/release-upload-client.yml`.

```bash
# Tag auf den aktuellen Stand setzen und pushen:
git tag upload-client-v2
git push origin upload-client-v2
```

GitHub baut dann automatisch die `.exe` und legt unter **Releases** eine
Seite „STS-Upload upload-client-v2" mit der `.exe` als Download an. Link an
die Operatoren schicken — fertig.

Naechste Version → neuer Tag (`upload-client-v3`, …). Tags sind billig;
pro Turnier-Update einfach hochzaehlen.

## 3. Was die `.exe` enthaelt

Den Stand des Commits/Tags, der gebaut wurde — inklusive:

* dem gefuehrten GUI (Schritt-Indikator, Fortschrittsbalken mit MB/s + ETA),
* dem **„↺ Zuruecksetzen"-Knopf** (ersetzt das manuelle Loeschen von
  `state.json`, siehe unten),
* den Fixes fuer False-Alarm-„Karte entfernt" und ausgeblendete interne
  Platten.

## 4. `state.json` zuruecksetzen — jetzt im Tool

Frueher musste bei haengendem Status (`⛔ unterbrochen`) von Hand
`%USERPROFILE%\.sts_upload\state.json` geloescht werden. **Nicht mehr
noetig:** Im Tool unten links **„↺ Zuruecksetzen"** klicken → Sicherheits-
abfrage bestaetigen. Das vergisst nur den **lokalen** Fortschritts-Status;
es loescht **keine Videos** und **keine Server-Daten**. Danach „SD-Karten
einlesen" → alles beginnt sauber neu.
