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
| Der Dienst verwendet **PostgreSQL-Job- und Annahme-Ledger**; Demo und Aufrufer ohne DB-Ledger bleiben **prozesslokal** | Eine abgerissene DB-Verbindung verweigert weitere Jobs oder Annahmen bis zum Dienstneustart | `tests/test_orchestrator.py::PersistentLedgerTest`, `tests/test_acceptance_ledger.py::PersistentAcceptanceTest`, `tests/test_serve.py::ServeTest` |
| Gespeicherte Annahmen sind an die **aktuelle Policy und die aktuellen Prüfschlüssel** gebunden | Eine mit historischen Annahmen unvereinbare Policy-Änderung (auch ein bloßer Versionswechsel oder eine entfernte Berechtigung) oder Schlüsselrotation verweigert den Dienststart. Policy-Änderungen und Rotation brauchen den Ablauf aus Gate C5; die Schlüsselhistorie muss dabei berücksichtigt werden | `tests/test_acceptance_ledger.py::PersistentAcceptanceTest::test_changed_trusted_policy_or_keys_refuse_historical_acceptance`, `docs/ROADMAP-V02.md` Gate C5 |
| Prozesslokaler Annahme- und Job-Ledger sind auf **100 000** Einträge begrenzt und laufen nie ab; persistente Ledger haben kein Eintragslimit | Volle prozesslokale Ledger verweigern weitere Einträge bis zum Neustart (fail closed). Persistierte Job-IDs und angenommene Handoffs bleiben dauerhaft verbraucht | `_MAX_JOBS` in `geniusnew/orchestrator.py`, `_MAX_ACCEPTED` in `geniusnew/verifier.py`, `tests/test_acceptance_ledger.py::PersistentAcceptanceTest` |
| Alle drei Signierschlüssel werden aus **einem Root-Secret** abgeleitet, und es gibt **einen** Worker-Schlüssel für alle Worker | Prüfende Instanzen halten nur öffentliche Schlüssel (nach v0.1 geschlossen, vorher HMAC). Wer aber das Root-Secret hält, kann alles signieren, und ein Worker kann das Ergebnis eines anderen Workers fälschen | `geniusnew/keys.py`, Modul-Docstring von `geniusnew/results.py` |
| Der **Audit-Anker** läuft standardmäßig in eigenem Prozess, wird aber vom Dienst gestartet und unter **demselben Betriebssystem-Nutzer** betrieben | Der Schreiber kann ihn nicht aus dem Speicher zurücksetzen, aber beenden. Mit `anchor_state` übersteht die Festlegung einen Neustart (nach v0.1); wer die Zustandsdatei schreiben kann, kann sie aber auf einen älteren, gültig signierten Kopf zurückschneiden. Ohne `anchor_state` vergisst ein Neustart alles. **Nach v0.1:** Mit `anchor_process serve` läuft der Anker als eigener Dienst, jede Antwort ist über die Nonce der Anfrage signiert; `python -m geniusnew serve` bindet ihn über `anchor_socket` und `anchor_reply_public_key` an (Gate C2). Unter eigenem Nutzer betrieben (`docs/ANCHOR-SERVICE.md`), kann der Dienstnutzer ihn weder beenden noch seine Datei zurückschneiden. Das ist eine Eigenschaft der Installation und im Test nicht belegbar. Der Nutzer des Ankers, `root` und eine zurückgespielte Sicherung können die Datei weiterhin zurückschneiden | `tests/test_anchor_process.py::test_a_file_rolled_back_to_an_older_signed_head_is_accepted_and_this_is_the_boundary` (offen gehalten), `tests/test_anchor_process.py::AnchorClientTest`, `tests/test_serve.py::ServeTest::test_a_job_runs_behind_a_served_anchor_that_outlives_the_service` |
| Mehrere Kind-Anker dürfen dieselbe `anchor_state`-Datei **nicht über Linux-Netzwerk-Namespaces hinweg teilen** | Der exklusive Zustands-Lease bindet im selben Netzwerk-Namespace den kanonischen Pfad und die Dateiidentität (auch bei Hardlinks und Bind-Mounts). Über Namespace-Grenzen ist er nicht sichtbar; mehrere Core-Instanzen dort müssen einen einzigen separat betriebenen Anker über `anchor_socket` nutzen | `tests/test_anchor_process.py::AnchorProcessTest::test_two_child_anchors_cannot_write_the_same_state_file`, `test_state_lease_rejects_a_hardlink_alias_of_a_live_history`, `docs/POSTGRES-B1.md` B5 |
| **Approvals** werden nur serverseitig erteilt (`Service.approve`); der Dienst speichert wartende Jobs und Token-Übergänge in PostgreSQL, Demo und explizite Aufrufer ohne DB bleiben prozesslokal | Kein HTTP-Weg für Freigebende bis Gate C3; die Zustellung des Tokens an den Client ist Deployment. Die PostgreSQL-Zeilen überstehen Neustarts; mit B5 bleibt auch die Audit-Kette dauerhaft, sodass `serve` einen konsistenten Zustand wieder laden kann. B4 allein verweigerte den Neustart nach einem verankerten Ereignis. Die Demo ohne DB verliert ihren Zustand | `tests/test_pending_database.py::DurablePendingTest::test_restart_preserves_exact_pending_wire_and_consumed_token`, `tests/test_serve.py::ServeTest::test_a_restart_after_jobs_continues_the_durable_anchored_chain`, `docs/ROADMAP-V02.md` Gates B4, B5 und C3 |
| Worker-Isolation ist eine **Prozessgrenze**, keine microVM | Kernelseitig gesperrt sind der Start neuer Prozesse und Programme (Seccomp) sowie jeder Dateizugriff außerhalb der Allowlist und TCP `bind`/`connect` (Landlock, ABI ≥ 4). UDP und Unix-Sockets sperrt nur der Python-Audit-Hook; rohe Syscalls aus bereits geladenem nativem Code sieht er nicht. Ohne POSIX-Limits, Seccomp und Landlock ABI 4 läuft kein Worker (fail closed): Windows, macOS, andere Linux-Architekturen, 32-Bit-Interpreter und Kernel vor 6.7 | `docs/ISOLATION-V01.md`, `docs/ISOLATION-A2.md`, `tests/test_isolation.py` |
| Ein Worker darf die **Python-Laufzeit**, `geniusnew/` und das Verzeichnis seines Moduls **lesen** (Landlock-Allowlist, gleiche Prüfung im Audit-Hook) | Seine Ausgabe geht an den Client zurück: Ein Secret in einem dieser Verzeichnisse würde herausgegeben. Root-Secret, DB-Zugangsdaten und Ankerzustand gehören deshalb an einen eigenen Pfad (Betriebsanleitung, Gate C5); dort sind sie für den Worker unlesbar | `tests/test_isolation.py::ProcessIsolationTest::test_the_package_source_is_readable_and_this_is_the_boundary` (offen gehalten), `test_a_service_secret_outside_the_allowlist_cannot_be_read` |
| HTTP-Eingang ohne **TLS und Sessions**; unauthentifizierter Verkehr ist nur durch die Verbindungsgrenze begrenzt | TLS und ein Limit für unauthentifizierte Anfragen sind Deployment-Aufgabe (Reverse-Proxy, geprüfte Vorlage in `docs/REVERSE-PROXY.md`); der Eingang lauscht in der Demo nur auf `127.0.0.1`, und `python -m geniusnew serve` lehnt jede Nicht-Loopback-Adresse ab. Im Code begrenzt sind: Anfragen pro Principal (Token-Bucket, `429`), gleichzeitig laufende Jobs (`503`, keine Warteschlange) und offene Verbindungen | `geniusnew/http_entry.py`, `geniusnew/config.py`, `tests/test_http_limits.py` |
| Der Dienst hält die **Audit-Kette und jeden bereits signierten Kopf** in PostgreSQL; Demo und explizite Aufrufer ohne DB-Kette bleiben prozesslokal | Ein Neustart prüft die gesamte Kette und alle signierten Köpfe gegen den getrennten Anker. Nur bereits gespeicherte, gültig signierte Köpfe dürfen nachverankert werden; ein Anker-Vorlauf oder ein unsignierter SQL-Suffix verweigert den Start. Der Ankerzustand darf nie still zurückgesetzt werden | `tests/test_serve.py::ServeTest::test_a_restart_after_jobs_continues_the_durable_anchored_chain`, `tests/test_postgres_audit.py::PostgresAuditTest::test_sql_unsigned_suffix_is_a_start_refusal_not_a_recovery_signature` |
| Der Audit-Anker erhält bei jedem Commit die **vollständige Kette** über einen Transport mit 64 MiB Nachrichtenlimit; die Zustandsdatei ist auf 16 MiB begrenzt | Vor dem DB-Commit werden die größere Socket-Anfrage und die maximale Größe der Zustandsdatei geprüft; ein weiteres Event wird abgelehnt, bevor ein nicht mehr verankerbarer Kopf gespeichert wird. Die Kapazität hängt von der Eventgröße ab. Ein inkrementelles Protokoll ist eine spätere Änderung | `tests/test_postgres_audit.py::PostgresAuditTest::test_oversized_anchor_commit_refuses_before_a_database_commit` |
| Ledger-, Approval- und Audit-Zeilen sind in B5 noch **nicht atomar aneinander gebunden** | Eine formal gültige, per SQL eingefügte Job-Zeile ohne Audit-Ereignis verhindert den Start noch nicht; auch ein Absturz zwischen Ledger-Mutation und Audit-Append kann einen solchen Widerspruch hinterlassen. Gate B6 muss beide Richtungen vor dem Listener prüfen und Mutationen mit ihren Audit-Ereignissen gemeinsam committen | `tests/test_ledger_audit_boundary.py::LedgerAuditBoundaryTest::test_a_job_row_without_an_audit_event_is_accepted_and_this_is_the_boundary` (offen gehalten), `docs/ROADMAP-V02.md` Gate B6 |
| API-Key-Digests sind **ungesalzen** | Richtig für zufällige Maschinenschlüssel, falsch für menschlich gewählte | Modul-Docstring von `geniusnew/http_entry.py` |

Eine Grenze aus dieser Liste zu schließen ist eine eigene, begründete Änderung mit Test,
kein Nebeneffekt.

## Meldung von Schwachstellen

Keine sensitiven Schwachstellendetails, Tokens oder Exploit-Daten in öffentliche Issues schreiben. Sicherheitsfunde zunächst über einen privaten, geeigneten Kanal des Repository-Eigentümers melden.
