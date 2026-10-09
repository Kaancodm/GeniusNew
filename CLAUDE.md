# GeniusNew — Claude Code

## Autonomer Dev-Workflow (Kaan, 08.10.2026)

Für Entwicklungswerkzeuge und Routinefreigaben gilt vorrangig [docs/DEV-AUTOMATION.md](docs/DEV-AUTOMATION.md).
Der Entwicklungsauftrag erlaubt reversible Arbeit bis zum geprüften Draft-PR ohne
wiederholte GO-Fragen. Eine Rollenbezeichnung erzeugt keine zusätzliche Wartefreigabe.
Gezielte Freigaben gelten für Produktion, Zugänge, Secrets, main und irreversible Aktionen.
Die tatsächlichen Runtime-Verträge und fachlichen CI-/Review-Gates gelten weiter.


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
