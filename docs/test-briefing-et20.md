# Test-Briefing: End-to-End-Pipeline mit Panasonic-V777-Material (ET20)

**Stand:** 2026-05-25
**Verfasst von:** Claude Code (Cloud-Instanz)
**Zielgruppe:** NAS-Claude-Instanz (`/volume1/SDD/projects/SwissTablesoccerRelive`)
**Operator:** Michael (verfuegbar fuer Rueckfragen)

---

## Ziel

Erster echter End-to-End-Test der Video-Pipeline mit Kameramaterial
(nicht Synthetik). Validieren, dass der Watcher anspringt, die Pipeline
durchlaeuft und ein abspielbares Output-File entsteht. Erster realer
Datenpunkt fuer den B2-Estimator (Merge-Dauer).

## Kontext (was schon erledigt ist)

* 15 `.MP4`-Dateien (Panasonic V777, MP4-Modus, je ~2 GB, je 30 min) wurden
  vom Operator via DSM File Station flach in `/volume1/SDD/eingang_einzel/ET20/`
  abgelegt. **Kein DCIM-Unterordner** — Operator hat manuell flachgezogen
  (Luecke A, dokumentiert in `docs/DECISIONS.md`).
* Container `video-pipeline` laeuft auf Branch `claude/wizardly-goodall-df2er`
  (deployed-Stand). Branch hat **noch keinen Luecke-B-Fix** — das ist OK,
  dieser Test betrifft die Pipeline (Move/Organize/Rename/Merge), nicht
  den Archiv-Flow.
* Lucke-B-Fix wartet als PR #13 auf `fix/archive-originals-from-work`.

## Auftrag (autonom durchziehen)

### Schritt 1 — Eingangsdaten und Container-Zustand pruefen

```bash
ls -la /volume1/SDD/eingang_einzel/ET20/
du -sh /volume1/SDD/eingang_einzel/ET20/
sudo grep -E '(quiet|eingang|work|output)' /volume1/SDD/video-pipeline-config/config_einzel.json
sudo docker ps --filter "name=video-pipeline" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Erwartet:
* 15 `.MP4`-Dateien, ~30 GB total
* `quiet_seconds` aus der Config notieren (typisch 10)
* Container "Up X minutes" mit Port-Mapping `8080->5000`

**Wenn etwas nicht stimmt** (z.B. weniger Dateien, Container down):
beim Operator nachfragen, NICHT raten.

### Schritt 2 — Watcher- und Pipeline-Lauf beobachten

```bash
sudo docker logs -f --tail=50 video-pipeline
```

Erwartete Log-Sequenz innerhalb der ersten Minute:
1. `folder_watcher` erkennt `ET20` (eventuell schon vor diesem Test passiert
   — dann steht es weiter oben im Log, einmal `--tail=200` nachschauen)
2. Nach `quiet_seconds` Stille: `pipeline_runner` startet Run
3. Phasen in Reihenfolge: `MOVING` → `ORGANIZING` → `RENAMING` → `MERGING`
4. Pro Phase eine Zeile mit `duration_s`
5. Merge: `ffmpeg`-Output landet ueber `.<name>.partial` und wird atomar
   umbenannt

Wenn der Watcher **nicht** anspringt, mit `Ctrl+C` aus dem Log aussteigen
und pruefen:
```bash
sudo docker exec video-pipeline ls -la /volume1/SDD/eingang_einzel/ET20/ 2>&1 | head
```
Sieht der Container die Dateien? Falls nein → Volume-Mount-Problem,
beim Operator melden.

### Schritt 3 — Output verifizieren

Sobald der Run "DONE" im Log meldet:

```bash
ls -lah /volume1/SDD/output_einzel/
ffprobe -v error -show_entries format=duration,size,bit_rate \
  -show_entries stream=codec_name,codec_type,width,height,r_frame_rate \
  -of default=noprint_wrappers=1 \
  /volume1/SDD/output_einzel/*.mp4
```

Erwartet:
* Eine `.mp4`-Datei nach Schema `2026 STS2 T20 <Turniername> Einzel.mp4`
  (Turniername kommt aus `filename_constants.turniername` in der Config —
  ggf. `T` als Default)
* `duration` ~ 15 × 30 min = ca. **27000 s** (450 min)
* `codec_name=h264`, `codec_type=video` und ein zweites Stream `aac`/`audio`
* Keine Drops oder Warnings von `ffprobe`

### Schritt 4 — API-Layer der GUI quervalidieren

```bash
curl -s http://localhost:8080/api/state/einzel | python3 -m json.tool | head -50
```

Erwartet: aktueller Status zeigt `state: IDLE` oder `DONE`, die Files-Liste
enthaelt den Output. Falls 401: Login-Cookie wird benoetigt — dann
ueberspringen, der Operator prueft die UI selbst im Browser.

## Bericht zurueck an den Operator

Strukturiert, in dieser Form:

```
TEST-REPORT — ET20 End-to-End (Datum/Uhrzeit)

EINGANG
- Dateien: 15 .MP4, X GB
- Quiet seconds: Y

PIPELINE-PHASEN (Dauer in Sekunden)
- MOVING:     X.XX
- ORGANIZING: X.XX
- RENAMING:   X.XX
- MERGING:    X.XX
- TOTAL:      X.XX

OUTPUT
- Datei: <name>.mp4
- Groesse: X GB
- Dauer laut ffprobe: X s (~ Y min)
- Codecs: video=h264, audio=aac
- Auffaelligkeiten: <keine | beschreiben>

EMPFEHLUNG
- PR #13 (Luecke-B-Fix) bereit zum Merge? <ja | nein, weil ...>
- Naechster Schritt: <z.B. Upload-Test mit dem entstandenen Output | Luecke A angehen | ...>
```

## Entscheidungen, die ICH (NAS-Claude) NICHT selbststaendig treffe

* PR #13 mergen — bleibt beim Operator.
* Container neu bauen / Branch wechseln — bleibt beim Operator.
* Originale loeschen / Tiering manuell triggern — bleibt beim Operator.
* `git commit`/`push` auf der NAS — nur nach explizitem Operator-OK.

## Wenn etwas grundsaetzlich schief geht

Stoppe bei der ersten ungeklaerten Anomalie und frage den Operator. Lieber
einmal kurz nachhaken als blind weiter machen. Beispiele:
* `ffmpeg` bricht mit Codec-Inkompatibilitaet ab (Panasonic-Material
  inkonsistent zwischen Clips?)
* Output-Dauer weicht stark von 15×30 min ab
* Watcher springt nicht an, obwohl der Container die Dateien sieht

Cloud-Claude (Verfasser dieses Briefings) kann die NAS **nicht** sehen.
Berichte gehen via Operator zurueck.
