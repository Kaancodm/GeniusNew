# GeniusNew

GeniusNew ist der saubere Neustart von Agent Genius.

## Projektgrenze

- Ziel-Repository: `Kaancodm/GeniusNew`
- Quell-Repository: `Kaancodm/Agent-Genius` ausschließlich read-only als Referenz
- Gepinnter Quell-Stand der aktuellen Klassifikation: `b0c7ce136160a4ba818eee028b7980c952848b5a`
- Historische erste Baseline: `09496c94c064ae36ee98b1a21553c7b3b358e864` — nur noch Herkunftsnachweis
- Agent Common ist kein Bestandteil von GeniusNew.
- Es gibt keinen automatischen oder pauschalen Import aus anderen Projekten.

## Public-Repository-Regel

Dieses Repository ist öffentlich. Daher dürfen keine Secrets, privaten Konfigurationen, Zugangsdaten, internen Tokens, nicht freigegebener Altcode oder vertrauliche Projektartefakte übernommen werden.

Jede Übernahme aus dem Altprojekt muss zuerst in der Migrationsmatrix
`docs/MIGRATION-MATRIX.md` klassifiziert werden. Sie ist das kanonische
Import-Gate und ist an den oben genannten exakten Quell-SHA gebunden:

- `ACCEPT` — einzeln geprüft und für den öffentlichen Import freigegeben
- `REBUILD` — fachlich relevant, aber GeniusNew-nativ neu implementieren; keine Kopie
- `REJECT` — nicht übernehmen
- `HISTORICAL_ONLY` — reine Herkunftsinformation; wird keine GeniusNew-Autorität
- `TEST_FIXTURE_ONLY` — statischer Test-/Beispielstring ohne Laufzeitautorität

`ACCEPT` ist derzeit bewusst für keine einzige Altkomponente gesetzt.

`docs/IMPORT-MANIFEST.md` ist die erste Inventur und nur noch historische
Evidenz. Sie ist an den älteren Quell-SHA gebunden und ist kein Gate mehr. Ihre
`REBUILT_NATIVE`-Einträge bleiben als Nachweis bereits GeniusNew-nativ gebauter
Bestandteile gültig; alle übrigen Status dort sind überholt.

## Architekturprinzipien

GeniusNew wird Zero-Trust aufgebaut. Sicherheitsrelevante Identität, Rechte, Policies und Tool-Freigaben dürfen nicht aus untrusted Client-Eingaben übernommen werden. Verträge werden strikt validiert, Grenzen explizit serialisiert und sicherheitsrelevante Aktionen auditierbar gemacht.

## Aktueller Stand

Phase 1: deterministischer Zero-Trust-Contract-Kern.

Die Phasennummern in `docs/MIGRATION-MATRIX.md` zählen die Migration aus dem
Altprojekt und sind nicht dieselben wie die Bauphasen hier.

Der erste Runtime-Code ist GeniusNew-nativ neu implementiert. Er enthält ein
striktes Handoff-Modell, kanonische JSON-Serialisierung, SHA-256-Payload-Bindung,
HMAC-Signaturen über den gesamten Handoff, Policy-Allow-Lists und einen
Fail-Closed-Validator. Der HMAC-Schlüssel ist eine serverseitige Laufzeitkonfiguration
und gehört niemals in dieses Repository.

Siehe:

- `docs/ADR-001-restart-and-isolation.md`
- `docs/MIGRATION-MATRIX.md` — kanonisches Import-Gate
- `docs/IMPORT-MANIFEST.md` — historische erste Inventur
- `SECURITY.md`
- `docs/HANDOFF-V1.md`
- `schemas/handoff-v1.schema.json`
- `geniusnew/contracts.py`

## Lokale Prüfung

Der Phase-1-Kern benötigt nur Python 3. Für die vollständige Contract-Prüfung:

```sh
python3 -m unittest discover -s tests -v
```
