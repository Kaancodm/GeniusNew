# Reihenfolge bis v0.2 geändert

- Datum: 04.10.2026
- Quelle: Chat mit Claude Code, 04.10.2026 („so weiter verfahren“ auf Claudes Projektbewertung); `docs/ROADMAP-V02.md`

## Entscheidung

**Reihenfolge bis v0.2 geändert (Kaan):** (1) Diese Woche den echten Server einrichten (`ops/server/genius-server`, `docs/setup/SERVER-TOOL.md`), danach **C5** (Dienste, Backup und Restore) und **E3** (frischer Klon nach Anleitung) vor das Portal ziehen. (2) Das Portal klein starten: **D1** (Vertrag Portal↔Kern) zuerst; der Python-Prototyp von Codex (27.09.) dient als Vorlage, nicht als Code zum Übernehmen

## Begründung

Bisher ist nichts auf einem echten Server gelaufen; C5 und E3 zeigen früh, ob Dienste, Anker unter eigenem Nutzer und Restore in der Praxis tragen. D1 legt die Schnittstelle fest, bevor Portal-Code entsteht
