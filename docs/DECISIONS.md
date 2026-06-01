# Entscheidungs-Log — SwissTablesoccerRelive

**Vor jeder neuen Entscheidung: [`INVARIANTS.md`](INVARIANTS.md) lesen.**
Was dort steht, ist gesetzt. Hier in DECISIONS sind die *veraenderbaren*
Entscheidungen — neue Eintraege duerfen Invarianten nicht aufweichen,
nur explizit aufheben (mit Operator-OK + Begruendung).

---

Dieses Dokument haelt wichtige Entscheidungen, Erkenntnisse und Operator-
Inputs fest, die den Code und die Architektur beeinflussen. Format: kurz,
chronologisch absteigend (neueste zuerst), eine Entscheidung pro Eintrag.

**Was hier gehoert:**
* Architektur-Entscheidungen mit Konsequenzen (z.B. „Stream-Copy only")
* Operator-Inputs, die das Verhalten der Software praegen (z.B. „Privacy-
  Default private", „7-Schritt-Workflow als Referenz")
* Bekannte Luecken / Abweichungen vom Soll-Verhalten (z.B. Luecke A / B)

**Was hier NICHT gehoert:** Routine-Commits, jeder Test-Run, jeder CLI-
Befehl — das steht im git-Log oder in GitHub-Issues.

---

## 2026-05-24 — Test-Material: Panasonic V777, MP4-Modus

**Operator-Input:** Test-Clips stammen von Panasonic V777 im MP4-Modus
(`.MP4`, nicht AVCHD/`.MTS`). Unterordner-Name unter `DCIM/` variiert pro
SD-Karte (Panasonic-Schema: `100PANA`, `101PANA`, ... bzw. proprietaere
Hash-Namen wie `228XDPHH`).

**Konsequenz:**
* Pipeline-Filter `*.mp4` (case-insensitiv) matched die Dateien.
* Haette die Kamera AVCHD geliefert, waere der Filter um `.MTS` zu
  erweitern oder die Kamera auf MP4-Modus umzustellen.
* Variierende DCIM-Unterordnernamen sind genau der Grund, warum
  Luecke A (Auto-Extraktion) wichtig ist — eine fixe Pfad-Annahme
  funktioniert nicht.

---

## 2026-05-24 — Workflow-Referenz: 7 Originalschritte des Operators

**Kontext:** Nach Session-Crash Abgleich des aktuellen Codes gegen den
urspruenglichen Laptop-Workflow.

**Operator-Originalworkflow (Soll-Verhalten):**

1. SD-Karten auf Laptop-Laufwerk D kopieren (`D:\21_Import-SD-Cards`)
2. Videos je SD-Karte aus DCIM-Struktur in flachen Ordner pro Tisch
   sammeln (bis zu 36 Files à ~2 GB, je 30 min)
3. Ergebnis: 24 Ordner Doppel + 24 Ordner Einzel
4. Merge vorbereiten — Umbenennung, ab 25 Videos „Part 2" (YouTube-Cap
   12 h = 24 × 30 min)
5. Merge durchfuehren, Dateiname YouTube-tauglich
6. YouTube-Upload
7. Originale archivieren, hochgeladene Videos loeschen

**Entscheidung:** Diese 7 Schritte sind die Referenz fuer Vollstaendigkeits-
Pruefungen. Abweichungen sind als „Luecke" zu dokumentieren.

**Konsequenz:** Zwei Luecken identifiziert (Luecke A und B, siehe naechste
Eintraege).

---

## 2026-05-24 — Luecke A: SD-Karten-Auto-Extraktion fehlt (Status: offen)

