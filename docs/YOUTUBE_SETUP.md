# YouTube-Upload einrichten

Diese Anleitung beschreibt den einmaligen Setup-Aufwand, damit die
Pipeline Videos im Namen des **Swisstablesoccer-YouTube-Kanals**
hochladen kann.

> Wichtig: Diesen Schritt muss du **einmalig auf einem Laptop mit
> Browser** ausführen. Die DS1522+ hat keinen Browser; das resultierende
> Token wird nach Abschluss aufs NAS kopiert. Anschließend läuft der
> Upload vollautomatisch (mit halbautomatischer Freigabe im Web-UI).

---

## 1. Voraussetzungen

* Zugang zum **Google-Konto, das den Swisstablesoccer-YouTube-Kanal
  besitzt oder verwaltet** (siehe Abschnitt 3 für die Wahl).
* Ein Laptop (Mac/Windows/Linux) mit:
  - Python 3.9+ und `pip`
  - Web-Browser
  - SSH/SCP-Client (für den Token-Transfer aufs NAS)
* Repo lokal geklont:
  ```bash
  git clone -b claude/read-briefing-start-build-2wOz7 \
    https://github.com/MichaelSaetteli/SwissTablesoccerRelive.git
  cd SwissTablesoccerRelive
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  ```

---

## 2. Google API Scopes

Die App fordert zwei Scopes an (definiert in
`youtube/oauth_setup.py::SCOPES`):

| Scope | Wofür |
|---|---|
| `https://www.googleapis.com/auth/youtube.upload` | Video hochladen |
| `https://www.googleapis.com/auth/youtube` | Playlists anlegen, Videos zu Playlists hinzufügen |

Das sind die offiziellen YouTube Data API v3 Scopes. Keine zusätzlichen
Berechtigungen werden verlangt — insbesondere kein Lesezugriff auf
private Daten ausserhalb des eigenen Kanals.

---

## 3. Welcher Account? — Kanal-Owner vs. Manager

Das **Google-Konto, mit dem du dich beim OAuth-Flow anmeldest, bestimmt
in welchen Kanal hochgeladen wird**. Es gibt zwei Wege:

### 3a. Kanal-Owner-Account verwenden (empfohlen)

Falls Swisstablesoccer einen **dedizierten Google-Account** besitzt, der
direkter Owner des YouTube-Kanals ist: diesen Account verwenden.
Vorteile:
* OAuth ist trivial — der Account hat unmittelbar volle Rechte
* Account-Wechsel im persönlichen Browser nicht nötig
* Pipeline ist von persönlichen Account-Aktivitäten entkoppelt

Nachteil: Passwort und 2FA müssen mit den Vereins-Verantwortlichen
geteilt sein.

### 3b. Brand Account / Channel Manager

Wenn der Swisstablesoccer-Kanal ein **Brand-Account** ist (oder zu einem
Brand-Account gehört) und dein **persönlicher Google-Account** als
**Manager** des Kanals eingetragen ist: dein persönlicher Account kann
verwendet werden.

So prüfen ob das so eingerichtet ist:
1. https://www.youtube.com/account (mit persönlichem Account angemeldet)
2. „Konto wechseln" → erscheint der Swisstablesoccer-Kanal in der Liste?
   * **Ja** → Manager-Setup, persönlicher Account funktioniert
   * **Nein** → entweder 3a verwenden oder das Brand-Account-Setup
     korrigieren

Wenn unklar: zuerst Variante 3b versuchen — falls beim Upload-Test
(Schritt 7) der falsche Kanal verwendet wird, auf 3a wechseln.

---

## 4. Google Cloud Project anlegen

1. Browser: <https://console.cloud.google.com/>
2. Oben links das Projekt-Dropdown → **„Neues Projekt"**
3. **Projektname**: `swisstablesoccer-pipeline` (oder beliebig)
4. **Organisation**: leer lassen falls keine vorhanden
5. **„Erstellen"** klicken, ~10 Sekunden warten
6. Sicherstellen, dass das neue Projekt im Dropdown ausgewählt ist

---

## 5. YouTube Data API v3 aktivieren

1. Linkes Menü: **„APIs und Dienste" → „Bibliothek"**
2. Suche nach **„YouTube Data API v3"** → anklicken
3. **„Aktivieren"** klicken
4. ~30 Sekunden warten

Nach der Aktivierung siehst du das Quota-Dashboard:

* **Default**: 10'000 Units / Tag
* **Pro Video-Upload**: 1'600 Units
* **Praktisch**: ca. **6 Videos / Tag** ohne Quota-Erhöhung

