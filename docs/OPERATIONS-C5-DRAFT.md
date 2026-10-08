# Betrieb und Recovery — Gate C5

Dieser Entwurf aktiviert keinen Dienst. B3/B4/B5/B6/B7, unabhängige Reviews und
der frische Host-Nachweis müssen am selben vollständigen Commit bestanden sein.
Der bestehende Server ist ein Entwicklungsserver. Der vorhandene PostgreSQL-
Testcluster ist keine Produktivdatenbank.

Kaan hat am 04.10.2026 für C5 entschieden: Der unabhängig gesicherte Ankerstand
ist eine nicht absenkbare Untergrenze. Fehlt bei einem älteren DB-Backup die
Historie bis zu diesem Stand und ist sie nicht rekonstruierbar, bleibt der Dienst
im Zustand **HOLD**. Die eigene Worker-OS-UID wird für C5 ausdrücklich
verschoben; bis zur gesonderten Umsetzung gilt das getestete Landlock-Mindestziel.
Am 05.10.2026 legte Kaan Backblaze B2 EU Central mit Object Lock und zunächst
30 Tagen Aufbewahrung als Offsite-Ziel fest. Ein Konto oder Bucket wurde damit
noch nicht angelegt.

## Installationsvertrag

Die Installation hält vier Verantwortungen getrennt: Anker, Kern, Worker und
Portal. Ankerzustand und Anker-Antwortschlüssel gehören ausschließlich dem
Ankernutzer. Die Core-Runtime darf sie weder lesen, schreiben noch zurücksetzen.
Die gemeinsame Socketgruppe gewährt nur Kommunikation mit dem Anker.

Die Beispiel-Units und die **C5-TOML-Vorlage**
`docs/examples/geniusnew-c5.toml` liegen unter `docs/examples/`. Nur diese
Vorlage aktiviert den unabhängigen Socket-Anker; die allgemeine
`docs/examples/geniusnew.toml` zeigt noch den Kind-Anker und ist für C5
unverändert ungeeignet. Vor Aktivierung sind die Pfade, der geprüfte
vollständige SHA und der öffentliche Audit-Schlüssel einzusetzen.
Das Programm liegt unter /opt/geniusnew, Secrets in privaten Dateien außerhalb
von Git. Migrationen laufen separat mit der Migrationsrolle; der Kern bekommt
ausschließlich die geprüfte Runtime-DSN. Ein fehlendes Feld verhindert den Start.

Die Worker laufen heute noch als Kindprozess des Kerns. Eine eigene systemd-
User-Zeile allein ändert ihre Identität nicht. Der gesonderte Worker-OS-Nutzer
braucht einen tatsächlich unterstützten Startpfad und einen Negativtest.
Bis dahin ist lediglich das Landlock-Mindestziel belegt. Diese Verschiebung ist
Kaans Entscheidung für C5 vom 04.10.2026, keine Behauptung über den Ist-Zustand.

### Dienst-Installation nach Freigabe

Die zwei Beispiele unter `docs/examples/` sind Vorlagen für einen eigens
freigegebenen Host, keine ausführbare Erstinstallation. Der geprüfte Commit wird
unveränderlich unter `/opt/geniusnew` bereitgestellt. Danach legt der Operator
die beiden Systemnutzer `geniusnew-anchor` und `geniusnew` sowie die gemeinsame
Socketgruppe `geniusnew-audit` an. Der Kernnutzer erhält diese Gruppe ergänzend,
der Ankernutzer bekommt keinen Zugriff auf die Core-DB oder das Root-Secret.

`/var/lib/geniusnew-anchor` gehört nur `geniusnew-anchor` und ist `0700`;
`/etc/geniusnew` gehört `geniusnew` und ist `0700`. Darin liegen
`geniusnew.toml` und die privaten Dateien `root_secret` und `database_dsn`.
Die beiden privaten Dateien gehören `geniusnew`, sind reguläre Dateien mit
Modus `0600` und keine Symlinks. Die TOML nennt ausschließlich
`anchor_socket` und `anchor_reply_public_key`, nie zusätzlich `anchor_state`.
Ihr `listen_host` wird privat auf Loopback gesetzt; der Reverse-Proxy aus
`docs/REVERSE-PROXY.md` übernimmt TLS und äußere Limits.

Nach Abgleich von Pfaden, Nutzern, Schlüsseln und vollständigem Commit-SHA:

```sh
sudo install -m 0644 docs/examples/geniusnew-anchor.service /etc/systemd/system/geniusnew-anchor.service
sudo install -m 0644 docs/examples/geniusnew-core.service /etc/systemd/system/geniusnew-core.service
sudo systemctl daemon-reload
sudo systemctl enable --now geniusnew-anchor.service
sudo systemctl is-active geniusnew-anchor.service
sudo systemctl enable --now geniusnew-core.service
sudo systemctl is-active geniusnew-core.service
```

Der Platzhalter `AUDIT_PUBLIC_KEY_HEX` in der Anker-Unit muss vorher durch den
öffentlichen Audit-Schlüssel ersetzt sein; seine private Hälfte und das
Root-Secret erscheinen nie in Unit, Kommandozeile oder Journal. Die Antwort-
Schlüsseldatei des Ankers bleibt unter seiner UID. Der öffentliche Antwort-
Schlüssel aus `anchor_process public-key` gehört in die Core-TOML. Ein fehlender
Socket oder ein falscher Antwortschlüssel muss den Core-Start verweigern.
`systemctl enable` ist erst nach der gesonderten Deployment-Freigabe erlaubt.

