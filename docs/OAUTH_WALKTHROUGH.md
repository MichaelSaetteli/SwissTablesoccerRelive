# Begleitete Anleitung: YouTube-OAuth + Generalprobe (für Claude Chat)

> **Zweck dieses Dokuments.** Es ist als **Briefing für einen Chat-Assistenten
> (z. B. Claude Chat)** gedacht, der den Operator Schritt für Schritt durch
> die einmalige YouTube-Einrichtung und die erste Generalprobe begleitet.
> Der Assistent hat **keinen Zugriff auf das Repo** und **kennt die
> Projekt-Historie nicht** — darum bringt dieses Dokument allen nötigen
> Kontext selbst mit. Tiefere Details stehen in
> [`YOUTUBE_SETUP.md`](YOUTUBE_SETUP.md); dieses Dokument ist der
> interaktive Begleiter (GitHub-Issue #3).

> **So benutzt du es:** Kopiere dieses ganze Dokument als erste Nachricht in
> einen frischen Chat und schreibe darunter z. B. *„Begleite mich Schritt
> für Schritt durch diese Anleitung. Stelle mir immer nur den nächsten
> Schritt, warte auf meine Rückmeldung und prüfe sie, bevor es weitergeht."*

---

## 0. Projekt-Kontext (für den Assistenten)

- **Projekt:** SwissTablesoccerRelive — eine halbautomatische Video-Pipeline
  für Tischfussball-Turniere, läuft als Docker-Container auf einer Synology
  DS1522+ NAS. Sie concateniert Roh-Videos per FFmpeg (Stream-Copy, kein
  Re-Encoding) und lädt sie über die **YouTube Data API v3** in den
  **Swisstablesoccer-Kanal** hoch.
- **Stand:** Pipeline, Web-Dashboard und der komplette Upload-Code sind
  fertig und laufen produktiv auf dem NAS (Port **8080**). **Aber:** der
  Upload-Pfad ist **noch nie gegen echtes YouTube gelaufen.** Genau das ist
  das Ziel dieser Anleitung.
- **Warum manuell:** Das OAuth-Token kann nur **einmalig auf einem Rechner
  mit Browser** erzeugt werden (die NAS hat keinen Browser). Danach läuft
  der Upload vollautomatisch; die NAS refresht das Token selbst.
- **Sprache im UI:** Deutsch (Schweiz, kein „ß", immer „ss").
- **Privacy-Default:** Jeder Upload ist zuerst `private` — der Operator
  schaltet im YouTube-Studio manuell auf öffentlich.

### Annahmen / Platzhalter (vor Beginn ersetzen)
Der Operator soll diese Werte zu Beginn nennen; bis dahin mit den
Platzhaltern arbeiten:
- **NAS-IP:** `<NAS_IP>` (Beispiel früher: `192.168.1.159`)
- **NAS-SSH-User:** `<NAS_USER>` (Beispiel früher: `saetteli`)
- **Config-Verzeichnis auf der NAS:** `/volume1/SDD/video-pipeline-config`
- **Web-UI:** `http://<NAS_IP>:8080/`
- **Google-Konto**, das den YouTube-Kanal besitzt/verwaltet.

---

## 1. Voraussetzungen prüfen (Checkpoint A)

Auf einem **Laptop mit Browser** (Mac/Windows/Linux):
- Python 3.9+ und `pip`
- Git, ein SSH/SCP-Client
- Zugang zum Google-Konto des Kanals

Befehle:
```bash
git clone https://github.com/MichaelSaetteli/SwissTablesoccerRelive.git
cd SwissTablesoccerRelive
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
**Rückmeldung an den Assistenten:** „pip install ok" oder die Fehlermeldung.

---

## 2. Google Cloud Projekt + API (Checkpoint B)

1. <https://console.cloud.google.com/> → neues Projekt
   `swisstablesoccer-pipeline` anlegen, auswählen.
2. „APIs und Dienste" → „Bibliothek" → **„YouTube Data API v3"** →
   **Aktivieren**.

**Rückmeldung:** „API aktiviert" (oder wo es hakt).

---

## 3. OAuth-Consent-Screen + Credentials (Checkpoint C)

1. „APIs und Dienste" → „Anmeldedaten".
2. „Zustimmungsbildschirm konfigurieren":
   - Nutzertyp **Extern**
   - App-Name `Swisstablesoccer Video-Pipeline`, Support-/Kontakt-E-Mail
   - Scopes-Schritt: **nichts** auswählen (der Code fordert sie an)
   - **Testnutzer:** die eigene E-Mail eintragen — **wichtig**, sonst
     scheitert die Anmeldung im „Testing"-Status.
3. „Anmeldedaten erstellen" → **OAuth-Client-ID** → Anwendungstyp
   **Desktop** → Name `pipeline-desktop` → Erstellen.
4. **JSON herunterladen** → lokal als `client_secrets.json` im Repo-Root
   speichern.

**Häufiger Stolperstein:** Vergisst man den Testnutzer, kommt in Schritt 4
ein `403 access_denied`. Lösung: eigene E-Mail als Testnutzer eintragen.

**Rückmeldung:** „client_secrets.json liegt im Repo-Root."

---

## 4. Welcher Kanal? (kurz prüfen)

Das **Google-Konto, mit dem man sich gleich anmeldet, bestimmt den
Ziel-Kanal.**
- Hat Swisstablesoccer einen eigenen Owner-Account → den verwenden.
- Ist der eigene Account **Manager** eines Brand-Accounts → prüfen unter
  <https://www.youtube.com/account> → „Konto wechseln": erscheint der
  Swisstablesoccer-Kanal? Wenn ja, geht der eigene Account.

Bei Unsicherheit: einfach starten — falls in der Verifikation (Schritt 7)
der falsche Kanal erscheint, Token löschen und mit dem richtigen Login
wiederholen.

---

## 5. OAuth-Flow ausführen (Checkpoint D)

Im Repo-Root, venv aktiv:
```bash
python -m youtube.oauth_setup ./client_secrets.json ./youtube_token.json
```
Ablauf:
1. Lokaler Webserver startet, Browser öffnet die Google-Anmeldung.
2. **Richtigen Account** wählen.
3. Warnung „App nicht überprüft" → **Erweitert** → **Weiter zu … (unsicher)**.
4. Berechtigungen bestätigen (`youtube.upload`, `youtube`).
5. Bei Brand-Account: im „Konto wechseln"-Dialog den **Swisstablesoccer-Kanal**
   wählen, nicht den persönlichen.
6. Browser: „The authentication flow has completed." / Terminal:
   `Token saved to ./youtube_token.json`.

Schnelltest, dass das Token brauchbar ist (zeigt Kanal-Titel):
```bash
python -c "from youtube.oauth_setup import load_credentials, build_youtube_service; \
from pathlib import Path; c=load_credentials(Path('youtube_token.json')); \
s=build_youtube_service(c); \
print(s.channels().list(part='snippet', mine=True).execute()['items'][0]['snippet']['title'])"
```
**Rückmeldung:** der ausgegebene Kanal-Titel — der Assistent prüft, ob es
der **Swisstablesoccer-Kanal** ist. Falls falsch → `rm youtube_token.json`
und Schritt 5 mit dem richtigen Login wiederholen.

---

## 6. Token auf die NAS kopieren (Checkpoint E)

```bash
scp ./youtube_token.json \
  <NAS_USER>@<NAS_IP>:/volume1/SDD/video-pipeline-config/youtube_token.json
```
Auf der NAS (SSH) für root les-/schreibbar machen (Container läuft als root):
```bash
sudo chown root:root /volume1/SDD/video-pipeline-config/youtube_token.json
sudo chmod 600       /volume1/SDD/video-pipeline-config/youtube_token.json
```
**Sicherheit:** Die Datei enthält einen unbegrenzt gültigen Refresh-Token.
Niemals ins Git einchecken (ist in `.gitignore`). Bei Verlust/Leak: in
Google → Sicherheit → Drittanbieter-Apps widerrufen und Schritt 5
wiederholen.

**Rückmeldung:** „Token auf NAS, Permissions gesetzt."

---

## 7. Generalprobe: kleiner echter Upload (Checkpoint F)

> **Wichtig:** zuerst mit **wenigen kurzen Clips** testen, NICHT mit 1 TB.

Mini-Clip erzeugen (Laptop oder im NAS-Container):
```bash
ffmpeg -f lavfi -i "color=c=red:size=320x240:duration=5:rate=15" \
    -c:v libx264 -pix_fmt yuv420p test.mp4
```
Im Web-UI `http://<NAS_IP>:8080/`:
1. Login.
2. Tab **„Einzel"** (kleinere Disziplin für den Test).
3. **YouTube-Konfiguration** ausfüllen:
   - Turniername `OAuth-Smoke-Test`
   - Titel-Template `{turniername} {kamera}`
   - „Neu anlegen" mit Titel `OAuth-Test-Playlist` → Speichern.
4. Test-Video mit Filename-Schema nach `output_einzel/` legen, z. B.
   `2026 STS2 T01 OAuth-Smoke-Test Einzel.mp4`.
5. „Vorschau aktualisieren" → generierter Titel erscheint.
6. **„Upload starten"** → Status `uploading` → nach ~30–60 s `done`.

In <https://studio.youtube.com> prüfen:
- Richtiger Kanal (Swisstablesoccer-Avatar oben rechts)?
- Neues Video sichtbar, Status **privat**?
- Playlist `OAuth-Test-Playlist` angelegt, Video drin?

**Rückmeldung:** Screenshot/Beschreibung des Ergebnisses + ob der Kanal
stimmt.

---

## 8. Quota beachten

- Standard: **10'000 Units/Tag**, ein Upload kostet **1'600** → praktisch
  **~6 Uploads/Tag**.
- Für ein echtes Turnier (z. B. 30 Videos) **Quota-Erhöhung beantragen**
  (kostenlos, 2–7 Werktage) oder Uploads auf mehrere Tage verteilen.
  Begründung im Antrag: „Verein lädt nur Aufzeichnungen eigener Turniere
  auf den eigenen Kanal — keine Inhalte Dritter."

---

## 9. Fehler-Tabelle (für schnelle Hilfe)

| Symptom | Ursache / Lösung |
|---|---|
| `403 access_denied` beim Login | Eigene E-Mail nicht als **Testnutzer** im Consent-Screen → eintragen. |
| `RefreshError: invalid_grant` | Token abgelaufen/widerrufen → Schritt 5 wiederholen. |
| Upload landet im **falschen Kanal** | Falscher Login/Brand-Account → `rm youtube_token.json`, Schritt 5 mit richtigem Kanal. |
| `quotaExceeded` | Tageslimit erschöpft → morgen weiter oder Quota-Erhöhung. |
| `401 Unauthorized` beim Upload | Token-Datei für root nicht lesbar / Pfad falsch → `ls -la /volume1/SDD/video-pipeline-config/youtube_token.json`. |
| Upload-Status bleibt auf `idle` | Web-UI: liegt das Video wirklich in `output_einzel/` mit korrektem Filename-Schema? „Vorschau aktualisieren" gedrückt? |

---

## 10. Definition of Done (Issue #3)

- [ ] `youtube_token.json` auf der NAS, Token-Refresh funktioniert
- [ ] Ein echter Upload erscheint **privat** im richtigen Kanal (Studio)
- [ ] Test-Playlist angelegt, Video zugeordnet
- [ ] Quota-Verbrauch beobachtet, im Tagesbudget
- [ ] Beim Test gefundene Stolpersteine als Folge-Notiz festhalten

> Wenn alle Haken gesetzt sind: der Kern ist bewiesen. Danach sind die
> nächsten sinnvollen Schritte Benachrichtigungen (#4) und der
> Qualitäts-Check nach Merge (#5).

---

### Was der Operator dem Assistenten zu Beginn mitteilen sollte
1. NAS-IP und SSH-User (für `<NAS_IP>` / `<NAS_USER>`).
2. Ob der YouTube-Kanal ein eigener Account oder ein Brand-Account ist.
3. Betriebssystem des Laptops (Mac/Windows/Linux) — für exakte Befehle.
