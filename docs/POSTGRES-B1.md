# PostgreSQL-Fundament (Gate B1)

B1 installiert ausschließlich das Schema für `schema_migrations`, `job_ledger` und
`acceptance_ledger`. Job- und Ergebnisverarbeitung verwenden weiterhin ihre
prozesslokalen Ledger. Neustartsicherheit, gespeicherte Wire-Prüfungen und
Audit-Reconciliation folgen in B2–B6; die Grenzen in `SECURITY.md` gelten unverändert.

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
  Tabellenrechte. `genius_core` erhält Schema-USAGE, SELECT auf `schema_migrations`,
  SELECT/INSERT/UPDATE auf `job_ledger` und SELECT/INSERT auf `acceptance_ledger`.
- Die Trigger sind mit Aufruferrechten ausgeführt; die Runtime kann sie nicht
  deaktivieren. Keine Core-Tabelle darf dem Portal Zugriff geben.
- Der Anker bleibt außerhalb der Core-Datenbank.

Die Tests prüfen diese Rechte und Trigger mit SQL über eine echte Runtime-Verbindung.
B1 nimmt keine Änderungen an einem Server oder Deployment vor.

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
DDL, Grants und Migrationseintrag von `0001_core_foundation` committen gemeinsam.
Ein Fehler rollt die Transaktion zurück. Ein erneuter Aufruf prüft die vorhandene
Migration, ohne sie erneut anzuwenden. Fehlende Historie bei bestehenden Tabellen,
unbekannte Versionen und geänderte Checksums werden nicht repariert.

Checksum ist `sha256(exakte SQL-Dateibytes).hexdigest()`; auch eine zusätzliche
Leerzeile ändert sie. Bereits angewendete SQL-Dateien dürfen deshalb nicht verändert
werden. `serve` verlangt die vollständige bekannte Historie, passende Checksums,
gültige Migrationszeitwerte und vorhandene Foundation-Tabellen/-Spalten. Diese
Prüfung läuft vor Ankerstart und HTTP-Listener. Es gibt keinen automatischen
Migrationslauf beim Dienststart. libpq-Fehlertexte und DSNs werden nicht ausgegeben.

B1 prüft die Erreichbarkeit beim Start und hält die Verbindung bis zum Dienstende.
Es koppelt die laufende Job-Verarbeitung noch nicht an Datenbanktransaktionen.

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
