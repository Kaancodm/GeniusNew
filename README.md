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

`./scripts/demo.sh` ist der kanonische v0.1-Nachweis: ein Befehl, der einen Job über
HTTP durch alle Schichten schickt, die Audit-Kette gegen den verankerten Kopf prüft und
den Job danach fünfzehnmal angreift.

**Voraussetzungen:** Linux auf x86_64 oder aarch64 (auch WSL 2), Python 3.11 oder neuer,
`git`. Eine einzige Abhängigkeit, `cryptography` für die Ed25519-Signaturen von Handoff,
Ergebnis und Audit-Kopf, exakt gepinnt und mit den Hashes aller veröffentlichten Dateien in
`requirements.txt`. Nach der Installation kein Internetzugriff — der HTTP-Eingang lauscht
nur auf `127.0.0.1` —, und nichts bleibt auf der Platte zurück außer temporären
Verzeichnissen, die wieder verschwinden.

```sh
git clone https://github.com/Kaancodm/GeniusNew.git
cd GeniusNew
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install --require-hashes -r requirements.txt
python3 -W error::ResourceWarning -m unittest discover -s tests   # Sekunden, endet mit OK
./scripts/demo.sh                                                 # der Nachweis
```

**Erfolgssignal:** Die Tests enden mit `OK`. Die letzte Zeile der Demo muss lauten:

```
PASS — job succeeded over HTTP, chain verified against the anchored head, 15/15 attacks refused.
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
| `[6]` | Fünfzehn Manipulationsversuche, jeder muss mit `[ok]` abgelehnt werden |
| `[7]` | Der Anker wird gestoppt und aus seiner Zustandsdatei neu gestartet; er setzt beim letzten signierten Kopf fort |

Die Ausgabe enthält bewusst nur Digests und Kennungen — keine Payload, kein Ergebnistext,
kein Schlüsselmaterial. `tests/test_demo.py` prüft das mit Kanarienwerten.

**Was `PASS` zeigt:** Ein Job läuft den ganzen Pfad einmal durch — API-Key →
serverseitige Identität → Orchestrator, der den Handoff signiert → Gateway, das ihn
unabhängig prüft und einen einmaligen Permit ausstellt → Worker in eigenem Prozess →
signiertes Ergebnis → Ergebnisprüfung, die nur öffentliche Schlüssel hält → Audit-Kette,
deren signierter Kopf bei einem Anker in eigenem Prozess liegt. Jeder der fünfzehn
Angriffe (fremder Key, Tier oder Job-ID aus dem Request, unbekannte Route, Ergebnis-Replay,
abgelaufene Gültigkeit, Doppelannahme, falsche Rollenschlüssel, Payload-Tausch, Dispatch
ohne Permit, gekürzte oder zurückgesetzte Kette) wird abgelehnt.

**Was `PASS` nicht bedeutet:** keine Produktionsfreigabe und kein Sicherheitsnachweis
für einen echten Betrieb. Außer Worker und Audit-Anker sind die Instanzen getrennte
Objekte in einem Prozess; die Worker-Isolation ist eine Prozessgrenze, keine microVM:
Prozessstart sperrt der Kernel (Seccomp), alles Übrige ein Python-Audit-Hook, und ein
Worker darf Dateien des Hosts lesen; der Anker wird vom Dienst unter demselben Nutzer gestartet (seine
Zustandsdatei übersteht einen Neustart, schützt aber nicht vor Rückschnitt durch diesen
Nutzer); alle Signaturen (Handoff, Ergebnis, Audit-Kopf) sind Ed25519, aber alle
Schlüssel hängen an einem Root-Secret. Die bekannten Grenzen stehen einzeln in
`SECURITY.md`.

**v0.1 und `main`:** Der Tag `v0.1` ist für Commit `3a0e1bc` vorgesehen. Für ihn gilt
das README jenes Commits: keine Abhängigkeit, HMAC- statt Ed25519-Signaturen, und die
Demo endet mit `13/13 attacks refused`. `main` ist seither weiter; was dazugekommen ist,
steht in `docs/STATUS.md`.

**Andere Betriebssysteme:** Die Worker-Isolation braucht POSIX-Ressourcenlimits und einen
Seccomp-Filter, also Linux auf x86_64 oder aarch64. Unter Windows und macOS verweigert
sie die Ausführung (fail closed), die Demo erreicht dort kein `PASS`. WSL 2 ist Linux;
die Testsuite lief dort vor dem Seccomp-Filter (belegt in #28). Die CI läuft auf `ubuntu-latest`.

## Aktueller Stand

Roadmap zu v0.1 (`docs/ROADMAP-V01.md`): Schritte 1–19 umgesetzt; der Audit-Anker
aus Schritt 8 läuft in eigenem Prozess, sein Lebenszyklus liegt noch beim Dienst.
Schritt 20 (technischer Quickstart-Review) ist nach den vom Projektverantwortlichen
angepassten Abnahmekriterien abgeschlossen; der geprüfte Commit, die Umgebung und das
Ergebnis stehen in [docs/QUICKSTART-REVIEW.md](docs/QUICKSTART-REVIEW.md).
Schritt 21 (Tag `v0.1`) steht aus.

Die Phasennummern in `docs/MIGRATION-MATRIX.md` zählen die Migration aus dem
Altprojekt und sind nicht dieselben wie die Bauphasen hier.

Der gesamte Runtime-Code ist GeniusNew-nativ neu implementiert. Schlüssel sind
serverseitige Laufzeitkonfiguration und gehören niemals in dieses Repository; die Demo
leitet ihre Schlüssel aus einem festen Demo-Secret ab, das nur für die Demo gilt.

Siehe:

- `docs/STATUS.md` — aktueller Stand und nächste Schritte, auch als Quelle für NotebookLM
  und Microsoft 365 Copilot
- `AGENTS.md` — Regeln für KI-Assistenten (Claude Code, ChatGPT/Codex, Copilot, Gemini);
  Kurzfassung für Copilot in `.github/copilot-instructions.md`
- `docs/ADR-001-restart-and-isolation.md`
- `docs/MIGRATION-MATRIX.md` — kanonisches Import-Gate
- `docs/IMPORT-MANIFEST.md` — historische erste Inventur
- `SECURITY.md`
- `docs/HANDOFF-V2.md`
- `docs/APPROVAL-V1.md`
- `schemas/handoff-v2.schema.json`
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

Dieselben Schritte wie die CI (`.github/workflows/verify.yml`), in der venv aus dem
Quickstart:

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -v
python3 scripts/refusals.py geniusnew/<modul>.py   # ein Modul, wie eine Zeile der CI-Matrix
python3 scripts/refusals.py                        # alle Module in GUARDED
```

Der erste läuft in Sekunden und enthält den End-to-End-Test und die Demo. Der Refusal-Guard
schaltet jede Ablehnung im Code einzeln ab und verlangt, dass die Suite jedes Mal rot
wird; er führt die Suite dafür einmal pro Ablehnung aus und dauert entsprechend Minuten.
