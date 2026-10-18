# Warteschlange in B6

- Datum: 02.10.2026
- Quelle: #98 („Umsetzung der Freigabe von Kaan“), in den B6-Branch gemergt (`f3ef129`); Claude DB Review auf #97 an `f220e30`

## Entscheidung

**Warteschlange in B6 (Kaan):** Den Copilot-Befund zu #97 minimal beheben. Die Kapazitätsgrenze für wartende Jobs zählt nur unabgelaufene Zeilen; abgelaufene Zeilen werden nicht ohne ihr Audit-Ereignis entfernt. Ob der auditierte Sweep noch in B6 kommt, ist offen (`docs/ROADMAP-V02.md`, offene Entscheidung 5)

## Begründung

Ein Sweep ohne Audit-Ereignis verletzt B6. Ohne Fix füllen 1000 nie freigegebene Jobs die Warteschlange für immer
