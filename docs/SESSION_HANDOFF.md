# Session-Handoff — SwissTablesoccerRelive

**Letzter Update:** 2026-06-02
**Letzte Session:** Slice 1 von Issue #15 (STS-Upload Server-Vertrag)

Dieses Dokument ist der **eine Anlaufpunkt** fuer eine neue Claude-Session
oder einen neuen Mitleser. Wer hier alles liest, hat den Kontext den die
laufende Arbeit braucht — die volle Chat-Historie ist nicht noetig.

## 1. Wo wir stehen

* **Pipeline (Move → Organize → Rename → Merge → YouTube-Upload):**
  funktional und im Container laufend. Verifiziert mit einer Real-Welt-
  Pipeline-Test-Session (ET21-Material — silent ffmpeg-failure-Bug
  entdeckt, siehe Punkt 4).
* **STS-Upload Server-Vertrag (Issue #15, Slice 1):** live, alle 7
  Endpoints durchgespielt, atomic-handoff in `eingang_einzel/ET99/`
  bewiesen. PR #16 offen.
* **Hardening (INVARIANTS + config-Validierung):** auf separater Branch,
  PR steht aus.

## 2. Aktive Branches und ihre Rolle

| Branch | Status | Was drauf |
|---|---|---|
| `main` | uralt | nur minimaler Projekt-Skelett |
| `claude/wizardly-goodall-df2er` | Default-Arbeitsbranch | Pipeline + Watcher + Web + YouTube, alles deployed |
| `fix/archive-originals-from-work` | PR #13 offen | Luecke-B-Fix (Archiv-Flow auf `work_`) |
| `hardening/invariants-and-config-validation` | PR ?, gepusht | INVARIANTS.md, INV-1-Enforcement in config_loader |
| `feat/upload-staging-server` | PR #16 offen | Slice 1 von Issue #15 — Server-Vertrag |

**Pro neuer Slice eine eigene Branch.** Niemals direkt auf `main` oder
`claude/wizardly-goodall-df2er` committen ohne PR-Review.

## 3. Offene PRs und Issues

| Nummer | Titel | Status |
|---|---|---|
| PR #13 | fix(archive): include work_ so originals survive | Code-Review erledigt, mergebar |
| PR #16 | feat(upload): server contract for the operator upload tool | Slice 1, gerade gepusht |
| Issue #14 | Auto-extract MP4s from SD card DCIM subfolders (Luecke A) | open — wird durch Issue #15 obsolet |
| Issue #15 | STS-Upload client tool (zero-error operator upload) | active — Slice 2 als Next |

## 4. Bekannte Bugs, die noch nicht gefixt sind

| Bug | Wo | Schweregrad |
|---|---|---|
| **ffmpeg silent failure**: `-c copy` setzt `returncode=0` bei korrupten Inputs trotz Daten-Abbruch | `pipeline/merge_ffmpeg.py` | hoch — Pipeline meldet "Erfolg" mit kaputtem Output. Fix: zusaetzliche Output-Dauer-Verifikation gegen Soll-Dauer |
| **Doppel-Config zeigt noch auf HDD** | `/volume1/SDD/video-pipeline-config/config_doppel.json` | mittel — Pipeline laeuft, aber Watcher beobachtet HDD statt SSD. Wird vom Hardening-PR abgefangen (Loader-Reject) |
| **Secret-Rotation deferred** | `.env` auf NAS hat noch alte hartkodierte Werte | niedrig auf LAN, aber technische Schuld |
| **Luecke A** (DCIM-Auto-Extraktion) | Pipeline-seitig | obsolet wenn Client-Tool (Issue #15) fertig ist |

## 5. Was die letzte Session geliefert hat (Issue #15 Slice 1)

* `upload_staging/`-Paket: paths, manifest, atomic-handoff, service
* `db/uploads.py` + Schema-Migration fuer `upload_cards` + neue
  `expected_cards_*`-Spalten auf `tournaments`
* 7 HTTP-Endpoints unter `/api/upload/*`
* Admin-CLI `scripts/prepare_card.py` (schreibt `.sts-card.json` auf SD-
  Karten-Root, normalisiert ETxx auf Upper, atomic write)
* Demo-CLI `scripts/sts_upload_demo.py` (Python+requests, demonstriert
  End-to-End-Flow)
* How-To `docs/UPLOAD_CLIENT_HOWTO.md` (curl-Beispiele + Endpoint-
  Tabelle)
* 64 neue Tests; 342/342 in der Suite gruen

Live-bewiesen mit echtem Lauf gegen die NAS:
* Login → start → 3 Chunks → finish (auto_release) → atomic rename →
  Watcher detektiert ET99 → Pipeline-Move zu work_einzel/ET99
* Per-card State-Machine durchgespielt (uploading → verified → released)
* Manifest-Verifikation (3 Files / 6.291.456 Bytes) erfolgreich

## 6. Was als naechstes ansteht — Slice 2 von Issue #15

Das ist der **konkrete naechste Arbeitsblock**. Vollstaendige Spec in
Issue #15; hier die Headline:

1. **PySide6-GUI** (Cross-Platform, Windows zwingend, macOS optional)
   * Wireframe aus Issue #15
   * Per-Karte-Liste mit State-Badges (`uploading`, `verified`,
     `released`, `failed`, `interrupted`)
   * `Auto-Release`-Checkbox (Default off)
   * Pro Karte: aktueller File-Fortschritt, ETA, Safe-to-remove-Indikator
2. **SD-Karten-Auto-Detection**
   * Mount/unmount-Events lauschen (`pyudev` auf Linux, `wmi` auf Windows)
   * Marker-Datei lesen (`.sts-card.json`)
   * Vollstaendigkeits-Check (erwartete vs gefundene Karten)
3. **Hardware-Mount-Monitoring**
   * Karte entfernt waehrend `uploading` → State `interrupted`
   * Karte re-inserted → resumed, nicht restarted
4. **Lokales Resume-State-File**
   * Tool-Crash → beim Neustart "X Karten waren in Upload, fortsetzen?"
5. **Distribution**
   * Windows: portable `.exe` via PyInstaller
   * Auto-Update vorerst nicht; Admin verteilt neue Version pro Turnier
6. **Operator + Admin Doku**

Realistisch **1.5–2 Wochen fokussierte Arbeit** fuer Slice 2.

## 6a. Slice 2 — Settled Requirements (nicht mehr diskutieren)

Diese Punkte sind mit dem Operator durchgesprochen und entschieden. Eine
neue Session, die anfaengt sie zu hinterfragen, verbrennt Operator-Zeit.
Wenn etwas davon sich aendern muss, ist das ein expliziter Operator-
Beschluss in dieser Datei UND in Issue #15 — nicht eine Diskussion im
Chat. Default-Antwort auf „sollen wir nicht doch X tun?": **nein, das ist
gesetzt.**

### Operator-Workflow

* **Hoechstens 3 Klicks** vom „Karten eingesteckt" bis „alle Uploads
  released": (1) „SD-Karten einlesen", (2) „Hochladen starten",
  (3) „Freigeben". Mit Auto-Release sind's nur 2.
* **Operator hat KEINE Eingabe-Felder** ausser den Klicks und der
  Auto-Release-Checkbox. Kein Pfad, kein Tisch, kein Turnier zur Auswahl.
* **Aktives Turnier ist auto-discovered** ueber `/api/upload/active-tournament`.
  Es gibt immer genau eines aktives Turnier — nie mehrere zur Auswahl
  stellen.
* **Tisch und Disziplin kommen aus dem Marker** (`.sts-card.json`), nie
  aus operator-Eingabe.
* **Variable Karten-Anzahl**: bis zu 30 pro Disziplin (Einzel + Doppel
  = bis zu 60 pro Turnier). UI muss auch mit 60 Zeilen lesbar bleiben.
* **Variable Reader-Parallelitaet**: 1-Slot bis 24-Slot. Tool muss
  beides gleich gut handhaben — additiver Scan (Re-Scan fuegt Karten
  hinzu, resettet NICHT bereits geladene).

### Per-Karten-Workflow

* **Freigabe ist separat vom Upload** (entkoppelt). Operator kann
  einzelne Karten oder Batches freigeben.
* **Auto-Release**-Option pro Tool-Session (nicht pro Karte), als
  Checkbox im UI, **Default off**. Wenn aktiv: nach `verified` direkt
  nach `released`.
* **„Alle verifizierten freigeben"** als einzelner Klick verfuegbar
  (Convenience fuer die Operatoren, die alle Karten prueffen wollen
  bevor sie alles auf einmal triggern).

### Hardware-State-Patterns (Safety-Layer)

Diese UX-Patterns sind explizit als **Schutzschicht gegen Handlings-
fehler** entschieden, nicht „nice to have":

| Pattern | Verhalten |
|---|---|
| Safe-to-remove-Indikator | `🔌 sicher entfernbar` erscheint **nur** wenn State in {`verified`, `released`}. Sonst `⛔ Nicht entfernen — Upload laeuft` |
| Mount/unmount-Awareness | Karte entfernt waehrend `uploading` → State `interrupted` + grosse rote Warnung + Recovery-Hinweis. Bei Re-Insertion automatisch resumen, nicht restarten |
| Defensive Buttons | „Freigeben"-Button disabled solange Karte in `uploading`. Tooltip erklaert warum |
| Resumability | Lokales State-File. Tool-Crash → beim Neustart „14 Karten waren in Upload, fortsetzen?" |
| Marker-Mismatch-Schutz | Karte mit Marker eines anderen/alten Turniers → angezeigt aber gesperrt, mit Erklaerung |
| Doppel-Scan-Robustheit | Mehrfach-Klick auf „Einlesen" → additiver Scan, keine Duplikate, kein State-Reset |
| Abschluss-Klarheit | Wenn alle erwarteten Karten released → grosser gruener Success-State |
| Fehlersprache verstaendlich | „Karte ET05: Uebertragungsfehler. Bitte erneut hochladen." statt SHA-256 mismatch chunk 47/812 (Details via Aufklapper) |

### Architektur-Entscheidungen

* **Stack**: Python + PySide6 (NICHT Tauri, NICHT Electron). Begruendung:
  kleines Binary, kein Browser-Wrapper, vertrauter Stack fuer das Server-
  Team. Wer Tauri/Electron diskutieren will: bitte hier zuerst aendern.
* **Transport**: HTTP-Multipart ueber die bestehende Flask-App (NICHT
  SFTP, NICHT WebDAV). Endpoints stehen bereits.
* **Verifikation MVP**: File-Count + Total-Bytes. **Kein SHA-256** im
  MVP (verdoppelt SD-Karten-I/O). Spaeter optional.
* **Marker-Datei**: `.sts-card.json` im Karten-Root. Karten ohne Marker
  werden im UI angezeigt aber gesperrt — niemals stillschweigend
  ignorieren oder vom Operator beschriften lassen (das ist Admin-Aufgabe
  vor dem Turnier).
* **Tournament-Discovery**: ueber `/api/upload/active-tournament`. Tool
  kennt KEINE Tournament-IDs aus eigenem Wissen.
* **Plattform-MVP**: Windows. macOS/Linux nach Bedarf spaeter.
* **Auto-Update**: nicht im MVP. Admin verteilt neue Versionen pro
  Turnier.

### Was bewusst NICHT zum MVP gehoert

* SHA-256-Verifikation (verdoppelt SD-I/O — spaeter wenn Production
  zeigt dass es noetig ist)
* Auto-Update-Mechanismus
* macOS-/Linux-Builds
* Multi-Tournament-Auswahl (es gibt immer genau eins aktives)
* Operator-Felder zum „Tisch manuell zuweisen" (immer Marker-basiert)
* Karten ohne Marker silent hochladen (mit Hinweis, aber nicht hochladbar)

### Was die Server-Seite bereits liefert (siehe `docs/UPLOAD_CLIENT_HOWTO.md`)

* `GET  /api/upload/active-tournament` — Discovery
* `POST /api/upload/start` — Reserve staging + DB-Row
* `POST /api/upload/<id>/chunk` — Multipart, ein File pro Call
* `POST /api/upload/<id>/finish` — Manifest-Verify, optional Auto-Release
* `POST /api/upload/release` — atomic rename, single oder batch
* `POST /api/upload/<id>/cancel`
* `GET  /api/upload/status` — Polling fuer UI

Der Client baut **ausschliesslich** gegen diese Endpoints. Neue Endpoints
nur dann, wenn die UX hart blockiert ist und der Operator zustimmt.

## 7. Mistakes-not-to-repeat (Kontext fuer eine neue Claude-Session)

Wenn du eine neue Session bist, lies das hier zuerst, sonst wiederholen
sich Reibungspunkte aus den letzten Sessions:

1. **Volume-Layout NICHT neu erfinden.** `eingang_*`, `work_*`,
   `output_*` gehoeren auf SSD. HDD ist nur fuer Archiv. Wenn eine
   Runtime-Config HDD-Pfade fuer eingang/work/output zeigt, ist die
   **Config der Bug** — nicht die Architektur. Siehe
   [`INVARIANTS.md`](INVARIANTS.md) INV-1. Das wurde mehrfach faelsch-
   licherweise „rationalisiert"; die Hardening-Branch baut Code-
   Enforcement dafuer ein.
2. **Niemals das `init`-Skill ungefragt auf existierendem Repo laufen
   lassen** — es ueberschreibt bestehende CLAUDE.md mit einem generischen
   Stub. Wenn `init` angeboten wird, immer beim Operator nachfragen.
3. **Auf der NAS laeuft Docker-Container als root**, weil Synology
   Shared Folders sonst uid-Mismatch haetten. Konsequenz: der Container
   schreibt root-owned Files ins Repo (z.B. `.git/config` mit Mode 000,
   `.env` mit Mode 600). Operator muss `chown -R saetteli:users` machen,
   bevor er ueber `cc` (NAS-Claude) Files anfasst.
4. **`sudo` auf der NAS verlangt Passwort interaktiv.** NAS-Claude kann
   nicht non-interactive sudo machen. Lange Listen sudo-Befehle gehoeren
   in eine zweite SSH-Session beim Operator, nicht in den NAS-Claude-Tab.
5. **Pipeline-Logs gehen NICHT auf docker stdout** sondern in per-run
   Files unter dem `paths.logs`-Verzeichnis aus der Config (typisch
   `/volume3/HDD11TB/pipeline_logs/`). Wer ffmpeg-Errors sucht: dort
   gucken, nicht in `docker logs`.

## 8. Test-Daten, die jetzt auf der NAS liegen

Wenn die naechste Session mit dem GUI loslegt und Server-Roundtrips
testen will:

* Aktives Turnier Einzel: id=2, Name "Untagged 2026-05-23 Einzel"
* Aktives Turnier Doppel: id=1, Name "TestTurnierSG"
* Test-Karte vom Server-Vertrag-Smoke: `/volume1/SDD/sts-test-card-et99/`
  mit Marker + 3 Dummy-Files (kann fuer GUI-Tests recycled werden)
* Upload-Card-Eintrag `id=1` (ET99) ist released; weitere Uploads gegen
  ET99 wuerden mit `final path already exists` failen — pro Testlauf
  einen neuen Tisch (ET98, ET97, ...) verwenden ODER ET99 vorher
  aufraeumen.
* Container und Web-UI laufen auf `http://192.168.1.159:8080/`
* WEB_USERNAME=admin; WEB_PASSWORD in `.env` (Secret-Rotation deferred)

## 9. Wie eine neue Claude-Session anfangen sollte

Empfohlener Prompt fuer den ersten Turn der neuen Session:

> Ich starte eine neue Session fuer den Slice 2 von Issue #15
> (PySide6-Client-Tool). Lies in dieser Reihenfolge:
> `docs/SESSION_HANDOFF.md` (inklusive §6 + §6a — die Slice-2-Settled-
> Requirements sind NICHT mehr zu diskutieren), `docs/INVARIANTS.md`,
> `docs/UPLOAD_CLIENT_HOWTO.md` (Server-Vertrag), Issue #15. Wenn alles
> klar ist, schlag mir einen Plan fuer den ersten konkreten Arbeitsblock
> vor — basierend auf dem in §6a Festgelegten, nicht „lass uns kurz die
> Architektur durchgehen". Der Server-Vertrag steht, du baust gegen die
> Endpoints.

Damit ist sie in ~4 Datei-Reads voll im Kontext und kann fokussiert
starten — **ohne** den langen Architektur-Discovery-Loop nochmal zu fahren.

**Wichtige Default-Antwort fuer die neue Session:** Wenn der Operator
beilaeufig sagt „sollen wir nicht X tun?" und X in §6a anders entschieden
ist, **darfst du NICHT zustimmen** — sondern auf §6a verweisen und
explizit fragen, ob er die Entscheidung kippen will. Dann erst aendern.
Sonst entsteht genau das Problem, das §6a verhindern soll: gleiche
Diskussionen aus alten Sessions wiederholen.

## 10. Bevorzugte Arbeitsweise des Operators

Erfahrungen aus mehreren Sessions, dem Operator zuliebe:

* **Ein-Befehl-eine-Antwort** ist effizienter als grosse Action-Bloecke
  fuer ihn. Schritt-fuer-Schritt-Anleitungen mit klarem „Schick mir den
  Output" am Ende.
* **Ehrliche Aufwandsschaetzungen** > Versprechen. Operator schaetzt
  „2-2.5 Wochen" mehr als „bis morgen fertig".
* **Komplexitaet reduzieren > clever sein.** Wenn ein Workflow drei
  manuelle Schritte hat, einen Befehl zu schreiben der alle drei
  hintereinanderfuegt ist okay; aber **erst pruefen ob die drei
  Schritte ueberhaupt richtig sind**.
* **Nie ungefragt destruktive Aktionen.** `git reset --hard`,
  `rm -rf`, `--no-verify` etc. brauchen explizites Operator-OK.
* **UX-Qualitaet beim Operator-Tool ist nicht nice-to-have**, sondern
  Schutzschicht gegen Handlingsfehler. Siehe Issue #15 „UX as a safety
  layer"-Sektion.
