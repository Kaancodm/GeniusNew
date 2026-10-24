# GeniusNew — Claude Code

@AGENTS.md
@docs/COLLABORATION.md

Vor sicherheitsrelevanter Arbeit zusätzlich lesen:
@SECURITY.md

Für Übergaben:
@docs/HANDOVER.md

## Claude-spezifisch

- Arbeite direkt bei einfachen, sequenziellen Aufgaben.
- Nutze Subagents nur für unabhängige, parallelisierbare Arbeit.
- Erfinde keine zusätzlichen Verifikationsschritte über die Repo-Gates hinaus.
- Tatsächlich ausgeführte Checks dürfen als PASS gemeldet werden; sonst UNKNOWN.
- Keine Direktänderung an main; Branch-/PR-Regeln aus AGENTS.md gelten.

## Umgebung und Basis zuerst prüfen

Damit nichts auf einer falschen Grundlage weitergebaut wird:

- Zu Beginn jedes Auftrags `hostname`, `pwd`, Remote, Branch und vollen SHA ausgeben.
- **Claude arbeitet nur auf dem Hauptserver des Projekts (Kaan, 24.10.2026).** Ist
  `CLAUDE_CODE_REMOTE` gesetzt oder ist `hostname` nicht der Hauptserver, läuft die
  Sitzung woanders (z. B. in einem flüchtigen Cloud-Container). Dann keinen Auftrag
  ausführen, auch keine Statusabfrage, keine Doku- und keine Repo-Änderung und nichts
  simulieren: sofort stoppen, melden und auf eine Sitzung auf dem Server verweisen
  (`claude remote-control` im Checkout dort).
- Dateien außerhalb des Repositorys gehen in der Cloud mit der Sitzung verloren.
  Ergebnisse, die bleiben sollen, gehören in einen PR oder werden Kaan übergeben.
- Widerspricht ein Auftrag einer Zeile in `docs/DECISIONS.md` oder den Rollen in
  `docs/COLLABORATION.md`, zuerst den Widerspruch melden und auf Kaans Entscheidung
  warten; nicht eigenmächtig umsetzen.
- Nennt `docs/STATUS.md` einen älteren `main`-SHA als den aktuellen, ist der Stand
  dort veraltet: nur nach Prüfung gegen Code und `git log` übernehmen.