Für ein Turnier mit z.B. 20–40 Match-Videos: rechtzeitig **Quota-
Erhöhung beantragen** (über „Kontingente und Systemlimits"). Genehmigung
dauert üblicherweise 2–7 Werktage; Google fragt nach dem Use Case
(Beschreibung: „Verein lädt Aufzeichnungen seiner Turniere auf den
eigenen Kanal — keine Inhalte Dritter").

---

## 6. OAuth-2.0-Credentials erstellen

1. Linkes Menü: **„APIs und Dienste" → „Anmeldedaten"**
2. Falls noch keiner: **„Zustimmungsbildschirm konfigurieren"**
   - Nutzertyp: **„Extern"** (auch für persönlichen Account OK)
   - App-Name: `Swisstablesoccer Video-Pipeline`
   - Nutzer-Support-E-Mail: deine Adresse
   - Entwicklerkontakt: gleiche Adresse
   - **Scopes** Schritt: nichts anwählen, weiter (Scopes werden vom
     Code direkt angefordert)
   - **Testnutzer**: dein E-Mail-Account hier eintragen (sehr wichtig!
     Sonst kannst du dich später nicht anmelden, solange die App im
     „Testing"-Status ist)
   - Zusammenfassung → Speichern
3. **„Anmeldedaten erstellen" → „OAuth-Client-ID"**
   - **Anwendungstyp**: **„Desktop"**
   - **Name**: `pipeline-desktop`
   - Erstellen
4. Im erscheinenden Dialog **„JSON herunterladen"** klicken
5. Datei lokal als `client_secrets.json` speichern (Ort merken)

> Sicherheit: Diese Datei ist **kein Geheimnis** im strengen Sinn
> (Public-Client-Flow), aber sie identifiziert dein Projekt. Nicht in
> öffentliche Repos einchecken.

---

## 7. OAuth-Flow ausführen (auf dem Laptop)

Im Repo-Root:

```bash
source .venv/bin/activate
python -m youtube.oauth_setup ./client_secrets.json ./youtube_token.json
```

Was passiert:

1. Lokaler Webserver startet auf einem freien Port (z.B. 8888)
2. Browser öffnet sich automatisch mit der Google-Anmeldung
3. **Wähle den richtigen Account** (Swisstablesoccer-Owner bzw. einen
   Account, der den Kanal verwalten darf — siehe Abschnitt 3)
4. Warnung „App nicht überprüft" → **„Erweitert"** → **„Weiter zu …
   (unsicher)"** klicken
5. Berechtigungen bestätigen (`youtube.upload`, `youtube` Verwaltung)
6. „Konto wechseln" Dialog falls Brand-Account: den Swisstablesoccer-Kanal
   auswählen, **NICHT** den persönlichen
7. Browser zeigt „The authentication flow has completed."
8. Im Terminal: `Token saved to ./youtube_token.json`

Datei prüfen — sollte etwa so aussehen (gekürzt):

```json
{
  "token": "ya29.a0AfH6...",
  "refresh_token": "1//0e...",
  "token_uri": "https://oauth2.googleapis.com/token",
  "client_id": "1234567890-xxx.apps.googleusercontent.com",
  "client_secret": "GOCSPX-...",
  "scopes": [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
  ],
  "expiry": "2026-05-19T15:30:00Z"
}
```

`refresh_token` ist unbegrenzt gültig (solange du den Zugriff nicht
manuell in deinem Google-Konto widerrufst). Der `expiry`-Zeitstempel
am Access-Token läuft alle 60 Minuten ab; die Pipeline refresht
automatisch im Hintergrund.

---

## 8. Token aufs NAS kopieren

```bash
scp ./youtube_token.json \
    saetteli@192.168.1.159:/volume1/SDD/video-pipeline-config/youtube_token.json
```

Auf der NAS in DSM Aufgabenplaner (oder via SSH als root) noch die
Permissions anpassen — Container läuft als root, also für root
beschreibbar machen:

```bash
chown root:root /volume1/SDD/video-pipeline-config/youtube_token.json
chmod 600       /volume1/SDD/video-pipeline-config/youtube_token.json
```

> Wichtig: `youtube_token.json` enthält einen **Refresh-Token mit
> unbegrenzter Lebensdauer**. Falls die Datei je in fremde Hände gerät:
> in Google → Sicherheit → Drittanbieter-Apps → „Swisstablesoccer
> Video-Pipeline" widerrufen, und Schritt 7 wiederholen.

---

## 9. Verifikation: ein Test-Upload

Erst ein **kleines, privates** Test-Video hochladen, bevor du auf
1-TB-Turniere losgehst.

Auf dem Laptop (oder im NAS-Container) ein winziges Video präparieren:

```bash
# 5-Sekunden-Clip generieren (analog dem e2e-Fixture)
ffmpeg -f lavfi -i "color=c=red:size=320x240:duration=5:rate=15" \
    -c:v libx264 -pix_fmt yuv420p test.mp4
```

Im Web-UI:

1. http://192.168.1.159:8080/ → Login
2. Tab „Einzel" auswählen (kleinere Disziplin für den Test)
3. „Datei-Benennung" überprüfen
4. „YouTube-Konfiguration" ausfüllen:
   - Turniername: `OAuth-Smoke-Test`
   - Titel-Template: `{turniername} {kamera}`
   - „Neu anlegen" mit Titel: `OAuth-Test-Playlist`
   - Speichern
5. Test-Video manuell ins `output_einzel/` legen mit Filename-Schema:
   `2026 STS2 T01 OAuth-Smoke-Test Einzel.mp4`
6. „Vorschau aktualisieren" → der generierte Titel sollte erscheinen
7. „Upload starten" → der Upload-Status sollte auf `uploading` springen
8. Nach ~30–60 Sekunden: `done`

In YouTube Studio (<https://studio.youtube.com>) prüfen:

* Richtiger Kanal aktiv (oben rechts der Swisstablesoccer-Avatar,
  nicht dein persönlicher)?
* Neues Video sichtbar in der Liste der Inhalte?
* Status „Eingeschränkt" / „Privat" (default `privacyStatus: private`)?
* Playlist `OAuth-Test-Playlist` neu angelegt, Test-Video drin?

Falls **falscher Kanal** verwendet wurde: Token löschen, Schritt 7
wiederholen und beim Account-Wahl-Dialog den richtigen Kanal anklicken.

---

## 10. Häufige Probleme

| Problem | Lösung |
|---|---|
| `RefreshError: invalid_grant` | Token abgelaufen oder widerrufen → Schritt 7 wiederholen |
| `quotaExceeded` | Tageskontingent erschöpft (10'000 Units) → bis morgen warten oder Quota-Erhöhung beantragen |
| `App nicht überprüft` Warnung | Solange die App im Testing-Status bleibt: normal. Im OAuth-Consent-Screen unter „Veröffentlichung" lassen sich auch andere Tester einladen |
| Upload landet im falschen Kanal | Mit dem Brand-Account-Setup verwechselt → Abschnitt 3 lesen, ggf. Schritt 7 mit anderem Login wiederholen |
| `youtube_token.json` lost | Backup von `/volume1/SDD/video-pipeline-config/` einspielen oder Schritt 7 wiederholen |
| 401 Unauthorized während Upload | Token-Datei nicht für root lesbar oder Pfad falsch — `ls -la /volume1/SDD/video-pipeline-config/youtube_token.json` prüfen |

---

## 11. Was die App mit dem Token macht

* Beim Container-Start: nichts (lazy load)
* Vor jedem Upload-Batch: `oauth_setup.load_credentials()`
  - Token vom Disk lesen
  - Wenn `expired and refresh_token` vorhanden → automatisch refreshen
  - Aktualisierte JSON zurückschreiben (atomar via temp + rename)
  - Falls Refresh fehlschlägt: Upload-Batch bricht mit klarer
    Fehlermeldung im Web-UI ab
* Während des Uploads: `youtube_uploader.upload_video()` benutzt einen
  resumable-Upload-Stream — bei Netzwerk-Unterbruch wird automatisch
  fortgesetzt
* Standard-Privacy: alle frischen Uploads sind `privacyStatus: private`
  und nur im YouTube Studio sichtbar, bis du sie dort manuell freigibst

---

## 12. Quota-Übersicht (zur schnellen Kalkulation)

| Operation | Quota |
|---|---|
| `videos.insert` (Upload) | 1'600 Units |
| `playlists.insert` (Playlist anlegen) | 50 Units |
| `playlistItems.insert` (Video zu Playlist) | 50 Units |
| Standard-Tageslimit | 10'000 Units |
| Praktischer Durchsatz | ~6 Videos / Tag |

Bei einem Turnier mit 30 Match-Videos:
* 30 × 1'600 (Uploads) + 1 × 50 (Playlist) + 30 × 50 (Playlist-Items) = **49'550 Units**
* → Quota-Erhöhung beantragen (Faktor ~5–10×) oder Uploads auf mehrere
  Tage verteilen.

Eine Quota-Erhöhung-Beantragung ist kostenlos, dauert ein paar
Werktage. Im Antrag relevant erwähnen:
* App ist nur für **eigene Kanal-Verwaltung** (kein Public-Service)
* Erwartete Volumen: z.B. „1× pro Monat ~40 Uploads"
* Speech-/Sport-Aufzeichnungen, kein YouTube-Partner-Programm.

---

Wenn dieser Setup steht: die Pipeline lädt im Anschluss vollautomatisch
hoch, sobald du im Web-UI bei einer Disziplin **„Upload starten"**
klickst.
