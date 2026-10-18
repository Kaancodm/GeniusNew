# Abläufe verschlankt

- Datum: 04.10.2026
- Quelle: Chat mit Claude Code, 04.10.2026 („so weiter verfahren“ auf Claudes Projektbewertung)

## Entscheidung

**Abläufe verschlankt (Kaan):** (1) Gemini prüft offiziell über die **Antigravity-CLI** oder die Gemini CLI; ein solches Review zählt, wenn es als PR-Kommentar Werkzeug, Modell, vollen Head-SHA und je Bereich PASS/FAIL nennt (Format in `docs/COLLABORATION.md`). Ein automatisches Gemini-Review jedes PRs gibt es nicht mehr; Pflicht bleibt es bei den Ausnahmen und als Design-Vorprüfung. Löst die Zeile vom 26.09.2026 („Gemini reviewt jeden PR automatisch“) ab. (2) `docs/STATUS.md` gehört **Claude Code**; Gemini ändert keine Datei mehr. Claude überträgt Wissensblöcke gesammelt auf `claude/wissen-<datum>`, Kaan mergt. Löst die Zeile vom 26.09.2026 („Gemini Pro und NotebookLM führen die Wissensdatenbank“) ab. (3) **Keine Stapel-PRs:** Jeder PR basiert auf `main`

## Begründung

Die GitHub-App Gemini Code Assist hat in diesem Repository nie ein Review abgegeben. `docs/STATUS.md` ist seit dem 27.09.2026 nicht mehr aktualisiert worden. Der Stapel #97 ← #106 ← #111 ← #112 ließ sich per API weder aktualisieren noch mergen und kostete pro Merge eine Update- und Review-Runde