**Problem:** Die NAS-Pipeline erwartet `.MP4`-Dateien flach in `ETxx/`.
Kameras legen die Dateien aber in DCIM-Unterordner mit variierenden Namen
ab (`DCIM/100PANA/`, `DCIM/228XDPHH/`, ...). Schritt 2 des Original-
workflows („Videos rausziehen / flachziehen") ist nicht automatisiert.

**Aktueller Workaround:** Operator legt Dateien manuell flach in `ETxx/`.

**Entscheidung:**
* Fuer ersten End-to-End-Test akzeptiert.
* Auto-Extraktion als GitHub-Issue erfasst — Implementierung NACH
  erfolgreichem End-to-End-Test.

**Anforderungen an die spaetere Loesung:**
* Erkennt DCIM-Struktur rekursiv (egal wie der Unterordner heisst).
* Verschiebt nur `.MP4` / `.MTS`-Dateien, ignoriert Thumbnails / Metadaten.
* Macht das idempotent (mehrfaches Auslesen derselben Karte darf nicht
  zu Doppel-Dateien fuehren).

---

## 2026-05-24 — Luecke B: Archiv-Flow zielt auf leeres Verzeichnis (Status: offen, HOHE Prioritaet)

**Problem:**
* Pipeline-Schritt 3 verschiebt `eingang_<disziplin>/ETxx` →
  `work_<disziplin>/ETxx` (atomarer Rename).
* Nach dem Run ist `eingang_<disziplin>/` leer, die Originale liegen in
  `work_<disziplin>/`.
* Der Archiv-Flow archiviert aber `eingang_` (leer) + `output_`.
* Tiering verschiebt `work_` und `output_` auf HDD-Staging, Retention 7
  Tage → danach geloescht.

**Konsequenz:** Operator-Wunsch „Originale dauerhaft archivieren"
(Schritt 8) ist nicht erfuellt. Risiko nach echtem Turnier: Original-
Aufnahmen nach 7 Tagen verloren.

**Entscheidung:**
* Fix MUSS vor dem ersten echten Turnier-Run erfolgen.
* Reihenfolge:
  1. End-to-End-Test mit Testdaten (Verlust akzeptabel)
  2. Luecke B fixen (Archiv-Flow auf `work_` umstellen, „delete uploads"
     sauber davon trennen)
  3. Luecke A als separates Issue angehen

---

## 2026-05-24 — Arbeitsweise: Option (a) — End-to-End-Test vor Refactoring

**Entscheidung:** Test mit 15 echten Panasonic-V777-Clips wird VOR dem
Fix von Luecke B durchgezogen.

**Begruendung:**
* Test-Material ist verzichtbar — 7-Tage-Verlust akzeptabel.
* Echtes Kameramaterial deckt Codec-/Timestamp-Probleme auf, die
  Synthetik-Clips nicht zeigen.
* Erster realer Datenpunkt fuer den B2-Estimator (Merge-Dauer).
* Reduziert Doppelarbeit, falls der Test ohnehin Bugs zeigt.

---

## (vor 2026-05-24) — Stream-Copy only, kein Re-Encoding

**Begruendung:** 1-2 TB pro Turnier, NAS ist I/O-bound. `ffmpeg -c copy`
macht aus 36 × 30 min einen 12h-Output in Minuten statt Stunden.

**Konsequenz:** Alle Quell-Clips eines Tisches muessen denselben Codec /
Profile / Container haben. Mischbetrieb (z.B. GoPro + Panasonic am
gleichen Tisch) kann beim Concat fehlschlagen — bisher nicht aufgetreten,
aber als Risiko zu beobachten.

---

## (vor 2026-05-24) — YouTube-Privacy-Default = private

**Begruendung:** Operator schaltet manuell im YouTube-Studio frei.
Verhindert, dass fehlerhafte oder unvollstaendige Uploads oeffentlich
sichtbar werden.

**Konsequenz:** Schritt 7 des Originalworkflows („Videos freigeben") ist
bewusst manuell gehalten. Kein Auto-Publish geplant.

---

## (vor 2026-05-24) — ETxx-Ordnerkonvention (ET + 2-stellige Tischnummer)

**Begruendung:** Eindeutige Tisch-Zuordnung allein aus dem Ordnernamen,
ohne separate Metadaten-Datei. Pipeline lehnt nicht-konforme Ordner ab —
harte Validierung verhindert Fehlzuordnungen.

**Konsequenz:** Operator muss SD-Karten-Inhalt in korrekt benannten
Ordnern ablegen (heute manuell, siehe Luecke A).

---

## (vor 2026-05-24) — 24-Clip-Limit pro Merge, automatisches Part-Splitting

**Begruendung:** YouTube-Cap = 12 h pro Video. 24 × 30 min = 12 h. Ab dem
25. Clip splittet `organize_folders` in `ETxx_1` / `ETxx_2`, der Output
bekommt `Part 1` / `Part 2` im Dateinamen.

---

## (vor 2026-05-24) — Container als root, Host-Port 8080

**Begruendung:** Synology-DSM-spezifisch — Port 5000+5001 sind DSM-intern
reserviert; Shared Folders haben uid-Mismatch ohne root-Container.

---

## (vor 2026-05-24) — Atomare File-Writes (Temp-File + `os.replace`)

**Begruendung:** Status-Dateien (`status_*.json`, `upload_status_*.json`)
duerfen nie halb-geschrieben gelesen werden — das Web-UI pollt alle 3 s.

**Konsequenz:** Jeder Status-Update geht ueber Temp-Datei →
`os.replace`. `fsync` nur bei terminalen Wechseln (Run done, Upload done),
um Disk-I/O niedrig zu halten.
