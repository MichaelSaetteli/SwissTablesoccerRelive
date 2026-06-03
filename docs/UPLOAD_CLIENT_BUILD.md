# STS-Upload — `.exe` holen, ohne lokal zu bauen

Kurz-Spickzettel: Wo die fertige Windows-`.exe` herkommt und wie man sie an
die Operatoren verteilt. **Du musst PyInstaller nie lokal auf Windows
laufen lassen** — GitHub baut die `.exe` bei jedem Push automatisch.

## TL;DR

| Ich will… | So geht's |
|---|---|
| Die **neueste** `.exe` zum Testen | **Releases → „STS-Upload — neueste Test-Version"** → `.exe` herunterladen. Diese Seite wird bei jedem Push automatisch aktualisiert (fester Link, kein Zip, keine Befehle). |
| Eine **stabile Version für Kollegen** | Im Browser: **Releases → „Draft a new release"** → neuen Tag `upload-client-vN` anlegen → **Publish**. GitHub baut die `.exe` und haengt sie an. (Oder per Tag-Push, siehe unten.) |

> **Kein Terminal noetig.** Zum Testen reicht der feste „neueste Test-Version"-
> Link. Fuer eine versionierte Ausgabe genuegt der „Draft a new release"-Knopf
> im Browser.

## 1. Neueste `.exe` zum Testen (fester Link, einfachster Weg)

Jeder Push, der `upload_client/**` aendert, startet automatisch den Workflow
**Build Upload Client (Windows)**. Der baut die `.exe` und aktualisiert
danach eine **rollende Pre-Release** unter dem festen Tag `test-build`.

**So holst du sie (kein Terminal, kein Zip):**

1. Repo → rechts **Releases** → **„STS-Upload — neueste Test-Version"**.
2. Unter **Assets** die `STS-Upload.exe` herunterladen. Fertig.

Diese Seite zeigt **immer** den neuesten Build — einfach den Link als
Lesezeichen speichern. Nach einem Push ~3 min warten, dann neu laden.

**Alternative (Artifact):** Actions → letzter Run → unten **Artifacts** →
`STS-Upload-windows` (Zip, laeuft nach 30 Tagen ab). Nur noetig, wenn die
Release-Seite mal nicht greift. Ohne Code-Aenderung bauen: Actions →
Workflow → **Run workflow**.

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
