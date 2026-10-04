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
Ihr `listen_host` ist `127.0.0.1`; der Reverse-Proxy aus
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

Der konkrete Bucket-Name, der eingeschränkte Upload-Schlüssel, der getrennte
Restore-Zugang, der Ort des privaten Entschlüsselungsschlüssels und der
verantwortliche Operator fehlen noch. Die Einrichtung und jeder externe Upload
brauchen Kaans gesonderte Deployment- und Zugriffsfreigabe.

Für den Datenbank-Snapshot dient
[`pg_dump -Fc`](https://www.postgresql.org/docs/17/app-pgdump.html); für die Probe
wird mit [`pg_restore`](https://www.postgresql.org/docs/17/app-pgrestore.html)
ausschließlich in eine **neue, wegwerfbare Datenbank** restauriert.
Die Sicherungsrolle erhält eine eigene, geprüfte Leseberechtigung. Ihre libpq-
Service- und Passwortdateien liegen privat außerhalb des Repositories; DSN und
Passwort erscheinen nicht als Kommandozeilenargument, im Manifest oder im PR.
Nach dem Stop aller Core-Schreiber und dem Gleichstandsvergleich lautet der
Kern der Sicherung beispielsweise (als Operator mit `sudo`, `private_dir` zeigt
auf einen zuvor bestimmten Pfad auf verschlüsseltem Speicher):

```bash
set -euo pipefail
sudo install -d -m 0700 -o root -g root "$private_dir"
sudo env PGSERVICEFILE=/etc/geniusnew/backup.pg_service.conf PGPASSFILE=/etc/geniusnew/backup.pgpass pg_dump --dbname='service=geniusnew-backup' --format=custom --file="$private_dir/core.dump"
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
restore drill.` Die Offsite-Probe steht noch aus: Nach Kaans Bucket- und
Zugriffsfreigabe, aber **vor dem ersten echten Backup**, lädt der benannte
Restore-Operator ein kleines verschlüsseltes Testobjekt hoch, hält dessen
File-ID und Prüfsumme im unabhängigen Katalog fest, erzeugt mit dem
Upload-Schlüssel einen Hide-Marker und liest genau die bestätigte Version mit
dem getrennten Restore-Zugang per ID zurück. Er prüft auch Compliance-Retention
und setzt einen Legal Hold mit dem getrennten Operatorzugang; der
Upload-Schlüssel muss das Entfernen verweigern. Der Operator protokolliert
File-IDs, Prüfsummen, Retention, Hold und Ergebnis ohne Schlüssel oder DSN.
Fehlt ein Nachweis, erfolgt kein produktiver Upload.

E3 folgt dieser Anleitung auf einem frischen Linux-Host. Ein neuer Worktree
auf dem Entwicklungsserver ersetzt diesen Betriebsnachweis nicht.

## Noch offene konkrete Freigaben

Nach den fertigen Code-/Review-Nachweisen: Anker/Core-Installation mit eigenen
OS-Nutzern, B2-Bucket und Schlüsselverwaltung nach separater Freigabe sowie
die oben beschriebene Offsite-Probe durch den benannten Restore-Operator. Die
Worker-UID ist für C5 ausdrücklich verschoben;
sie bleibt eine offene Sicherheitsgrenze.
AGENTS.md verlangt für Deployment, Zugriffsrechte und Secret-Rotation Kaans OK.
