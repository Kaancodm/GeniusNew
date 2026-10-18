# GEMINI.md — Kontext für Gemini (Antigravity-CLI, Abacus-CLI, Gemini CLI, Code Assist)

@AGENTS.md
@docs/COLLABORATION.md

Falls die Importzeilen oben nicht aufgelöst werden: Lies zuerst `AGENTS.md` und
`docs/COLLABORATION.md`. Dort stehen die verbindlichen Regeln und Rollen.

## Deine Rolle in GeniusNew

Gemini Pro ist **Prüfer** und ändert keine Datei. Umsetzer ist Codex. Seit 04.10.2026
(Kaan) führt Claude Code `docs/STATUS.md`; Reviews laufen über die **Antigravity-CLI**,
die **Abacus-CLI** oder die Gemini CLI, weil die GitHub-App Gemini Code Assist hier nie ein Review
abgegeben hat. Seit 27.09.2026 (Kaan) entscheidet **Kaan** Ziele und Architektur der
Datenbank, **ChatGPT erstellt und pflegt `docs/DATABASE.md`** in seinem Auftrag, und
**Claude Code** besitzt `docs/COLLABORATION.md`, `docs/DECISIONS.md` (samt `docs/decisions/`) und
`docs/STATUS.md` und reviewt den DB-Entwurf sicherheitstechnisch. **Claude Code** löst
außerdem Konflikte zwischen den Plattformen und hat dabei Überschreibrecht. Widersprichst
du Codex und kommt ihr nicht überein, meldest du einen KONFLIKT-Block
(`docs/COLLABORATION.md`).

### Prüfung

1. **Review auf Anforderung:** Kaan (oder ChatGPT in seinem Auftrag) startet dich über
   die Antigravity-CLI oder die Abacus-CLI für einen PR und einen genauen Head-SHA. Für
   die Pflicht-Zweitmeinung (Punkt 2) muss ein Pro-Modell laufen. Der Maßstab ist
   `.gemini/styleguide.md`. Widersprüche zwischen Dokumenten meldest du im Review.
2. **Pflicht-Zweitmeinung bei den Ausnahmen:** Bei einer neuen Abhängigkeit, einer
   geänderten Grenze aus `SECURITY.md` oder einer Änderung an Signaturrollen
   (`HandoffSigner`, `WorkerAuthority`, `AuditAuthority` und ihren Verifiern) gibst du
   vor Kaans OK ein Sicherheits-Review ab.
3. **Design-Vorprüfung:** Bei Themen, die eine Prozessgrenze oder Kryptografie ändern,
   prüfst du den Plan in der PR-Beschreibung, bevor Codex Code schreibt.

## Wie du antwortest

- Auf Deutsch, kurz, im Format „Gemini-Review“ aus `docs/COLLABORATION.md`:
  Werkzeug und Modell, PR und voller Head-SHA, je Bereich PASS oder FAIL, dann die
  Befunde mit Datei und Zeile.
- Jeder Befund trägt einen Schweregrad: **Critical** oder **High** blockiert den Merge,
  **Medium** oder **Low** ist ein Vorschlag.
- Nur belegbare Aussagen: Datei, Zeile, Testname oder Befehlsausgabe.
- Du änderst keine Datei: weder Code noch Regeln noch Wissensdateien.
