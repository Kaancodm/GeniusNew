# Datenbank im Code

- Datum: 26.09.2026
- Quelle: `docs/COLLABORATION.md`

## Entscheidung

**Datenbank im Code** für Ledger, wartende Jobs, Audit-Kette und Anker-Zustand. Gemini Pro ist Head der Datenbank (Design, Schema, Pflicht-Freigabe jedes DB-PRs), Codex setzt um. Die Technik entscheidet Kaan nach Geminis Vorschlag in `docs/DATABASE.md`. Löst „keine Datenbank“ für die Zeit nach v0.1 ab

## Begründung

Neustarts sollen nichts vergessen und nichts doppelt annehmen; eine Stelle verantwortet das Datenmodell