Den öffentlichen Audit-Schlüssel leitet der Core-Nutzer lokal aus seiner
privaten Root-Datei ab. Nur die öffentliche Ausgabe wird in die Anker-Unit
übernommen:

```sh
cd /opt/geniusnew
sudo -u geniusnew .venv/bin/python - <<'PY'
from geniusnew.audit import AuditAuthority
from geniusnew.config import read_root_secret
from geniusnew.keys import derive_keys

root = read_root_secret('/etc/geniusnew/root_secret')
audit = AuditAuthority(audit_key=derive_keys(root).audit_key)
print(audit.verifier().public_key.hex())
PY
```

Vor der Aktivierung nachweisen:

1. Kern und Anker haben verschiedene UIDs; Kern kann Ankerdateien nicht öffnen.
2. Worker liest weder Root-Secret noch Runtime-DSN noch Ankerzustand.
3. Der Kern bindet nur an Loopback; TLS und äußere Limits liegen am Proxy.
4. Alle Tabellenrechte entsprechen DATABASE §10; Startprüfung verweigert
   Superuser, Owner, Rollen-Bypass und zusätzliche Rechte.
5. Der Anker läuft unabhängig. SIGTERM des Kerns beendet ihn nicht.
6. Die TOML enthält explizite HTTP-Limits; dieselben Werte gelten am Proxy.

## PostgreSQL 17, Verbindung und Rollen

**Auftrag Kaan, 08.10.2026; Entwurf, keine Installationsfreigabe.** Maßgebliche
Codebasis: `7f81e240f6ec4b16d876f081249afb3d47240901`. PostgreSQL **17** wie
`postgres:17` in `.github/workflows/verify.yml`; auch `initdb`, `pg_dump` und
`pg_restore` stammen aus Major 17. Das bewegliche CI-Tag ist kein unveränderlicher
Deployment-Pin: konkrete Minorversion und Paket-/Image-Digest ins private
Abnahmeprotokoll aufnehmen. Eine andere Majorversion braucht begründete
Abweichung, Claude-Review, Kaans Freigabe und denselben vollständigen Testlauf.
Die ausschließlich für Wegwerf-CI konfigurierte `trust`-Authentisierung wird
**nicht** in den Betrieb übernommen.

### Neucluster mit Datenchecksums

Nur für ein neues, leeres, von Kaan freigegebenes Datenverzeichnis als dessen
PostgreSQL-OS-Nutzer; nie auf einem vorhandenen Cluster ausführen:

```sh
initdb --version
initdb --data-checksums --auth-local=peer --auth-host=reject --pgdata="$PGDATA"
```

`PGDATA` wird ausschließlich privat festgelegt. Vor dem ersten Start TCP gemäß
`examples/postgres-c5.conf` deaktivieren. In der freigegebenen Bootstrap-Sitzung:

```sql
SHOW server_version_num;  -- 170000 <= value < 180000
SHOW data_checksums;      -- on
```

