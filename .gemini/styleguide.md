# Review-Leitfaden für Gemini (Antigravity-CLI, Abacus-CLI, Gemini CLI, Code Assist)

GeniusNew ist ein Agentensystem in Python. Regeln: `AGENTS.md`, Rollen:
`docs/COLLABORATION.md`. Reviews bitte **auf Deutsch**, im Format
„Gemini-Review“ aus `docs/COLLABORATION.md`.

## Schweregrad

- **Critical / High: blockiert den Merge.** Codex behebt den Befund oder begründet im
  Thread, warum er nicht zutrifft.
- **Medium / Low: Vorschlag.** Er darf offen bleiben.

## Immer prüfen

1. **Unklare Zustände:** Nicht sicherheitsrelevante Automation darf mit protokolliertem
   Grund einen bestmöglichen Versuch machen. Erzeugt ein stilles `return`, ein breites
   `except` oder ein Default bei Identität, Berechtigungen, Approvals, Secrets oder
   Audit unklare Autorität? *High*
2. **Signaturrollen:** Hält eine prüfende Instanz (Gateway, Ergebnisprüfung, Anker,
   Audit-Prüfung) einen privaten Schlüssel, ein Root-Secret oder eine
   `*Signer`/`*Authority`-Instanz? *Critical*
3. **Bestehender Refusal-Guard:** Ändert ein PR eine Ablehnung ohne den betroffenen
   Vertrag und die Guard-Prüfung anzupassen? *High*
4. **Grenzen:** Ändert der PR eine Grenze aus `SECURITY.md`, ohne den offen haltenden
   Test (`…_and_this_is_the_boundary`) und die Tabelle anzupassen? *High*
5. **Leaks:** Gelangen Nutzdaten, Schlüssel oder Tokens in Audit-Einträge, Logs,
   Fehlermeldungen oder Tests mit echten Werten? *Critical*
6. **Tests:** Ist ein Skip mit Grund und `# TODO: fix later` markiert und im PR
   ausgewiesen? Ein stiller Skip oder ein als bestanden ausgegebener Skip ist *High*.
7. **Abhängigkeiten:** Ist eine neue Abhängigkeit hinzugekommen, begründet und in
   `requirements.txt` hash-gepinnt? Ohne Hash-Pin gilt das als *High*.
8. **Wissensblock:** Enthält die Beschreibung eines Codex-PRs den ausgefüllten Block
   „## Für die Wissensdatenbank“? Fehlt er: *High*. Ändert ein Codex-PR
   `docs/STATUS.md`? Diese Datei gehört Claude Code: *Medium*. Ändert er
   `docs/COLLABORATION.md`, `docs/DECISIONS.md` oder `docs/DATABASE.md`? Diese Dateien
   gehören Claude Code beziehungsweise ChatGPT (im Auftrag von Kaan), nicht Codex;
   ohne Kaans konkreten Änderungsauftrag ist das *High*.

9. **DB-Code-PRs** (Speicher-Code, Schema, Migrationen): Liegt der Anker in derselben
   Datenbank oder unter denselben Zugangsdaten wie die Audit-Kette? *Critical*. Startet
   der Dienst bei beschädigtem oder fehlendem Speicher still bei null? *Critical*.
   Stehen Zugangsdaten oder DB-Dateien im Repository? *Critical*. Weicht das Schema von
   `docs/DATABASE.md` ab? *High*. Die Freigabe für diese PRs gibt Claude Code
   (`Claude DB Review: APPROVED`/`CHANGES REQUESTED`), nicht Gemini.
10. **Stapel-PR:** Basiert der PR auf einem anderen PR-Branch statt auf `main`? *Medium*

## Nicht bemängeln

- Deutsche Doku neben englischem Code: Das ist so gewollt.
- Lange Docstrings, die ein *Warum* oder eine Grenze erklären: Das ist Projektstil.
