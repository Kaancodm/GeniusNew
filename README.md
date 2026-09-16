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
- `REBUILT_NATIVE` — als GeniusNew-eigener Code neu gebaut; kein Altcode importiert
- `REJECT` — nicht übernehmen
- `PENDING_REVIEW` — noch nicht entschieden

## Architekturprinzipien

GeniusNew wird Zero-Trust aufgebaut. Sicherheitsrelevante Identität, Rechte, Policies und Tool-Freigaben dürfen nicht aus untrusted Client-Eingaben übernommen werden. Verträge werden strikt validiert, Grenzen explizit serialisiert und sicherheitsrelevante Aktionen auditierbar gemacht.

## Aktueller Stand

Phase 1: deterministischer Zero-Trust-Contract-Kern.

Der erste Runtime-Code ist GeniusNew-nativ neu implementiert. Er enthält ein
striktes Handoff-Modell, kanonische JSON-Serialisierung, SHA-256-Payload-Bindung,
HMAC-Signaturen über den gesamten Handoff, Policy-Allow-Lists und einen
Fail-Closed-Validator. Der HMAC-Schlüssel ist eine serverseitige Laufzeitkonfiguration
und gehört niemals in dieses Repository.

Siehe:

- `docs/ADR-001-restart-and-isolation.md`
- `docs/IMPORT-MANIFEST.md`
- `SECURITY.md`
- `docs/HANDOFF-V1.md`
- `schemas/handoff-v1.schema.json`
- `geniusnew/contracts.py`

## Lokale Prüfung

Der Phase-1-Kern benötigt nur Python 3. Für die vollständige Contract-Prüfung:

```sh
python3 -m unittest discover -s tests -v
```
