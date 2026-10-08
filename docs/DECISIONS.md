# Entscheidungen

Teil der Wissensdatenbank; `docs/DECISIONS.md` und `docs/COLLABORATION.md` gepflegt
von Claude Code, seit 04.10.2026 auch `docs/STATUS.md` (`docs/COLLABORATION.md`).

Jede Entscheidung von Kaan steht als **eigene Datei** in `docs/decisions/`, mit Datum,
Begründung und Quelle, damit niemand sie neu verhandeln muss. Eine Datei pro
Entscheidung heißt: Zwei PRs ändern nie dieselbe Zeile, und Entscheidungen führen
nicht mehr zu Merge-Konflikten.

## Regeln

- **Dateiname:** `YYYY-MM-DD-NN-thema.md`; `NN` (mindestens zweistellig, ab 100 dreistellig)
  zählt fortlaufend über alle Dateien (nächste freie Nummer nehmen). Das Datum im Namen
  muss ein echtes Kalenderdatum sein und zu `- Datum:` passen (`0000-00-00` ↔ `—`). Eine Entscheidung ohne Datum trägt `0000-00-00`.
- **Aufbau:** Titel, `- Datum:`, `- Quelle:`, Abschnitt `## Entscheidung`, Abschnitt
  `## Begründung` (genau diese Reihenfolge; `scripts/decisions_tool.py` liest sie).
- **Besitz:** `docs/decisions/` gehört wie diese Datei ausschließlich Claude Code.
- **Zurücknehmen:** Wer eine Entscheidung zurücknimmt, legt eine neue Datei an und
  lässt die alte unverändert stehen.
- **Alles in einem Dokument:** `python3 scripts/decisions_tool.py` gibt alle
  Entscheidungen, neueste zuerst, als ein Dokument aus. Das ist die Datei für einen
  NotebookLM-Export (Verfahren: `docs/COLLABORATION.md`, „NotebookLM-Quellenpaket“).
- **Stand 07.10.2026:** Die bisherige Tabelle (41 Einträge; danach kommen neue Dateien hinzu) wurde ohne inhaltliche
  Änderung in diese Dateien überführt.
