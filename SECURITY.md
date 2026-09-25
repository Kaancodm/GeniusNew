# Security Policy

## Scope

GeniusNew ist ein öffentliches Zero-Trust-Projekt. Sicherheit hat Vorrang vor schneller Codeübernahme.

## Keine Secrets im Repository

Niemals committen:

- API-Keys, Tokens, Passwörter oder private Schlüssel
- echte `.env`-Dateien
- produktive Datenbanken oder Dumps
- personenbezogene oder vertrauliche Daten
- interne Zugangsdaten, Deployment-Secrets oder private Endpunkte

Nur redigierte Beispiele mit eindeutig ungefährlichen Platzhaltern dürfen öffentlich versioniert werden.

## Altprojekt-Import

`Kaancodm/Agent-Genius` ist ausschließlich Read-only-Quelle. Kein Pfad wird automatisch übernommen. Vor jedem Import sind mindestens Herkunft, Sicherheitswirkung, Projektzugehörigkeit und Public-Repository-Eignung zu prüfen.

Insbesondere werden Artefakte mit Agent-Common-Bezug nicht übernommen. Wenn ein fachlich brauchbares Konzept aus einem vermischten Altartefakt stammt, wird es für GeniusNew neu aufgebaut statt blind kopiert.

## Zero-Trust-Grundsätze

- Fail closed statt implizit erlauben.
- Untrusted Daten strikt validieren.
- Identität und Berechtigungen serverseitig bestimmen.
- Keine Selbstfreigabe sicherheitskritischer Agentenaktionen.
- Tool-Nutzung über explizite Allowlist und Approval-Gates.
- Auditdaten dürfen keine Nutzdaten oder Secrets leaken.
- Sicherheitsrelevante Grenzen müssen testbar und reproduzierbar sein.

## Bekannte Grenzen von v0.1

v0.1 ist kein Sicherheitsnachweis für einen echten Betrieb (`docs/ROADMAP-V01.md`). Die
folgenden Grenzen sind bewusst offen. Belege mit **(offen gehalten)** sind Tests, die die
Lücke selbst behaupten: sie werden rot, sobald jemand die Grenze schließt, ohne diese
Liste anzupassen. Die übrigen Belege zeigen, wo die Grenze im Code oder in der
Dokumentation steht.

| Grenze | Folge | Beleg |
| --- | --- | --- |
| Alle Instanzen außer Worker und Audit-Anker laufen in **einem Prozess** | §8-Trennung ist für sie logisch, keine Speichertrennung. Entschieden: reicht für v0.1 | `docs/ROADMAP-V01.md` Schritte 13 und 17 |
| Job-Ledger des Orchestrators ist **prozesslokal** | Neustart oder zweite Instanz mit gleicher Kennung dispatcht denselben unverfallenen Handoff erneut | `tests/test_orchestrator.py::test_the_ledger_is_process_local_and_this_is_the_boundary` (offen gehalten) |
| Annahme-Ledger der Ergebnisprüfung ist **prozesslokal** | Neustart nimmt dasselbe Ergebnis erneut an | `tests/test_verifier.py::test_the_ledger_is_process_local_and_this_is_the_boundary` (offen gehalten) |
| Beide Ledger sind auf **100 000** Einträge begrenzt und laufen nie ab | Danach lehnt die Instanz alles ab (fail closed), bis sie neu startet | `_MAX_JOBS` in `geniusnew/orchestrator.py`, `_MAX_ACCEPTED` in `geniusnew/verifier.py` |
| Der **Audit-Anker** hält nur **Speicher** | Ein Neustart des Ankers vergisst alle Festlegungen. Ein Neustart des Dienstes nicht mehr: Start und Stopp liegen beim eigenen Pfad `anchor_process.start`, der Dienst hält nur den Socket-Pfad. Persistenz ist offen: `docs/ADR-002-anchor-persistence.md` | `tests/test_anchor_process.py::test_a_restarted_anchor_remembers_nothing_and_this_is_the_boundary` (offen gehalten) |
| Der **Audit-Anker** läuft unter **demselben Betriebssystem-Nutzer** wie der Dienst | Ein Prozess dieses Nutzers kann ihn per Signal beenden; `verify` lehnt dann ab (fail closed), der Anker ist aber weg. Ein eigener Nutzer ist Deployment | `tests/test_anchor_process.py::test_an_anchor_that_is_gone_fails_closed` |
| Die Ergebnissignatur ist **HMAC** (symmetrisch); Handoffs und Audit-Köpfe sind nach v0.1 Ed25519 | Wer eine Ergebnissignatur prüfen kann, kann sie auch erzeugen; Unabhängigkeit dort ist organisatorisch, nicht kryptografisch. Für Handoffs und Audit-Köpfe gilt das nicht mehr: Gateway, Ergebnisprüfung und Anker halten nur öffentliche Schlüssel | `tests/test_verifier.py::test_the_symmetric_key_means_this_instance_could_also_sign` (offen gehalten) |
| **Approvals** werden nur serverseitig erteilt (`Service.approve`), wartende Jobs liegen prozesslokal | Kein HTTP-Weg für Freigebende; die Zustellung des Tokens an den Client ist Deployment. Ein Neustart verliert wartende Jobs | Docstring von `Service.approve` in `geniusnew/wiring.py` |
| Worker-Isolation ist eine **Prozessgrenze**, keine microVM | Kein Schutz gegen bereits geladenen nativen Code oder rohe Syscalls; nur unter Linux: ohne POSIX-Limits (Windows) oder mit abgelehntem Adressraum-Limit (macOS) keine Ausführung — Windows über WSL | `docs/ISOLATION-V01.md`, `tests/test_isolation.py`, Jobs `macos` und `windows` in `.github/workflows/verify.yml` |
| HTTP-Eingang ohne **TLS, Rate-Limiting, Sessions** | Deployment-Aufgabe; der Eingang lauscht in der Demo nur auf `127.0.0.1` | `geniusnew/http_entry.py` |
| API-Key-Digests sind **ungesalzen** | Richtig für zufällige Maschinenschlüssel, falsch für menschlich gewählte | Modul-Docstring von `geniusnew/http_entry.py` |

Eine Grenze aus dieser Liste zu schließen ist eine eigene, begründete Änderung mit Test,
kein Nebeneffekt.

## Meldung von Schwachstellen

Keine sensitiven Schwachstellendetails, Tokens oder Exploit-Daten in öffentliche Issues schreiben. Sicherheitsfunde zunächst über einen privaten, geeigneten Kanal des Repository-Eigentümers melden.
