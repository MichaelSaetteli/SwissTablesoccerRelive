# STS-Upload — Anleitung fuer Operatoren

Das **STS-Upload**-Tool laedt die SD-Karten eines Turniers auf die NAS.
Es ist so gebaut, dass man **nichts falsch machen kann**: du gibst keine
Pfade, keine Tischnummern und kein Turnier ein — das Tool liest alles von
der Karte. Du klickst nur.

## Voraussetzung

Die SD-Karten sind vom Admin **vor dem Turnier beschriftet** worden (jede
Karte traegt eine kleine Markierungsdatei mit Tisch + Disziplin). Karten
ohne Markierung zeigt das Tool an, kann sie aber **nicht** hochladen —
melde solche Karten dem Admin.

## Der Ablauf in 3 Klicks

1. **Karten einstecken** — eine oder mehrere (bis 24 gleichzeitig, je nach
   Reader). Du kannst jederzeit weitere nachstecken.
2. **Klick 1 — „SD-Karten einlesen"** — das Tool findet die Karten, liest
   die Markierung und zeigt jede Karte als Zeile. (Das passiert auch
   automatisch, wenn du eine Karte einsteckst.)
3. **Klick 2 — „Hochladen starten"** — alle bereiten Karten werden parallel
   hochgeladen und geprueft.
4. **Klick 3 — „Alle verifizierten freigeben"** — gibt die fertig
   geprueften Karten frei; ab da laeuft die Pipeline auf der NAS.

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
| 🔒 gesperrt | gesperrt | Karte ohne Markierung oder falsches Turnier |

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