[`initdb --data-checksums`](https://www.postgresql.org/docs/17/app-initdb.html)
schaltet Seitenprüfsummen ein. `off` ist ein Installations-HOLD, kein Anlass,
einen vorhandenen Cluster zu löschen oder automatisch neu anzulegen.
Seitenprüfsummen ersetzen weder Signaturen/Audit-Anker noch Backup oder eine
Restore-Probe. Auch am restaurierten Prüfcluster beide `SHOW`-Nachweise erheben.

### Ein Host: Unix-Socket und peer, ohne Passwort

`examples/postgres-c5-local.pg_hba.conf` und
`examples/postgres-c5.pg_ident.conf` erlauben nur die drei exakten Paare:
`geniusnew-migrate → genius_migrate`, `geniusnew → genius_core` und
`geniusnew-backup → genius_backup`, jeweils für die Core-DB `geniusnew`.
Es gibt keinen Passwort-Fallback und keine pauschale Admin-/Portal-/Replikations-
Freigabe. [Peer](https://www.postgresql.org/docs/17/auth-peer.html) prüft die
OS-Identität; die Map ist nötig, weil OS- und DB-Rollennamen verschieden sind.

Der private Runtime-DSN-Inhalt hat die Form
`host=/run/geniusnew-postgresql dbname=geniusnew user=genius_core`.
`_connect` verlangt **explizit** `host`, `dbname` und `user`; ein bloßes
`service=...` erfüllt diesen Parser-Vertrag nicht. `host` ist hier ein
Socketverzeichnis, kein Hostname. Auch ohne Passwort bleibt die DSN-Datei privat,
`0600`, außerhalb der Worker-Allowlist. Keine Passwortdatei für diesen Modus.

Das Socketverzeichnis gehört dem PostgreSQL-OS-Nutzer und der eigenen Gruppe
`geniusnew-db`, Modus `0750`; die Socketdatei hat Gruppe `geniusnew-db`, Modus
`0770`. Nur PostgreSQL-, Kern-, Migrations- und Backup-OS-Nutzer erhalten die
nötige Gruppenzugehörigkeit. Die Core-Unit nennt zusätzlich `geniusnew-db`;
der Ankernutzer gehört **nicht** hinein. Gruppen, Verzeichnis und Neustart sind
spätere, ausdrücklich freizugebende Operatorhandlungen.

**Offene Worker-Grenze:** Ein Worker läuft derzeit mit Core-UID und kann durch
`peer` nicht vom Kern unterschieden werden. `SECURITY.md` dokumentiert für
Unix-Sockets nur den Python-Audit-Hook, keine rohe Syscall-Sperre. Socketmodus,
DSN-Geheimhaltung und Connection-Limit schließen diese Grenze nicht. Vor
Deployment muss Claude diesen Pfad bewerten; ohne belastbaren Negativnachweis
oder Kaans ausdrückliche Entscheidung zum konkreten Restrisiko bleibt die
Peer-Installation in HOLD. Keine Sandbox-Abschwächung als Lösung.

### Getrennte Hosts: ausschließlich TLS und SCRAM

Alternativ `examples/postgres-c5-tcp.pg_hba.conf`: nur `hostssl` mit
`scram-sha-256` für dieselben drei Rollen und jeweils kleinste privat
festgelegte Client-CIDRs. Unersetzte Platzhalter sind **keine installierbare
Konfiguration**. Kein Zusammenkopieren beider HBA-Vorlagen; keine zusätzliche
`host ... trust/md5/password`-Regel. Die abschließenden `reject`-Regeln erfassen
sonstige DBs/Rollen, Klartext, IPv4/IPv6 und physische Replikation.

Serverseitig TLS einschalten, Zertifikat/Schlüssel privat bereitstellen und
`password_encryption=scram-sha-256` prüfen. Diese Einstellung ändert bestehende
Passwort-Verifier nicht: SCRAM-Nachweis nur als boolesches Ergebnis, niemals
Verifier ausgeben; eine nötige Passwortsetzung braucht eigene Freigabe.
Clientseitig ist der private DSN vollständig, beispielsweise:

```text
host=<DB_CERTIFICATE_NAME> dbname=geniusnew user=genius_core sslmode=verify-full sslrootcert=<PRIVATE_CA_FILE> passfile=<PRIVATE_PASSFILE> gssencmode=disable
```

[`verify-full`](https://www.postgresql.org/docs/17/libpq-ssl.html) prüft CA-Kette
und Servernamen. `hostssl` allein erzwingt diese Clientprüfung **nicht**.
`gssencmode=disable` verhindert, dass GSS-Verschlüsselung den expliziten TLS-Pfad
ersetzt. Kein Fallback auf `require`, `prefer` oder unverschlüsseltes TCP.
CA-Datei vor fremder Änderung schützen; Passwortdatei `0600`, nie im Repository,
in Befehlsargumenten oder im Journal. Firewall-/Listeneränderungen nur nach
separater Freigabe; kein öffentlicher DB-Endpunkt.

### Bootstrap und genius_core

Vor Migration Rollen gemäß `docs/POSTGRES-B1.md` anlegen: `genius_migrate`
besitzt DB/Schema, `genius_core` und `genius_backup` sind Nicht-Eigentümer.
Alle drei Login-Rollen sind `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
NOBYPASSRLS`, ohne gegenseitige oder privilegierte Rollenmitgliedschaft. Im
lokalen Modus werden die neuen Rollen ohne Passwort angelegt. Vorhandene Rollen
nicht ungeprüft übernehmen oder ihre Rechte/Secrets automatisch reparieren.
Nach Migrationen 0001–0004 enthält `examples/postgres-c5-roles.sql` die
**erst nach Freigabe** auszuführenden Betriebs-DDL; kein Migrationscode wird
geändert. Extension-Anlage erfolgt erst danach und außerhalb von `public`.

`genius_core CONNECTION LIMIT 1`: `_serve` in `geniusnew/__main__.py` öffnet
**eine** Verbindung und teilt sie zwischen allen Stores; kein Pool. Der ältere
B3-Absatz in `docs/POSTGRES-B1.md` nennt noch zwei Verbindungen und ist gegenüber
diesem Code veraltet. Backup/Monitoring verwenden `genius_backup`; die
Core-Prüfverbindung wird nur bei gestopptem Kern geöffnet. Mehrere aktive
Core-Instanzen brauchen einen neu begründeten Wert und Freigabe. PostgreSQL
prüft das [Connection-Limit nur näherungsweise](https://www.postgresql.org/docs/17/sql-createrole.html):
es ist eine Ressourcenbegrenzung, keine Exactly-once-/Singleton-Garantie.

`REVOKE TEMPORARY ON DATABASE geniusnew FROM PUBLIC` entfernt die übliche
Temp-Freigabe; auch direkte bzw. geerbte TEMP-/CREATE-Rechte der Runtime dürfen
nicht bleiben. Tabellenrechte kommen unverändert ausschließlich aus den
Migrationen. `_check_runtime_role` verweigert erreichbare privilegierte Rollen,
Core-Tabellenbesitz, Schema-CREATE, Trigger-Bypass über `session_replication_role`
und jede abweichende Tabellen-Rechtemenge, in PG17 einschließlich `MAINTAIN`.
Die Runtime erhält keine Monitoring-, Serverdatei- oder Administrationsrolle.
`api_key_digests` und Portal-/D1-Tabellen sind hier noch keine installierten
Core-Tabellen und werden nicht ad hoc angelegt.

Die [Rollen-Defaults](https://www.postgresql.org/docs/17/sql-alterrole.html)
setzen zusätzlich `statement_timeout=10s`, `lock_timeout=5s` und
`search_path=pg_catalog,public`, passend zu `_connect`. Entwurfswert
`idle_in_transaction_session_timeout=60s` begrenzt hängende Transaktionen;
Start-/Kettenprüfung müssen unter diesem Wert in der Probe bestehen.
Keinen positiven `idle_session_timeout` für die langlebige Core-Verbindung
aktivieren. Rollen-Defaults sind kein Schutz gegen absichtliches `SET`;
datenbankspezifische Overrides und frische Login-Sessions zusätzlich prüfen.
`SET ROLE` übernimmt diese Defaults nicht. `_check_runtime_role` prüft
Connection-Limit, TEMP und diese Defaults **nicht**: sie bleiben Betriebsgates.

Der finale HBA-Satz erlaubt absichtlich keinen Cluster-Admin-Login. Alle
Bootstrap-/Extension-/Rollenarbeiten vor seinem Abschluss erledigen, eine
bereits autorisierte Admin-Sitzung bis zur Prüfung neuer Verbindungen offen
halten und danach schließen. Spätere Cluster-Admin-Arbeiten benötigen einen
separaten, freigegebenen Offline-Wartungsweg unter der PostgreSQL-OS-Identität;
keine dauerhafte zusätzliche HBA-Allow-Regel. Auf einem gemeinsam genutzten
Cluster diese exklusive HBA-Vorlage nicht anwenden.

**Abnahme auf der isolierten Installation:** `pg_hba_file_rules` muss ohne
Parserfehler sein; maßgeblich sind zusätzlich erfolgreiche bzw. verweigerte
**neue** Verbindungen nach Reload, nicht nur die Datei auf Disk
([HBA-Vertrag](https://www.postgresql.org/docs/17/auth-pg-hba-conf.html)).
Drei Rollen positiv prüfen; fremde UID/Role, Portal, falsche DB, Replikation und
unerlaubter TCP-Pfad negativ. Bei TLS falsche CA, falscher Zertifikatsname,
fehlendes TLS und falsches Passwort negativ prüfen. `SHOW` der Timeouts und
`search_path`, `rolconnlimit=1`, `has_database_privilege('genius_core',
'geniusnew','TEMP')=false`, kein DB-CREATE sowie `_check_runtime_role` über
`open_database` und danach die vollständige Service-Startprüfung nachweisen.
Eine zweite gleichzeitige Core-Verbindung soll verweigert werden. Kein
Negativtest auf der produktiven DB, keine unveränderte Admin-Sitzung als
Nachweis einer korrekten neuen Anmeldung.

## Monitoring ohne Nutzdaten

`examples/postgres-c5.conf` bereitet
[`pg_stat_statements`](https://www.postgresql.org/docs/17/pgstatstatements.html)
vor: Preload benötigt einen freigegebenen PostgreSQL-Neustart;
`compute_query_id=on`, `track=top`, `track_utility=off`, `track_planning=off`
und `save=off`. Bestehende freigegebene Preload-Einträge nicht überschreiben.
Die Extension liegt im gesperrten Schema `c5_stats`; nur die Backup-Rolle erhält
die benötigte Funktionsausführung, **kein** `pg_read_all_stats`/`pg_monitor`.

`examples/postgres-c5-monitoring.sql` ist ausschließlich lesend. Der Aufruf
`pg_stat_statements(false)` und die feste Spaltenauswahl liefern aggregierte
Core-Aufrufzahlen, Laufzeiten, Block-/WAL-Zähler und die Größen der acht
installierten Core-Tabellen. Kein `SELECT *`, kein `query`, `queryid`, Principal,
Token, `wire`, `event` oder Row-Sample im Export. `max_exec_ms` ist ein Maximum,
kein Perzentil. Vorhandene Query-Statistiken sind kumulativ; Neustart/Reset
markiert eine neue Messperiode. Tabellen-Gesamtgröße schließt Indizes/TOAST ein.

**Grenze:** `pg_stat_statements` hält intern repräsentative SQL-Texte; dessen
Normalisierung ist keine garantierte Anonymisierung. Deshalb ausschließlich
parametrisierte Anwendungsqueries, keine Utility-Erfassung, kein Export des
Textspeichers und kein SQL-/Log-Rohtext im Dashboard. Das private DB-Logging
wird durch diesen Entwurf nicht abgeschaltet oder als datensicher behauptet.
Die Backup-Rolle kann wegen ihres Dump-Auftrags Nutzdaten lesen; ihr Zugang
bleibt privat. Export erlaubt nur die genannten numerischen Metriken und
festen Tabellennamen, nicht den Zugang selbst.

Warnung sobald **eine** Schwelle erreicht ist:

| Messwert | Warnung ab | Bezugsgrenze |
| --- | ---: | ---: |
| tatsächliche Audit-Zeilen (`count(*)`) | 43496 | 62137 Records |
| tatsächliche Ankerdateigröße | 11744052 Byte (aufgerundet, etwa 11,2 MiB) | 16777216 Byte (16 MiB) |

Beide Schwellen sind `ceil(0.70 * Grenze)`. Die Record-Grenze folgt dem aktuellen
Zustandsformat; große Events können zuvor das separate 64-MiB-Transportlimit
erreichen. Warning ist keine Kapazitätsgarantie. Im Code bleiben die
Größenprüfungen vor dem Commit maßgeblich; nichts wird gekürzt oder gelöscht.
Auch abgelaufene Ledger-/Pending-Zeilen nicht per Betriebs-SQL entfernen.

Ankergröße nur als Anker-OS-Nutzer bzw. autorisierter Operator mittels
`stat --format='%s' /var/lib/geniusnew-anchor/anchor.state` erfassen; reguläre
Datei ohne Symlink verlangen. Kein Lesezugriff auf Ankerinhalt für Core oder
Backup-Monitoring und keine breite sudo-Freigabe. Fehlende/unlesbare Datei,
fehlende Statistik-Extension, Timeout, unvollständige Tabellenliste oder
veralteter Messzeitpunkt sind Monitoringfehler, niemals null oder grün.

Entwurfsrhythmus: Kapazität jede Minute, SQL-Aggregate/Tabellengrößen alle fünf
Minuten, Alter und Wachstumsrate mitführen. Ab Warnung Kaan informieren,
verifiziertes Backup und verbleibende Kapazität prüfen, Last kontrolliert
begrenzen; vor Erschöpfung Wartungs-HOLD statt unkontrollierter Auftragsannahme.
Inkrementelles Audit/Anker-Protokoll ist ein gesonderter DB-Auftrag, kein hier
behaupteter Fix. Collector, Scheduler und Alarme werden hier nicht installiert.
Nach einem Verbindungsabbruch verweigert der aktuelle Dienst Jobs bis zum
Neustart (`SECURITY.md`); `Restart=on-failure` wirkt nur bei tatsächlichem
Prozessende und ist noch kein automatischer DB-Reconnect-/Exit-Code-Vertrag.

## Backup ohne unvollständiges Paar

Ein Backup umfasst Core-Datenbank, getrennten Ankerzustand, öffentliche
Schlüsselfingerprints, Migrationschecksums, Code-SHA und einen privaten Manifest.
Produktiv-Secrets und private Schlüssel werden gesondert verschlüsselt gesichert;
sie gehören nie in ein Git- oder öffentliches Evidenzartefakt.

Reihenfolge für einen konsistenten Wartungsbackup:

1. Neue Aufträge sperren und laufende Requests geordnet beenden.
2. Kern stoppen; Ankerzustand erhalten, Anker nicht zurücksetzen.
3. Persistierte Kette und gespeicherten signierten Kopf prüfen.
4. Den signierten Ankerstand unabhängig abfragen und gegen denselben Kopf prüfen.
5. Erst bei gleichem count/head_hash DB-Snapshot und Ankersnapshot sichern.
6. Manifest mit beiden Hashes, SHA, UTC und Ergebnis erzeugen; getrennten
   unveränderlichen Ablageort für den zuletzt bestätigten Ankerstand verwenden.
7. Backup verschlüsselt an den bestätigten externen Speicher übertragen und
   Lesbarkeit/Integrität dort prüfen. Fehlende Bestätigung ergibt kein Backup-PASS.
8. Kern nur nach erneut erfolgreicher Startprüfung freigeben.

Der Gleichstand aus Schritt 4 lässt sich nach dem Stop aller Core-Schreiber
lesend prüfen. Der Befehl läuft aus `/opt/geniusnew` als Core-Nutzer und gibt
weder DSN noch Schlüssel aus. Er muss mit `HEAD_EQUAL` enden; sonst bleibt
der Dienst gestoppt.

```sh
cd /opt/geniusnew
sudo -u geniusnew .venv/bin/python - <<'PY'
import psycopg
from geniusnew.anchor_process import AnchorClient
from geniusnew.config import load_config

config = load_config('/etc/geniusnew/geniusnew.toml')
with psycopg.connect(config.database_dsn) as connection:
    row = connection.execute(
        'SELECT count, head_hash FROM public.audit_heads ORDER BY count DESC LIMIT 1'
    ).fetchone()
anchor = AnchorClient(socket_path=config.anchor_socket,
                      reply_public_key=config.anchor_reply_public_key)
if row is None or tuple(row) != anchor.committed:
    raise SystemExit('HOLD: database and anchor heads differ')
print('HEAD_EQUAL', row[0], row[1])
PY
```

### Externes Backupziel

Das Ziel ist ein privater Backblaze-B2-Bucket in einem **separaten Konto** in der
[Region EU Central](https://www.backblaze.com/docs/cloud-storage-data-regions).
Diese Region wird bei der Kontoerstellung festgelegt und lässt sich danach
nicht umstellen. Der Bucket erhält
[Object Lock](https://www.backblaze.com/docs/cloud-storage-object-lock) mit
30 Tagen Standardaufbewahrung im Compliance-Modus. Ein solcher Schutz kann
während seiner Laufzeit auch vom Kontoinhaber nicht verkürzt werden. Vor dem
ersten Upload müssen Bucket, Retention und Wiederherstellungszugang mit einem
kleinen verschlüsselten Testobjekt und Rücklesen geprüft sein.

Jeder bestätigte Backupstand bekommt eigene Objektnamen für verschlüsselten
DB-Dump, verschlüsselten Ankerzustand und Manifest mit Code-SHA, UTC, `count`,
`head_hash` und Prüfsummen. Zusätzlich hält ein vom Upload-Zugang unabhängiger,
privat gesicherter Restore-Katalog die **B2-File-IDs und Prüfsummen aller drei
konkreten Versionen** fest. Ein fehlender Upload, eine fehlende File-ID oder ein
fehlgeschlagenes Rücklesen genau dieser Version per ID ergibt kein Backup-PASS.
Der Restore-Katalog darf nicht allein als neueste Version im Upload-Bucket
liegen; eine Kopie muss nach Verlust des Servers für den Restore-Operator
erreichbar sein. Sind Katalog und verifizierbare Versionen nicht verfügbar,
bleibt der Dienst in HOLD.

Der eingeschränkte Upload-Schlüssel braucht `writeFiles`, aber weder
`deleteFiles`, `writeFileLegalHolds`, `writeFileRetentions`,
`writeBucketRetentions` noch `bypassGovernance`. Backblaze erlaubt mit
[`writeFiles` auch `b2_hide_file`](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities):
Ein Hide-Marker kann den Abruf nach Namen mit 404 enden lassen, obwohl die
geschützte ältere Version erhalten bleibt. Der getrennte Restore-Zugang erhält
`listFiles`, `readFiles`, `readFileRetentions` und `readFileLegalHolds`, aber
keine Schreibrechte. Er lädt die im Katalog bestätigte Version per File-ID
und vergleicht ihre Prüfsumme. Bei fehlender ID
werden alle Versionen mit `b2_list_file_versions` untersucht, aber ohne
unabhängig bestätigte Zuordnung kein Backup-PASS erteilt. Das Verhalten ist im
[B2-Versionsmodell](https://www.backblaze.com/docs/cloud-storage-file-versions)
beschrieben. Zugangsdaten und der private Entschlüsselungsschlüssel bleiben
außerhalb des Repositories und außerhalb des Upload-Buckets.

**Anker-Untergrenze:** Zusätzlich zum 30-Tage-Backup bleibt der zuletzt
unabhängig bestätigte Ankerkopf in einem eigenen Objekt unter
[Legal Hold](https://www.backblaze.com/docs/cloud-storage-object-lock).
Ein alter Hold wird erst entfernt, nachdem ein neuerer Ankerkopf hochgeladen,
zurückgelesen und unabhängig bestätigt wurde. Kann das nicht belegt werden,
bleibt der alte Hold bestehen und ein Restore hinter diesen Stand in HOLD.
Damit löscht der Ablauf den letzten unabhängigen Ankerbeleg auch dann nicht,
wenn normale 30-Tage-Backups auslaufen.

`writeFileLegalHolds` kann einen Hold auch entfernen und liegt deshalb nur bei
einem getrennten Operatorzugang außerhalb des Upload-Servers. Der Operator
dokumentiert die File-ID des alten und des neuen Ankerobjekts vor jeder
Hold-Änderung.

Kaan gab am 05.10.2026 die kleine Offsite-Probe frei und benannte den privaten
Bucket `geniusnew`. Upload- und Restore-Zugang wurden getrennt und auf diesen
Bucket begrenzt; Kaan führte die nötigen Master- und Operatorhandlungen aus.
Zugangsdaten bleiben außerhalb des Repositories. Der private
Entschlüsselungsschlüssel und der Restore-Katalog liegen als privates
Wiederherstellungspaket bereit; dessen bestätigte Kopie außerhalb des Servers
ist weiterhin offen. Jeder produktive externe Upload braucht Kaans gesonderte
Deployment-Freigabe.

Für den Datenbank-Snapshot dient
[`pg_dump -Fc`](https://www.postgresql.org/docs/17/app-pgdump.html); für die Probe
wird mit [`pg_restore`](https://www.postgresql.org/docs/17/app-pgrestore.html)
ausschließlich in eine **neue, wegwerfbare Datenbank** restauriert.
Die Sicherungsrolle `genius_backup` erhält nur CONNECT, Schema-USAGE und SELECT
auf die acht installierten Core-Tabellen (Beispiel oben), keine Schreib-, DDL-,
Replikations- oder pauschalen Server-Leserechte. Nach jeder Migration die
Vollständigkeit gegen einen echten Dump prüfen; kein stilles Überspringen
fehlender Rechte. `default_transaction_read_only` ist nur Zusatzschutz, die
ACL ist maßgeblich. Sie ist keine Rolle für Restore oder Migration.

Der lokale Dump läuft als eigener OS-Nutzer `geniusnew-backup` über `peer`,
**nicht als root** und nicht als `genius_core`. Seine private, `0600` geschützte
`/etc/geniusnew-backup/pg_service.conf` enthält den Service `geniusnew-backup`
mit explizitem Socketpfad, `dbname=geniusnew`, `user=genius_backup` und
`connect_timeout=5`, aber kein Passwort. Bei getrennten Hosts enthält dieser
Service stattdessen die oben verlangten TLS-Parameter und einen privaten
`passfile`-Pfad. DSN und Passwort erscheinen nie als Kommandozeilenargument,
im Manifest oder im PR. Schema `c5_stats` und Extension `pg_stat_statements`
werden mit den beiden expliziten Ausschlussoptionen von `pg_dump` 17 nicht
gesichert. Die Extension und flüchtige Monitoringobjekte werden nach einem Restore separat bereitgestellt,
nicht als Teil der signierten Core-Historie.
Nach dem Stop aller Core-Schreiber und dem Gleichstandsvergleich lautet der
Kern der Sicherung beispielsweise (als Operator mit `sudo`, `private_dir` zeigt
auf einen zuvor bestimmten Pfad auf verschlüsseltem Speicher):

```bash
set -euo pipefail
sudo install -d -m 0700 -o geniusnew-backup -g geniusnew-backup "$private_dir"
sudo -u geniusnew-backup env PGSERVICEFILE=/etc/geniusnew-backup/pg_service.conf pg_dump --dbname='service=geniusnew-backup' --format=custom --exclude-schema=c5_stats --exclude-extension=pg_stat_statements --file="$private_dir/core.dump"
sudo chmod 0600 "$private_dir/core.dump"
sudo pg_restore --list "$private_dir/core.dump" >/dev/null
sudo systemctl stop geniusnew-anchor.service
sudo install -m 0600 -o root -g root /var/lib/geniusnew-anchor/anchor.state "$private_dir/anchor.state"
sudo sha256sum "$private_dir/core.dump" "$private_dir/anchor.state" | sudo tee "$private_dir/SHA256SUMS" >/dev/null
sudo chmod 0600 "$private_dir/SHA256SUMS"
```

Die Befehle sind erst nach der getrennten Freigabe
für Backupziel und DB-Sicherungsrolle auszuführen. `anchor.key`, Root-Secret und
DB-Zugangsdaten werden getrennt verschlüsselt gesichert und mit demselben
Backupstand verbunden. Ein Dump ohne diese Schlüssel kann die signierte Historie
nicht als derselbe Dienst fortsetzen. Nach dem Kopieren startet der Operator
zuerst den Anker, dann den Kern; beide müssen ihre Startprüfungen bestehen.

### WAL/PITR bleibt ein Vorschlag

`pg_dump` bleibt die Basis; ein Dump allein bietet keine Wiederherstellung auf
beliebige Zwischenzeitpunkte. WAL-Archivierung/PITR, etwa mit
[pgBackRest](https://pgbackrest.org/user-guide.html), ist **nur ein Vorschlag**.
Keine Installation, neue Abhängigkeit, Replikationsrolle, `archive_command`-
Änderung oder zusätzlicher Dienst in diesem PR. Kaan entscheidet Werkzeug,
Speicher, Kosten, RPO/RTO und Aufbewahrung nach Claude-Sicherheitsreview.

Ein späterer PITR-Entwurf braucht physisches Basisbackup, lückenlose verifizierte
WAL-Historie und getrennte, unveränderliche Sicherung des Ankerzustands. WAL
enthält sensible DB-Daten und übernimmt Verschlüsselungs-/Zugriffsgates.
Recovery-Zeitpunkt und Timeline müssen die unabhängig bestätigte Anker-
Untergrenze erreichen; fehlende WAL-Segmente ergeben HOLD, niemals einen
Anker-Rückschnitt. PITR ersetzt weder `pg_dump` noch den unabhängigen Ankerbeleg.

## Restore und Anker-Vorlauf

Der neueste unabhängig festgehaltene Ankerstand bleibt die von Kaan entschiedene
nicht absenkbare Untergrenze. Ein älteres DB-Backup berechtigt niemals zum
Zurücksetzen oder Neuerzeugen des Ankers.

1. Dienst bleibt offline; zuerst Restore in eine isolierte Prüf-DB.
2. Code, Migrationen, bytegenaue Wires, Signaturen und bidirektionale Ledger-/
   Audit-Zuordnung vollständig prüfen.
3. DB-Stand hinter dem festgehaltenen Anker: Start muss verweigern.
4. Fehlende Historie aus verifizierter Replikation oder Wiederherstellungslogs
   wiederherstellen, bis sie den bisherigen Ankerstand vollständig enthält.
5. DB vor dem Anker: ausschließlich bereits gespeicherte und verifizierte
   signierte Köpfe nachverankern. Recovery signiert keine neuen DB-Inhalte.
6. Gleichstand und vollständige Prüfung: Restore-Protokoll erzeugen; anschließend
   kontrolliert starten und einen neuen Job mit neuer ID ausführen.
7. Fehlende Historie nicht rekonstruierbar: HOLD. Kein stiller Epochenwechsel,
   kein neuer Anker und keine Freigabe alter job_id-Werte.

Die CI-Probe in `scripts/demo_restore.py` nutzt eine neue Datenbank und eine
Kopie des Ankerzustands. Sie prüft `pg_dump`/`pg_restore`, Start und Replay;
Upload, Versionsabruf und Rücklesen aus B2 müssen zusätzlich mit einem kleinen
verschlüsselten Testobjekt einschließlich Hide-Marker geprüft werden.
`pg_restore` und der Starttest dürfen niemals auf die laufende Produktions-DB
oder den aktiven Anker zeigen. Das private Protokoll hält Dump- und
Anker-Prüfsummen, die bestätigten B2-File-IDs, den vor dem Restore beobachteten
Ankerkopf, den restaurierten DB-Kopf und den Start-/Refusal-Ausgang fest. Bei
einem älteren DB-Dump wird die Kopie des Ankers **nicht** zurückgesetzt: Der
Start muss mit `ContractError` scheitern. Danach werden die wegwerfbaren
Ressourcen entfernt; die aktive Historie bleibt unverändert.

### Regelmäßige Restore-Probe

Betriebsvorschlag: wöchentlich sowie nach freigegebenen Schema-, PostgreSQL-,
Backup- oder Recovery-Änderungen. Kaan bzw. ein ausdrücklich benannter Operator
wiederholt die Fälle aus #119: gültiger Stand mit passendem Anker, Replay und
DB hinter Anker. Ergänzend verlangt die C5-Abnahme DB-Vorlauf nur mit bereits
signiertem Kopf und manipulierte Historie; diese zusätzlichen Fälle werden
durch #119 allein nicht belegt. Dazu kommt eine getrennte Rücklese-/
Entschlüsselungsprobe eines bestätigten Offsite-Backups.
Datum, vollständiger SHA, Versionen, `data_checksums`, Prüfsummen, Fallausgänge
und Wiederherstellungsdauer privat protokollieren; im PR nur bereinigtes Ergebnis.

`scripts/demo_restore.py` hat einen absichtlichen **CI-only-Guard** auf
`GITHUB_ACTIONS`, exakte Test-DSN und Wegwerf-Container-ID. Nicht auf dem Server
mit nachgebauten CI-Variablen umgehen. Der vorhandene `Verify contracts`-Lauf
führt die Probe bereits aus; seinen exakten SHA und Schritterfolg als Nachweis
verwenden. Ein Wochenlauf ohne neuen PR benötigt eine separat freigegebene
CI-Auslösung/Planung; dieser Dokumenten-PR ändert keinen Workflow und behauptet
keinen installierten Zeitplan. Eine reale Backup-Probe braucht unabhängig
davon eine explizit freigegebene, vom Produktivsystem isolierte Umgebung.
Bei ausgefallener/fehlgeschlagener Probe bleibt das Recovery-Gate offen.

Eine andere Loss-/Epoch-Recovery würde einen eigenen Sicherheitsvertrag mit
Kaans neuer ausdrücklicher Architekturentscheidung benötigen.

## Schlüsselrotation

Rotation ist eine gesonderte Betriebsentscheidung. Der aktuelle Kern leitet
Rollenschlüssel aus einem Root-Secret ab. Ein einfacher Austausch würde
gespeicherte alte Handoffs, Ergebnisse und Köpfe unverifizierbar machen.
Darum sind vor einer Rotation ein implementierter historischer Schlüsselbund,
ein überprüfbarer Übergangsvertrag und passende Start-/Recovery-Tests nötig.
Ein geänderter Root-Schlüssel wird bis dahin fail closed abgelehnt; vorhandene
historische Daten werden nicht neu signiert.

## Abnahmeprotokoll

Das private Protokoll nennt Host, OS/Kernel/Landlock, vollständigen Code-SHA,
Migrationen, UIDs, Listener, Tests/Demo/Guards, Review-SHAs und Backupmanifest.
Secretinhalte und DSN-Werte werden nicht ausgegeben.

Erforderliche Restore-Proben: gültiger Gleichstand startet; älteres DB-Backup
hinter Anker verweigert; DB-Vorlauf wird ausschließlich mit vorhandenem gültig
signierten Kopf nachverankert; manipulierte Bytes oder Kopf-Signatur verweigern.
Nach jeder Probe bleibt jede bereits reservierte ID verbrannt.

Die lokale `pg_dump`/`pg_restore`-Probe gegen den Wegwerf-PostgreSQL-Service
lief in der CI von PR #119 am Head `c177dc124d8e9e7102fc2a1ab7e56eac7481c468`
mit `contracts` und der letzten Zeile `PASS — C5 disposable PostgreSQL and anchor
restore drill.` Die am 05.10.2026 von Kaan freigegebene Offsite-Probe im privaten
Bucket `geniusnew` belegte für ein kleines verschlüsseltes Testobjekt Upload,
Hide-Marker, Wiederauffinden beider Versionen und bytegleiches Rücklesen der
bestätigten Upload-Version per File-ID mit dem getrennten Restore-Zugang. Die
Bucket- und Objektabfrage belegte Object Lock mit 30 Tagen Standardaufbewahrung
im Compliance-Modus. Kaan setzte den Legal Hold auf der Upload-Version; eine
erneute Abfrage bestätigte ihn. Der Upload-Schlüssel verweigerte den Versuch,
diesen Hold zu entfernen, und der Hold blieb aktiv. Der private Katalog enthält
File-IDs, SHA-256, Retention und Hold-Zustand ohne Zugangsdaten.

Eine zweite kleine Probe behob die Lücke der ersten: Ihr privater
Entschlüsselungsschlüssel wurde mit Modus `0600` aufbewahrt; die per File-ID
zurückgelesenen Bytes wurden gegen SHA-256 geprüft, erfolgreich entschlüsselt
und mit dem ursprünglichen Klartext verglichen. Legal Hold und die verweigerte
Entfernung durch den Upload-Schlüssel wurden auch für diese Version erneut
belegt. Der technische Offsite-Test ist damit bestanden. Für den vollständigen
Offsite-PASS fehlt nur noch die bestätigte Kopie des privaten
Wiederherstellungspakets außerhalb des Servers. Bis dahin erfolgt kein
produktiver Upload.

E3 folgt dieser Anleitung auf einem frischen Linux-Host. Ein neuer Worktree
auf dem Entwicklungsserver ersetzt diesen Betriebsnachweis nicht.

## Noch offene konkrete Freigaben

Am **aktuellen vollständigen PR-Head** erforderlich: Claude-Sicherheitsreview
für Deploy, Netz und DB sowie Copilot-Review; historische Kiro-/ChatLLM-PASS
ersetzen diese neuen Gates nicht. Kaan mergt; keine automatische Freigabe.
Offen sind insbesondere Peer-/Worker-Negativnachweis, isolierte HBA/TLS-/Rollen-/
Checksum-Prüfung, Monitoring-Exportprüfung und die regelmäßige Restore-Planung.

Nach den fertigen Code-/Review-Nachweisen: Anker/Core-Installation mit eigenen
OS-Nutzern, dauerhafte Schlüsselverwaltung und Abschluss der oben beschriebenen
Offsite-Probe durch die externe Kopie des Wiederherstellungspakets. Die
Worker-UID ist für C5 ausdrücklich verschoben;
sie bleibt eine offene Sicherheitsgrenze.
AGENTS.md verlangt für Deployment, Zugriffsrechte und Secret-Rotation Kaans OK.
