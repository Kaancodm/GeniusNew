# ADR-001: Neustart und Projektisolierung von GeniusNew

- Status: Accepted for bootstrap
- Datum: 2026-09-16
- Entscheider: Kaan
- Ziel-Repository: `Kaancodm/GeniusNew`
- Quell-Repository: `Kaancodm/Agent-Genius` (read-only)
- Referenz-Baseline: `09496c94c064ae36ee98b1a21553c7b3b358e864`

## Kontext

Agent Genius wird als eigenständiges Projekt neu aufgebaut, um historische Projektvermischungen und unklare Herkunft von Komponenten nicht in die neue Basis zu übernehmen.

Die Referenz-Baseline enthält fachlich relevante Zero-Trust-Bausteine, ist aber nicht als Ganzes importierbar. Mindestens ein Verfassungsartefakt verwendet noch einen Agent-Common-Identifier (`https://agent-common.dev/...`), und die gepinnte Handoff-Baseline enthält noch kein verpflichtendes `user_id`-Feld. Damit ist ein unveränderter Repo-Transfer ausgeschlossen.

`GeniusNew` bleibt auf ausdrückliche Entscheidung des Eigentümers öffentlich.

## Entscheidung

1. `GeniusNew` ist die neue, eigenständige Projektbasis.
2. Andere Projekte werden nicht automatisch als Quelle verwendet.
3. `Kaancodm/Agent-Genius` darf ausschließlich als read-only Referenz dienen.
4. Die einzige initial zulässige Referenz ist der Commit `09496c94c064ae36ee98b1a21553c7b3b358e864`.
5. Kein Quellcode wird 1:1 übernommen, bevor er im Import-Manifest geprüft wurde.
6. Artefakte mit Agent-Common-Bezug werden nicht importiert. Nutzbare Konzepte daraus werden bei Bedarf neu implementiert.
7. Weil das Ziel-Repository öffentlich ist, ist Public-Safety-Review vor jedem Altimport zwingend.
8. Architekturverträge und Identitätsmodell werden vor Runtime-Übernahme neu konsolidiert.
9. `user_id` wird vor Implementierung von userbezogenen Rate-/Concurrency-Limits explizit entschieden und durch Schema, Modell und Orchestrator konsistent geführt.
10. Jede akzeptierte Komponente muss Tests und eine nachvollziehbare Herkunft haben.

## Import-Gate

Eine Altkomponente darf nur importiert werden, wenn alle folgenden Bedingungen erfüllt sind:

- eindeutiger Quellpfad und Quell-SHA
- keine Secrets oder privaten Daten
- keine fremde Projektidentität oder unklare Projektzugehörigkeit
- Sicherheitsgrenze dokumentiert
- Public-Repository-Eignung geprüft
- Klassifikation `ACCEPT`
- Teststrategie definiert

Andernfalls lautet die Klassifikation `REBUILD`, `REJECT` oder `PENDING_REVIEW`.

## Konsequenzen

Der Neustart ist bewusst kein Fork und kein vollständiger Clone. Das kostet initial mehr Inventurarbeit, verhindert aber, dass historische Vermischungen, tote Policies oder nicht mehr gültige Sicherheitsannahmen zur neuen Vertrauensbasis werden.

## Nächstes Gate

Vor Runtime-Code:

1. Import-Inventur der gepinnten Baseline.
2. Verfassung/Contracts neu festlegen.
3. Identitätsmodell einschließlich `user_id` festlegen.
4. Minimalen Zero-Trust-End-to-End-Pfad definieren.
5. Erst dann Komponenten einzeln übernehmen oder neu aufbauen.
