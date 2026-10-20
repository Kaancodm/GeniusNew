# GeniusNew — Claude Code

@AGENTS.md
@docs/COLLABORATION.md
@docs/EXECUTION-GATES.md

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
