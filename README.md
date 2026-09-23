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

## Quickstart

Voraussetzungen: Linux, Python 3.11 oder neuer, `git`. Keine Abhängigkeiten. Nach dem
Klonen kein Internetzugriff — der HTTP-Eingang lauscht nur auf `127.0.0.1` —, und nichts
bleibt auf der Platte zurück außer temporären Verzeichnissen, die wieder verschwinden.

```sh
git clone https://github.com/Kaancodm/GeniusNew.git
cd GeniusNew
./scripts/demo.sh
```

Der Befehl schickt einen Job über HTTP durch alle Schichten und greift ihn danach
dreizehnmal an. Die letzte Zeile muss lauten:

```
PASS — job succeeded over HTTP, chain verified against the anchored head, 13/13 attacks refused.
```

und der Exit-Code ist `0`. Alles andere ist ein Fehlschlag: das Skript sagt dann, welche
Bedingung nicht erfüllt war, und endet mit einem Exit-Code ungleich `0`.

Was die Ausgabe zeigt, in der Reihenfolge der nummerierten Blöcke:

| Block | Bedeutung |
| --- | --- |
| `[0]`–`[1]` | Drei getrennte Rollenschlüssel, Policy als Allow-List mit Default-Deny |
| `[2]`–`[3]` | Job geht über einen echten Socket hinein; die Identität kommt aus dem API-Key, nicht aus dem Request |
| `[4]` | Audit-Chain mit vier Einträgen von drei Instanzen: `orchestrator`, `gateway`, `monitor` |
| `[5]` | Kette verifiziert gegen einen signierten Kopf, festgelegt bei einem Anker in eigenem Prozess |
| `[6]` | Dreizehn Manipulationsversuche, jeder muss mit `[ok]` abgelehnt werden |

Die Ausgabe enthält bewusst nur Digests und Kennungen — keine Payload, kein Ergebnistext,
kein Schlüsselmaterial. `tests/test_demo.py` prüft das mit Kanarienwerten.

**Was `PASS` nicht bedeutet:** keine Produktionsfreigabe und kein Sicherheitsnachweis
für einen echten Betrieb. Außer Worker und Audit-Anker sind die Instanzen getrennte
Objekte in einem Prozess; die Worker-Isolation ist eine Prozessgrenze, keine microVM;
der Anker wird vom Dienst gestartet und hält nur Speicher; die Signaturen sind HMAC und
damit symmetrisch. Die bekannten Grenzen stehen einzeln in `SECURITY.md`.

**Andere Betriebssysteme:** Die Worker-Isolation braucht POSIX-Ressourcenlimits. Unter
Windows verweigert sie die Ausführung (fail closed), die Demo erreicht dort kein `PASS`.
WSL ist Linux; die Testsuite läuft dort (belegt in #28). macOS ist nicht getestet. Die
CI läuft auf `ubuntu-latest`.

## Aktueller Stand

Roadmap zu v0.1 (`docs/ROADMAP-V01.md`): Schritte 1–19 umgesetzt; der Audit-Anker
aus Schritt 8 läuft in eigenem Prozess, sein Lebenszyklus liegt noch beim Dienst.
Schritt 20 (Gegenlesen dieses Quickstarts durch jemanden ohne Projektkenntnis) ist
offen, Schritt 21 (Tag `v0.1`) steht aus.

Die Phasennummern in `docs/MIGRATION-MATRIX.md` zählen die Migration aus dem
Altprojekt und sind nicht dieselben wie die Bauphasen hier.

Der gesamte Runtime-Code ist GeniusNew-nativ neu implementiert. Schlüssel sind
serverseitige Laufzeitkonfiguration und gehören niemals in dieses Repository; die Demo
leitet ihre Schlüssel aus einem festen Demo-Secret ab, das nur für die Demo gilt.

Siehe:

- `docs/ADR-001-restart-and-isolation.md`
- `docs/MIGRATION-MATRIX.md` — kanonisches Import-Gate
- `docs/IMPORT-MANIFEST.md` — historische erste Inventur
- `SECURITY.md`
- `docs/HANDOFF-V1.md`
- `docs/APPROVAL-V1.md`
- `schemas/handoff-v1.schema.json`
- `geniusnew/contracts.py`
- `geniusnew/approvals.py`
- `geniusnew/workers.py`
- `geniusnew/isolation.py`
- `geniusnew/gateway.py`
- `geniusnew/orchestrator.py`
- `geniusnew/verifier.py`
- `geniusnew/http_entry.py`
- `geniusnew/wiring.py` — Kompositionswurzel
- `geniusnew/anchor_process.py` — Audit-Anker in eigenem Prozess
- `geniusnew/audit.py`, `geniusnew/audit_chain.py`
- `docs/ISOLATION-V01.md`
- `docs/GATEWAY-V01.md`
- `docs/ROADMAP-V01.md`

## Lokale Prüfung

Dieselben zwei Schritte wie die CI (`.github/workflows/verify.yml`):

```sh
python3 -m unittest discover -s tests -v
python3 scripts/refusals.py
```

Der erste läuft in Sekunden und enthält den End-to-End-Test und die Demo. Der zweite
schaltet jede Ablehnung im Code einzeln ab und verlangt, dass die Suite jedes Mal rot
wird; er führt die Suite dafür einmal pro Ablehnung aus und dauert entsprechend Minuten.
