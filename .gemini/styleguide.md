# Review-Leitfaden für Gemini Code Assist

GeniusNew ist ein Zero-Trust-Agentensystem in Python. Regeln: `AGENTS.md`, Rollen:
`docs/COLLABORATION.md`. Reviews bitte **auf Deutsch**.

## Schweregrad

- **Critical / High: blockiert den Merge.** Codex behebt den Befund oder begründet im
  Thread, warum er nicht zutrifft.
- **Medium / Low: Vorschlag.** Er darf offen bleiben.

## Immer prüfen

1. **Fail closed:** Gibt es ein stilles `return`, ein breites `except`, einen Default
   statt einer Ablehnung? Unklare Eingaben müssen mit `ContractError` abgelehnt werden.
   *High*
2. **Signaturrollen:** Hält eine prüfende Instanz (Gateway, Ergebnisprüfung, Anker,
   Audit-Prüfung) einen privaten Schlüssel, ein Root-Secret oder eine
   `*Signer`/`*Authority`-Instanz? *Critical*
3. **Refusal-Guard:** Hat jede neue Ablehnung einen Test, der ihr Fehlen bemerkt? Steht
   ein neues Modul mit Ablehnungen in `GUARDED` in `scripts/refusals.py`? *High*
4. **Grenzen:** Ändert der PR eine Grenze aus `SECURITY.md`, ohne den offen haltenden
   Test (`…_and_this_is_the_boundary`) und die Tabelle anzupassen? *High*
5. **Leaks:** Gelangen Nutzdaten, Schlüssel oder Tokens in Audit-Einträge, Logs,
   Fehlermeldungen oder Tests mit echten Werten? *Critical*
6. **Tests:** Wird ein Test übersprungen, deaktiviert oder abgeschwächt? *Critical*
7. **Abhängigkeiten:** Ist eine neue Abhängigkeit hinzugekommen, und ist sie in
   `requirements.txt` hash-gepinnt? Ohne Kaans OK gilt das als *High*.
8. **Wissensblock:** Enthält die Beschreibung eines Codex-PRs den ausgefüllten Block
   „## Für die Wissensdatenbank“? Fehlt er: *High*. Fehlt darin der Beleg (Head-SHA,
   Test oder Befehl): *Medium*. Ändert ein Codex-PR
   `docs/STATUS.md` oder `docs/DECISIONS.md`? Diese Dateien gehören Gemini: *Medium*.

9. **DB-PRs** (Speicher-Code, Schema, Migrationen, `docs/DATABASE.md`): Liegt der
   Anker in derselben Datenbank oder unter denselben Zugangsdaten wie die Audit-Kette?
   *Critical*. Startet der Dienst bei beschädigtem oder fehlendem Speicher still bei
   null? *Critical*. Stehen Zugangsdaten oder DB-Dateien im Repository? *Critical*.
   Weicht das Schema von `docs/DATABASE.md` ab? *High*. Am Ende jedes DB-Reviews steht
   „Gemini-Freigabe DB: ja“ oder „Gemini-Freigabe DB: nein“ mit Grund.

## Nicht bemängeln

- Deutsche Doku neben englischem Code: Das ist so gewollt.
- Lange Docstrings, die ein *Warum* oder eine Grenze erklären: Das ist Projektstil.
