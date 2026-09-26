## Problem und Ergebnis

Welches konkrete Problem löst dieser PR, und was kann man danach beobachten?

## Scope

- Baseline-Commit (voller SHA):
- Zugehöriges Issue / abhängiger PR:
- Geänderte Dateien und berührte Sicherheitsgrenzen:

## Prüfung

Nur tatsächlich ausgeführte Prüfungen eintragen; nicht Geprüftes als `UNKNOWN`.

| Prüfung | Ergebnis / Beleg |
| --- | --- |
| Tests (`unittest discover -s tests`) | |
| Demo (`./scripts/demo.sh`) | |
| Refusal-Guard der geänderten Module | |
| CI auf dem aktuellen Head | |
| Gemini-Review (bei DB-PRs: „Gemini-Freigabe DB: ja“) | |

## Für die Wissensdatenbank
- Was ist jetzt anders: <1–3 Sätze>
- Entscheidungen: <keine | Entscheidung, Begründung>
- Geänderte Grenzen (SECURITY.md): <keine | welche>
- Messwerte: <Anzahl Tests, Ergebnis der Demo>
- Nächster Schritt: <Vorschlag>

## Offene Punkte

Bekannte Grenzen und nötige Entscheidungen von Kaan. Wer mergen darf und wann, steht in
`docs/COLLABORATION.md`.
