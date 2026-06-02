# STS-Upload — Anleitung fuer Operatoren

Das **STS-Upload**-Tool laedt die SD-Karten eines Turniers auf die NAS.
Du gibst keine Pfade und keine Tischnummern ein — der Tisch kommt aus dem
**Namen der Karte** (`E01` → Tisch 1). Du waehlst nur die Disziplin und
klickst.

## Voraussetzung

Die SD-Karten tragen einen **Datentraegernamen** wie `E01`, `E02` …
(Einzel) bzw. `D01`, `D02` … (Doppel) — daraus liest das Tool die
Tischnummer. Eine Karte ohne Nummer im Namen zeigt das Tool gesperrt an;
melde sie dem Admin (Karte umbenennen).

## Der Ablauf

1. **Disziplin waehlen** — oben „Einzel" oder „Doppel" einstellen, je
   nachdem welchen Schwung Karten du gerade einliest. (Schuetzt davor, dass
   Einzel-Karten versehentlich als Doppel landen.)
2. **Karten einstecken** — eine oder mehrere (bis 24 gleichzeitig, je nach
   Reader). Du kannst jederzeit weitere nachstecken.
3. **Klick „SD-Karten einlesen"** — das Tool findet die Karten und zeigt
   jede als Zeile (Tisch, Disziplin, Datum). Passiert auch automatisch beim
   Einstecken.
4. **Klick „Hochladen starten"** — alle bereiten Karten werden parallel
   hochgeladen und geprueft.
5. **Klick „Alle verifizierten freigeben"** — gibt die fertig geprueften
   Karten frei; ab da laeuft die Pipeline auf der NAS.

> **Eine Karte gehoert zur anderen Disziplin?** Stell ihre Disziplin in der
> Zeile um — der Rest des Schwungs bleibt unveraendert.

> **Nur 2 Klicks?** Setz das Hakchen **„Auto-Release nach Verifikation"**
> oben. Dann wird jede Karte nach dem Pruefen automatisch freigegeben —
> der Freigabe-Klick faellt weg.

Einzelne Karten freigeben: Hakchen in der Zeile setzen und unten
**„Markierte freigeben"** klicken.

## Was die Statusanzeige bedeutet

| Symbol | Status | Bedeutung |
|---|---|---|
| ⏳ wartet | bereit | Karte erkannt, Upload noch nicht gestartet |
| ▶ hochladen | laeuft | Dateien werden uebertragen (x/n Dateien) |
| ✓ verifiziert | geprueft | Alle Dateien da und korrekt — bereit zur Freigabe |
| ✅ released | fertig | Freigegeben, Pipeline laeuft |
| ⚠ Fehler | Problem | Uebertragung unvollstaendig — siehe unten |
| ⛔ unterbrochen | pausiert | Karte wurde entfernt — wieder einstecken |
| 🔒 gesperrt | gesperrt | Kartenname ohne Tischnummer, oder kein aktives Turnier |
| ⚠ (Datum-Spalte) | Achtung | Aufnahme-Ordner liegen **mehr als 3 Tage** auseinander — evtl. alte Daten drauf. Zeile gelb. **Doppelklick auf die Zeile** oeffnet die Ordner-Auswahl (Default: die neuesten an, alte aus). |

**Disziplin pro Karte korrigieren:** Stimmt bei einer Karte die Disziplin
nicht, stell sie direkt in der **Disziplin-Spalte** (Dropdown) um — geht nur,
solange die Karte noch nicht hochgeladen ist.

**Entfernen-Spalte:**
* **🔌 sicher entfernbar** — die Karte darf raus (verifiziert oder
  freigegeben).
* **⛔ Nicht entfernen — Upload laeuft** — Karte stecken lassen!

## Wenn etwas schiefgeht

* **Karte zu frueh entfernt:** grosse rote Warnung erscheint, die Karte
  geht auf **unterbrochen**. Einfach **wieder einstecken** und nochmal
  **„Hochladen starten"** — der Upload macht dort weiter, wo er war (faengt
  **nicht** von vorne an).
* **⚠ Fehler bei einer Karte:** Karte stecken lassen, **„Hochladen starten"**
  erneut klicken — das Tool laedt die fehlenden Dateien nach und prueft
  erneut. (Details zum Fehler stehen im Tooltip der Zeile.)
* **Tool abgestuerzt oder geschlossen:** neu starten. Oben erscheint ein
  Hinweis, wenn Karten aus der letzten Sitzung offen waren. Karten
  einstecken, **„Hochladen starten"** — es wird fortgesetzt.
* **Netzwerk kurz weg:** wie „Karte entfernt" — der Upload pausiert und
  setzt fort, sobald die NAS wieder erreichbar ist.

## Wann bist du fertig?

Wenn alle erwarteten Karten freigegeben sind, erscheint ein **grosser
gruener Hinweis**. Die Kopfzeile zeigt jederzeit den Stand, z.B.
„Einzel — 18 / 30 Karten verifiziert · 4 hochladen · 8 wartend". Fehlt am
Ende eine Zahl, fehlt eine Karte — nachschauen, ob noch eine im Reader
steckt oder vergessen wurde.

## Anmelden

Beim Start fragt das Tool einmalig nach **Server**, **Benutzer** und
**Passwort** (bekommst du vom Admin). Danach keine Eingaben mehr.
