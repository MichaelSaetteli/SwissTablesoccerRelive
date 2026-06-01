# Invarianten — SwissTablesoccerRelive

Dieses Dokument haelt Regeln fest, die **nicht zur Diskussion stehen**.
Eine Invariante ist eine Entscheidung, die so oft falsch in Frage gestellt
wurde, dass das Risiko der naechsten Falschdiskussion groesser ist als
der Wert einer Aenderung.

**Wer eine Invariante aendern will, braucht explizites Operator-OK in
einem expliziten Schritt.** Keine AI-Session darf Invarianten autonom
relativieren, „rationalisieren" oder ueberschreiben. Wenn der laufende
Code einer Invariante widerspricht, ist der Code der Fehler, nicht die
Invariante.

Wenn du als zukuenftige Claude-Session diese Datei liest: **lies sie zu
Ende, bevor du irgendwo Konfigurations- oder Architektur-Entscheidungen
triffst.** Wenn dir eine Invariante „komisch" vorkommt: trotzdem
einhalten, dann nachfragen — nicht erst handeln, dann rechtfertigen.

---

## INV-1 — Volume-Layout: SSD = hot path, HDD = nur Archiv

**Regel:**

| Volume | Verwendung |
|---|---|
| `/volume1/SDD/` (SSD) | `eingang_*`, `work_*`, `output_*`, configs, Status-Files, runs.db |
| `/volume2/HDD12TB/` | **nur** Archiv (nach erfolgtem YouTube-Upload) und Tiering-Staging |
| `/volume3/HDD11TB/` | sekundaeres Archiv, Backups, Pipeline-Logs |

**Begruendung:** Die Pipeline ist I/O-bound. SSD-Reads/Writes machen den
Unterschied zwischen Minuten und Stunden Merge-Dauer. HDD ist fuer
Massendaten gedacht, die nach dem Upload selten gelesen werden.

**Was das konkret heisst:**

* `eingang_einzel`, `eingang_doppel`, `work_einzel`, `work_doppel`,
  `output_einzel`, `output_doppel` muessen unter `/volume1/SDD/` liegen.
* `paths.logs` darf auf HDD liegen (Logs sind hauptsaechlich
  Schreib-Last und werden selten gelesen).
* `tiering.staging_root` und der Archiv-Root gehoeren auf HDD.

**Enforcement:**

* `pipeline/config_loader.py::_validate_volume_layout` lehnt Configs ab,
  die `eingang/work/output` auf `/volume2/*` oder `/volume3/*` legen.
* `tests/test_config_loader.py` enthaelt Regressions-Tests.
* Operator-Doku: `docs/ARCHITECTURE.md`, `CLAUDE.md`,
  `docker-compose.yml` (Kommentar-Block).

**Vergangene Falschdiskussionen:** Mehrere AI-Sessions haben begonnen,
HDD-Pfade als „architektonisch sinnvoll" zu rationalisieren, sobald sie
sie in einer Runtime-Config sahen. Das ist die Falle — die Runtime-Config
kann driften, die Invariante nicht.

---

## INV-2 — YouTube-Privacy-Default: `private`

**Regel:** Jeder neue YouTube-Upload startet mit Privacy-Setting `private`.
Freigabe erfolgt **manuell** im YouTube-Studio durch den Operator.

**Begruendung:** Verhindert, dass fehlerhafte oder unvollstaendige
Uploads oeffentlich erscheinen. Operator hat die letzte Pruefinstanz.

**Enforcement:** `youtube/youtube_uploader.py` setzt `privacyStatus`
hartcodiert. Es gibt keinen Code-Pfad, der das ueberschreibt.

---

## INV-3 — Stream-Copy only, kein Re-Encoding

**Regel:** Der Merge verwendet ausschliesslich `ffmpeg -f concat -c copy`.
Kein `-c:v libx264`, kein `-c:a aac`, kein „Konvertierung wenn die
Codecs nicht stimmen".

**Begruendung:** 1-2 TB pro Turnier. Re-Encoding wuerde aus Minuten
Stunden machen und I/O- + CPU-Budget der NAS sprengen. Alle Quell-Clips
eines Tisches muessen denselben Codec/Profile/Container haben — das ist
eine Kamera-Setup-Anforderung, kein Software-Feature.

**Enforcement:** `pipeline/merge_ffmpeg.py::build_ffmpeg_command`.

---

## INV-4 — Atomare File-Writes

**Regel:** Status-Files, Configs und Output-Videos werden niemals
„halb geschrieben" sichtbar. Schreiben geht immer ueber Temp-File +
`os.replace`. `fsync` nur bei terminalen Wechseln (Run done, Upload
done), damit Disk-I/O niedrig bleibt.

**Begruendung:** Das Web-UI pollt alle 3 s. Ein halb-geschriebenes
Status-JSON wuerde die UI in undefinierte Zustaende stuerzen. Ein
halb-geschriebenes Output-Video wuerde versehentlich hochgeladen.

**Enforcement:** `pipeline/status_file.py`, `pipeline/config_loader.py::save_config`,
`pipeline/merge_ffmpeg.py` (`.partial`-Pattern).

---

## INV-5 — ETxx-Ordnerkonvention

**Regel:** Eingangs-Ordner heissen `ET` + zweistellige Tischnummer
(z.B. `ET01`, `ET20`). Nach Split bei >24 Files auch `ETxx_1`, `ETxx_2`.
Andere Ordnernamen werden **abgelehnt**, nicht „intelligent geraten".

**Begruendung:** Eindeutige Tisch-Zuordnung ohne Metadaten-Datei. Harte
Validierung verhindert Fehlzuordnungen, die im Stream-Copy-Setup
katastrophal waeren (falsche Spielmaterialien zusammengeschnitten).

**Enforcement:** `pipeline/config_loader.py::parse_folder_name`,
`watcher/folder_watcher.py` (Filter).

---

## Wie diese Datei zu pflegen ist

* **Neue Invarianten** entstehen, wenn eine Entscheidung **mehrfach**
  faelschlich relativiert wurde. Eine einzelne Diskussion macht noch
  keine Invariante.
* **Bestehende Invarianten aendern:** nur durch expliziten Operator-Beschluss,
  dokumentiert in `docs/DECISIONS.md` mit Begruendung. Der Operator-OK
  muss explizit auf die Invariante referenzieren („INV-1 wird aufgehoben
  weil ..."), nicht implizit durch eine Code-Aenderung.
* **AI-Sessions** lesen INVARIANTS.md vor jeder Architektur- oder
  Konfigurationsentscheidung. Wenn eine vorgeschlagene Aenderung gegen
  eine Invariante laeuft, ist die Antwort „nein", nicht „lass uns
  diskutieren".
