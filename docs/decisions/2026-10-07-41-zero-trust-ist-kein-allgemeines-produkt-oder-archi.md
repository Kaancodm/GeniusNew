# Zero-Trust ist kein allgemeines Produkt- oder Architekturziel mehr

- Datum: 07.10.2026
- Quelle: Arbeitsrichtlinie von Kaan an Claude Code, 07.10.2026; Impact-Check in dieser Sitzung; Baseline `main` `3497e699c3b42c51eb0d26742af7b3e6de042f58`

## Entscheidung

**Zero-Trust ist kein allgemeines Produkt- oder Architekturziel mehr (Kaan):** Sicherheit bleibt Kernziel, aber nicht jede Komponente wird nach maximalen Zero-Trust-Anforderungen gebaut. Architekturentscheidungen fallen künftig nach tatsächlichem Risiko, Produktnutzen, Komplexität, Wartbarkeit und Aufwand; keine zusätzlichen Security-Schichten ohne Nutzen für das reale Bedrohungsmodell. **Unverändert:** Bestehende funktionierende Schutzmaßnahmen (Authentisierung, Autorisierung, Audit, Signaturprüfung, Replay-Abwehr, Approval-Grenze, Fail-Closed, Persistenzsicherung) werden nicht entfernt oder abgeschwächt, nur weil die Vorgabe entfällt; Tests und Sicherheitsgarantien werden nicht angepasst oder gelöscht, um die CI grün zu bekommen; Audit- und Nachweisfähigkeit bleibt. **Verfahren für Änderungen an Security-Grenzen:** erst dokumentieren (aktueller Zustand, Problem, Bedrohungsmodell, Änderung, Sicherheitsauswirkung, Regressionsrisiko), dann Vorschlag vorlegen und auf Kaans ausdrückliche Freigabe warten; nie eigenständig durchführen. **Priorität:** funktionierendes Produkt → klare Sicherheitsgrenzen → einfache Architektur → zuverlässige Tests → Bedienbarkeit → schnelle Beta-Reife. **Nicht Teil dieser Zeile:** Weder Code, Tests noch `SECURITY.md`, `AGENTS.md`, Verfassung und Roadmaps wurden geändert; ihre Zero-Trust-Formulierungen stehen bis zu einem eigenen, freigegebenen PR weiter dort. Löst frühere Zero-Trust-Vorgaben als Zielbild ab, nicht die daraus entstandenen Schutzmaßnahmen

## Begründung

Kaan hob die strikte Zero-Trust-Vorgabe auf, um schneller ein funktionierendes Produkt zu erreichen, ohne bestehenden Schutz still zu verlieren. Ein Read-only-Impact-Check (Claude Code, 07.10.2026) bewertet KEEP/SIMPLIFY/REMOVE; Vereinfachungen brauchen je einen Vorschlag und Kaans Freigabe
