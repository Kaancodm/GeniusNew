# Entscheidungen

Teil der Wissensdatenbank; gepflegt von Gemini Pro (`docs/COLLABORATION.md`). Jede
Zeile ist eine Entscheidung von Kaan. Sie steht mit Datum und Quelle hier, damit
niemand sie neu verhandeln muss. Neue Einträge kommen oben dazu. Wer eine Entscheidung
zurücknimmt, trägt das als neue Zeile ein und lässt die alte stehen.

| Datum | Entscheidung | Begründung | Quelle |
| --- | --- | --- | --- |
| 26.09.2026 | Claude Code sorgt für Ordnung und Struktur zwischen den Plattformen, entscheidet Konflikte verbindlich und hat Überschreibrecht gegenüber allen Werkzeugen, nicht gegenüber Kaan und den Ausnahmen. Löst „Claude Code nur auf Hilferuf“ ab | Eine Stelle, die Widersprüche zwischen Werkzeugen und Dokumenten auflöst | `docs/COLLABORATION.md` |
| 26.09.2026 | Die Datenbank ist die **Grundlage für das Portal**. Das Portal wird danach auf ihr neu gebaut (REBUILD nach `docs/MIGRATION-MATRIX.md`). Betriebsort und DB-Technik entscheidet Kaan gemeinsam, nach Geminis Vorschlag | Der Betriebsort bestimmt die Technik: Serverless hat keine dauerhafte lokale Datei | `docs/COLLABORATION.md` |
| 26.09.2026 | **Datenbank im Code** für Ledger, wartende Jobs, Audit-Kette und Anker-Zustand. Gemini Pro ist Head der Datenbank (Design, Schema, Pflicht-Freigabe jedes DB-PRs), Codex setzt um. Die Technik entscheidet Kaan nach Geminis Vorschlag in `docs/DATABASE.md`. Löst „keine Datenbank“ für die Zeit nach v0.1 ab | Neustarts sollen nichts vergessen und nichts doppelt annehmen; eine Stelle verantwortet das Datenmodell | `docs/COLLABORATION.md` |
| 26.09.2026 | Gemini Pro und NotebookLM führen die Wissensdatenbank (`STATUS.md`, `DECISIONS.md`, NotebookLM-Quellen) | Eine Stelle für Wissen, getrennt von Code; alle Werkzeuge fragen zuerst dort | `docs/COLLABORATION.md` |
| 26.09.2026 | Gemini reviewt jeden PR automatisch; Critical/High blockiert; Pflicht-Zweitmeinung bei Ausnahmen | Unabhängige Prüfung, ohne Kaan als Engpass | `.gemini/styleguide.md`, `docs/COLLABORATION.md` |
| 26.09.2026 | Codex setzt um und mergt eigene PRs bei grüner CI; Ausnahmen (neue Abhängigkeit, `SECURITY.md`-Grenze, Tags) brauchen Kaans OK | Weniger Wartezeit; die Ausnahmen bleiben Kaans Entscheidung | `AGENTS.md`, `docs/COLLABORATION.md` |
| 26.09.2026 | Claude Code nur noch für Ordnung der Regeln und auf Hilferuf von Codex (abgelöst, siehe oben) | Ein Umsetzer statt zwei | `docs/COLLABORATION.md` |
| 25.09.2026 | Alle Signaturen Ed25519: Audit-Köpfe (#31), Handoffs (#34), Ergebnisse (#35); prüfende Instanzen halten nur öffentliche Schlüssel | Mit HMAC konnte jede prüfende Instanz auch signieren | `SECURITY.md`, PRs #31/#34/#35 |
| 25.09.2026 | Quickstart-Review (Schritt 20) als technischer Review angenommen, ohne Person ohne Projektkenntnis | Frischer Clone und venv belegen den Weg ausreichend | `docs/QUICKSTART-REVIEW.md` |
| 23.09.2026 | Audit-Anker läuft in eigenem Prozess | Der Schreiber darf den Anker nicht aus seinem Speicher zurücksetzen können | `docs/ROADMAP-V01.md` Schritt 8 |
| 23.09.2026 | HMAC bleibt für v0.1, Ed25519 danach | v0.1 nicht mit einer Abhängigkeitsentscheidung aufhalten | `docs/ROADMAP-V01.md` Schritt 8 |
| 23.09.2026 | Approval über HTTP gehört in v0.1 | Approval-pflichtige Jobs müssen den ganzen Weg gehen können | `docs/ROADMAP-V01.md` Schritt 17 |
| 23.09.2026 | Für v0.1 reicht logische Trennung von Gateway und Orchestrator (ein Prozess) | Prozess-Trennung ist Deployment; die Schnittstellen sind schon getrennt | `docs/ROADMAP-V01.md` Schritt 13 |
| 23.09.2026 | autoresearch-Muster nur als Entwicklungsschleife, nicht als Produktfunktion | Es misst den Code, es gehört nicht in den Dienst | `docs/AUTORESEARCH.md` |
| 23.09.2026 | Browser Use und die „Cybersecurity Skills“-Sammlung werden nicht eingebaut | Außerhalb des Scopes; fremde Quelle ohne Prüfung | Chat mit Kaan |
| 17.09.2026 | Keine Datenbank in v0.1 (#3 geschlossen); der Kern bleibt prozesslokal | Widerspricht dem Bootstrap-Scope | `docs/ROADMAP-V01.md` |
| 17.09.2026 | `docs/MIGRATION-MATRIX.md` ist das kanonische Import-Gate; Altprojekt nur Lesequelle | Nichts ungeprüft übernehmen | `docs/MIGRATION-MATRIX.md`, `SECURITY.md` |
| — | Das Projekt heißt GeniusNew und wird nicht umbenannt | Jede Umbenennung hat einen Migrationszyklus gekostet | `docs/ROADMAP-V01.md`, Arbeitsregeln |
