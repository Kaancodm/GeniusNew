# GeniusNew Import Manifest

Quelle für diese erste Inventur: `Kaancodm/Agent-Genius@09496c94c064ae36ee98b1a21553c7b3b358e864`

Dieses Manifest ist ein Sicherheits- und Herkunfts-Gate. `PENDING_REVIEW` bedeutet ausdrücklich: noch nicht nach GeniusNew kopieren.

| Bereich / Altpfad | Status | Grund | Nächster Schritt |
|---|---|---|---|
| `staat/verfassung/handoff.schema.json` | REBUILD | Enthält noch Agent-Common-Schema-ID; Identitätsmodell der Baseline ist für GeniusNew nicht final | Neues GeniusNew-Handoff-Schema definieren |
| `staat/verfassung/models/handoff.py` | REBUILD | Kein verpflichtendes `user_id`; muss konsistent mit neuem Schema entstehen | Neues Contract-Modell nach Schema bauen |
| `staat/verfassung/models/base.py` | PENDING_REVIEW | Strict-/forbid-Konzept fachlich relevant, Implementierung noch nicht Public- und Isolation-geprüft | Einzelreview |
| `staat/verfassung/models/canonical.py` | PENDING_REVIEW | Kanonisches Hashing fachlich relevant | Kryptografische/Serialisierungsprüfung |
| `staat/verfassung/job_result.schema.json` | PENDING_REVIEW | Vertragsbaustein relevant, Abhängigkeit von neuer Handoff-Verfassung | Nach neuem Handoff prüfen |
| `staat/verfassung/audit_log.schema.json` | PENDING_REVIEW | Auditvertrag relevant | Datenminimierung/PII/Secret-Leak prüfen |
| `staat/gesetze/approval-policy.json` | REBUILD | Altpolicy enthält historische Produktentscheidungen und muss zur neuen Tool-/Approval-Architektur passen | Neue Policy aus Anforderungen ableiten |
| `staat/gesetze/policy.py` | REBUILD | Muss neue Policy und Identität konsistent erzwingen | Neu implementieren |
| `staat/regierung/orchestrator.py` | REBUILD | Zentrale Trust-Grenze; keine 1:1-Übernahme | Gegen neue Contracts neu bauen |
| `polizei/grenzschutz/gateway.py` | PENDING_REVIEW | Fail-closed-Grenzschutz fachlich wertvoll | Gegen neue Contracts und Policies prüfen |
| `polizei/forensik/audit_chain.py` | PENDING_REVIEW | Hash-Chain fachlich relevant | Integritätsmodell und Persistenz prüfen |
| `polizei/forensik/anchor.py` | PENDING_REVIEW | Forensik-Baustein | Zweck und Bedrohungsmodell prüfen |
| `polizei/forensik/verify.py` | PENDING_REVIEW | Verifikation relevant | Gegen neue Audit-Struktur prüfen |
| `polizei/interne_ermittlung/monitor.py` | PENDING_REVIEW | Unabhängige Ergebnisprüfung entspricht Zero-Trust-Ziel | Schnittstellen und Leakage prüfen |
| `land/transport.py` | PENDING_REVIEW | Serialisierungsgrenze relevant | Minimalen Transportvertrag neu festlegen |
| `land/worker.py` | PENDING_REVIEW | Worker-Sicherheitsverhalten relevant | Capability-/Tool-Grenzen prüfen |
| `land/summarizer.py` | PENDING_REVIEW | Deterministischer Referenzworker kann für Tests nützlich sein | Später als Testworker prüfen |
| `industriegebiet/runtime.py` | PENDING_REVIEW | Runtime-Grenzen relevant | Threat Model vor Übernahme |
| `industriegebiet/firecracker_runtime.py` | REJECT_FOR_BOOTSTRAP | Nicht erforderlich für minimalen Neustart; erhöht Komplexität und Angriffsfläche | Nur durch spätere explizite Architekturentscheidung wieder aufnehmen |
| `industriegebiet/sandboxes/profiles.json` | REBUILD | Profile müssen aus neuer Policy/Runtime abgeleitet werden | Neue Profile definieren |
| `bürgerbüro/portal/*` | REBUILD | Externe Angriffsfläche; Auth/Request-Verträge müssen auf neue Identität abgestimmt werden | Erst nach Kernverträgen neu bauen |
| `einwohnermeldeamt/database/*` | REJECT_FOR_BOOTSTRAP | Baseline enthält nur Platzhalterstruktur | Datenmodell später bewusst entwerfen |
| `nationalbibliothek/memory/*` | REJECT_FOR_BOOTSTRAP | Nicht Teil des minimalen Trust-Kerns | Spätere eigene Phase |
| `.github/workflows/verify.yml` | PENDING_REVIEW | CI-Prinzip sinnvoll, muss auf GeniusNew und neue Toolchain angepasst werden | Neue minimale CI definieren |
| `tests/*` | PENDING_REVIEW | Tests enthalten wertvolle Sicherheitsinvarianten, aber sind an Altarchitektur gekoppelt | Anforderungen extrahieren, Tests für GeniusNew neu schreiben |
| `README.md` des Altrepos | REJECT_AS_SOURCE_CODE | Dokumentiert Altstand und Altidentität, dient nur als historische Referenz | GeniusNew-Dokumentation eigenständig halten |

## Regel

`ACCEPT` darf erst gesetzt werden, nachdem eine Komponente einzeln geprüft wurde. Für Bootstrap Phase 0 ist daher bewusst noch keine Altkomponente als `ACCEPT` markiert.
