# GeniusNew

GeniusNew ist der saubere Neustart von Agent Genius.

## Projektgrenze

- Ziel-Repository: `Kaancodm/GeniusNew`
- Quell-Repository: `Kaancodm/Agent-Genius` ausschließlich read-only als Referenz
- Gepinnte Referenz-Baseline: `09496c94c064ae36ee98b1a21553c7b3b358e864`
- Agent Common ist kein Bestandteil von GeniusNew.
- Es gibt keinen automatischen oder pauschalen Import aus anderen Projekten.

## Public-Repository-Regel

Dieses Repository ist öffentlich. Daher dürfen keine Secrets, privaten Konfigurationen, Zugangsdaten, internen Tokens, nicht freigegebener Altcode oder vertrauliche Projektartefakte übernommen werden.

Jede Übernahme aus dem Altprojekt muss zuerst im Import-Manifest klassifiziert werden:

- `ACCEPT` — geprüft und für den öffentlichen Import freigegeben
- `REBUILD` — fachlich relevant, aber neu und sauber implementieren
- `REJECT` — nicht übernehmen
- `PENDING_REVIEW` — noch nicht entschieden

## Architekturprinzipien

GeniusNew wird Zero-Trust aufgebaut. Sicherheitsrelevante Identität, Rechte, Policies und Tool-Freigaben dürfen nicht aus untrusted Client-Eingaben übernommen werden. Verträge werden strikt validiert, Grenzen explizit serialisiert und sicherheitsrelevante Aktionen auditierbar gemacht.

## Aktueller Stand

Phase 0: Projektisolierung und Governance.

Noch kein Runtime-Code aus `Kaancodm/Agent-Genius` wurde übernommen.

Siehe:

- `docs/ADR-001-restart-and-isolation.md`
- `docs/IMPORT-MANIFEST.md`
- `SECURITY.md`
