# PostgreSQL-Fundament (Gate B1)

B1 installiert das Schema für `schema_migrations`, `job_ledger` und
`acceptance_ledger`. B2 bindet den Job-Ledger des HTTP-Dienstes an PostgreSQL:
Eine neue Instanz kann eine bereits persistierte Job-ID nicht erneut dispatchen.
B3 bindet auch den Annahme-Ledger an PostgreSQL: Eine angenommene Antwort bleibt
nach Neustart verbraucht; die gebundene Job-Zeile wechselt atomar zu `COMPLETED`.
B4 speichert wartende Jobs und Approval-Übergänge dauerhaft. B5 speichert die
Audit-Kette und jeden signierten Kopf. Die verbleibenden Grenzen stehen in
`SECURITY.md`.

## Voraussetzungen und Rollen

Python 3.11+, PostgreSQL (CI: Version 17), die Systembibliothek `libpq`
(Debian/Ubuntu: Paket `libpq5`) und `pip install --require-hashes -r requirements.txt`.
`psycopg` arbeitet synchron ohne Pool. Die Paket-Hashes stammen aus den
[PyPI-Metadaten für psycopg](https://pypi.org/pypi/psycopg/3.3.3/json) und
[typing-extensions](https://pypi.org/pypi/typing-extensions/4.16.0/json).

Vor der Migration müssen eine separate Core-Datenbank, ihre Migrationsrolle und die
Login-Rolle `genius_core` vorhanden sein. Rollenanlage und Zugangsdatenverwaltung sind
Betriebsaufgaben; der Migrationscode legt keine Login-Rollen oder Passwörter an.

- Die Migrationsrolle besitzt die Datenbank bzw. DDL-Rechte im Schema `public`.
- `genius_core` darf weder Superuser noch Datenbank-/Tabelleneigentümerin sein und
  keine Mitgliedschaft in einer privilegierten Rolle haben.
- Die Migration entzieht `PUBLIC` das Anlegen von Objekten im Schema `public` und
  Tabellenrechte. Die tabellengenauen Rechte für `genius_core` stehen in
  `docs/DATABASE.md` Abschnitt 10 und werden beim Start geprüft.
- Die Trigger sind mit Aufruferrechten ausgeführt; die Runtime kann sie nicht
  deaktivieren. Keine Core-Tabelle darf dem Portal Zugriff geben.
- Der Anker bleibt außerhalb der Core-Datenbank.

Die Tests prüfen diese Rechte und Trigger mit SQL über eine echte Runtime-Verbindung.
B1 nimmt keine Änderungen an einem Server oder Deployment vor.

## B2: persistenter Job-Ledger und Runtime-Startprüfung

`serve` übergibt den `PostgresJobLedger` sowohl beim eigenen als auch beim separat
betriebenen Anker. Reservierung und Ausführungs-Commit verwenden die Runtime-DSN.
Die Demo und Aufrufer von `build` ohne DB-Ledger verwenden weiterhin den
prozesslokalen Ledger. Persistierte Job-IDs
werden nicht freigegeben oder nach TTL gelöscht. Eine abgerissene DB-Verbindung
verweigert weitere Jobs bis zum Dienstneustart.

Vor Anker und Listener verweigert der Dienst eine Runtime-Rolle mit privilegierten
Rollenflags, Tabellenbesitz, überschüssigen Tabellen-/Schema-Rechten oder Zugriff
auf Server-Dateien/-Programme. Das gilt auch für Rechte über erreichbare Rollen.
`SET` oder `ALTER SYSTEM` auf `session_replication_role` sind verboten, weil sie die
Trigger umgehen; unter PostgreSQL 17 wird zusätzlich `MAINTAIN` geprüft. Bestehende
Ledger-Zeilen müssen die formalen Digest-, Zeit- und State-Vorgaben erfüllen.
Verbindlich sind die Runtime-Rechte in `docs/DATABASE.md`, Abschnitt 10.

Die Startprüfung benötigt mindestens **PostgreSQL 15**: Dort wurden die Parameter-
Rechte und `has_parameter_privilege` eingeführt ([Versionshinweis](https://www.postgresql.org/about/press/presskit15/),
[Funktion](https://www.postgresql.org/docs/15/functions-info.html)). Ein älterer
Server wird fail closed mit `database connection or operation failed` abgelehnt.
Die Tests benötigen **PostgreSQL 16 oder neuer**, da sie Mitgliedschaften mit
`GRANT ... WITH INHERIT FALSE, SET TRUE` erzeugen
([PostgreSQL 16 GRANT](https://www.postgresql.org/docs/16/sql-grant.html)).
Die erfolgreiche Suite wurde für PostgreSQL 16 und 17 belegt; PostgreSQL 15 ist
hiermit kein getesteter Support-Nachweis.

## B3: persistenter Annahme-Ledger

`serve` verwendet eine zweite Runtime-Verbindung für den `PostgresAcceptanceLedger`.
Nach vollständiger Prüfung von Handoff und Worker-Ergebnis schreibt eine Transaktion
die Annahme und setzt genau den zugehörigen Job von `EXECUTION_COMMITTED` auf
`COMPLETED`. Ein wiederholtes Ergebnis bleibt nach Neustart oder in einer zweiten
Instanz abgelehnt. Ein Datenbankfehler verweigert die Annahme ohne Ersatzspeicher.

Vor dem Listener werden die persistierten Annahmen und `COMPLETED`-Jobs in beide
Richtungen verglichen und die gespeicherten Wires mit den öffentlichen Schlüsseln
geprüft. Beschädigte oder nicht mehr zur aktuellen Policy passende Daten verweigern
den Start. B3 allein machte den Neustart nach einem Job noch nicht möglich;
erst B5 schließt die Audit-Persistenzgrenze.

## B4: wartende Jobs und Approval-Records

Die Migrationen `0002_pending_jobs` und `0003_approval_store` halten wartende
Handoff-Wires und unverzweigte Token-Historien. Ein verbrauchter Token bleibt
nach einem Neustart verbraucht. Grant, Consume, Pending-Auflösung und
Ledger-Übergang folgen den Transaktionen aus `docs/DATABASE.md` §4.4–4.5.

## B5: persistente Audit-Kette

`0004_audit_chain` speichert jedes kanonische Event und seinen bereits
signierten Kopf in derselben Transaktion. Beim Start prüft der Dienst die ganze
Kette, alle Köpfe und den getrennten Anker. Ist die DB konsistent voraus,
reicht er ausschließlich einen gespeicherten gültigen Kopf nach. Ein
unsignierter SQL-Suffix oder ein Anker-Vorlauf verweigert den Start. Die
Ledger↔Audit-Transaktionen und der bidirektionale Abgleich folgen erst mit B6.

Ein Kind-Anker mit `anchor_state` belegt den kanonischen Pfad und die Dateiidentität
seiner Zustandsdatei während seiner Laufzeit exklusiv. Eine zweite Instanz mit
demselben Pfad, einem Hardlink oder einem Bind-Mount auf diese Datei verweigert
den Start. Die Sperre nutzt den abstrakten Unix-Socket-Namensraum und gilt nur innerhalb
desselben Linux-Netzwerk-Namespace ([Linux `network_namespaces(7)`](https://man7.org/linux/man-pages/man7/network_namespaces.7.html)).
Mehrere Core-Instanzen, insbesondere in
getrennten Netzwerk-Namespaces, müssen einen einzigen separat betriebenen
Ankerdienst über `anchor_socket` nutzen und dürfen die Zustandsdatei nicht
zwischen Kind-Ankern teilen.

## Konfiguration und Migration

`service.database_dsn_file` in der TOML benennt eine absolute Datei außerhalb des
Repositories und der Worker-Allowlist, beispielsweise `/etc/geniusnew/database_dsn`.
Sie muss eine reguläre UTF-8-Datei des Dienstnutzers ohne Gruppen-/Fremdrechte sein
(Modus 0600, keine Symlinks, maximal 4096 Bytes). Ihr Inhalt ist eine libpq-DSN mit
explizitem `host`, `dbname` und `user`. Beispiel mit ausschließlich Platzhaltern:

```text
host=REPLACE_WITH_DB_HOST dbname=REPLACE_WITH_CORE_DATABASE user=genius_core password=REPLACE_WITH_RUNTIME_PASSWORD
```

Eine zweite, ebenso private Datei enthält die DSN der Migrationsrolle. Sie wird nur
für den manuellen Migrationsaufruf verwendet:

```sh
python -m geniusnew migrate --dsn-file /etc/geniusnew/migration_dsn
python -m geniusnew serve --config /etc/geniusnew/geniusnew.toml
```

`migrate` sperrt konkurrierende Migratoren mit einem transaktionalen Advisory Lock.
DDL, Grants und jeder Migrationseintrag committen gemeinsam.
Ein Fehler rollt die Transaktion zurück. Ein erneuter Aufruf prüft die vorhandene
Migration, ohne sie erneut anzuwenden. Fehlende Historie bei bestehenden Tabellen,
unbekannte Versionen und geänderte Checksums werden nicht repariert.

Checksum ist `sha256(exakte SQL-Dateibytes).hexdigest()`; auch eine zusätzliche
Leerzeile ändert sie. Bereits angewendete SQL-Dateien dürfen deshalb nicht verändert
werden. `serve` verlangt die vollständige bekannte Historie, passende Checksums,
gültige Migrationszeitwerte und alle erwarteten Tabellen/-Spalten. Diese
Prüfung läuft vor Ankerstart und HTTP-Listener. Es gibt keinen automatischen
Migrationslauf beim Dienststart. libpq-Fehlertexte und DSNs werden nicht ausgegeben.

Der Dienst prüft die Erreichbarkeit beim Start und hält die Verbindung bis zum
Dienstende. Seit B2 nutzt die laufende Job-Verarbeitung diese Verbindung für
persistente Reservierungen und Ausführungs-Commits.

## Lokale Tests und CI

Die vollständige Suite und jeder Refusal-Lauf brauchen einen **wegwerfbaren lokalen
PostgreSQL-Testcluster** mit Trust-Authentisierung für seine Testverbindungen. Niemals
Produktionszugangsdaten verwenden: Der Test-Administrator muss Rollen und Datenbanken
anlegen dürfen. Jede Fixture erzeugt eine eigene Datenbank mit zufälligem
`geniusnew_test_…`-Namen und löscht nur diese wieder. Die unprivilegierte Testrolle
`genius_core` wird bei Bedarf angelegt und bleibt im Testcluster bestehen.

```sh
export GENIUSNEW_TEST_ADMIN_DSN='host=127.0.0.1 port=REPLACE_WITH_TEST_PORT dbname=REPLACE_WITH_TEST_ADMIN_DATABASE user=REPLACE_WITH_TEST_ADMIN_ROLE'
python3 -W error::ResourceWarning -m unittest discover -s tests -v
./scripts/demo.sh
python3 scripts/refusals.py geniusnew/database.py geniusnew/config.py geniusnew/__main__.py
git diff --check
```

Ohne Test-DSN oder erreichbares PostgreSQL schlägt die Suite fehl; DB-Tests werden
nicht übersprungen. Die CI stellt PostgreSQL 17 für Tests und für jeden
Refusal-Matrixjob bereit. Die Demo selbst bleibt unabhängig von PostgreSQL.

Der zuerst ausgeführte Regressionstest
`StructureTest.test_a_missing_database_connection_is_refused` scheiterte auf der
Baseline `59741fe747287557dbf69cbb7ecb126252f4000e` mit
`AssertionError: ContractError not raised`. Der B1-Starttest prüft zusätzlich echte
PostgreSQL-Fehlerzustände vor Listener und Anker: unerreichbare DB, fehlende Migration
oder Migrationstabelle, falsche Checksum und unbekannte neuere Migration.
