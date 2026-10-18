# GeniusNew — Datenbankdesign v0.2

**Status:** ChatGPT-Entwurf im Auftrag von Kaan für v0.2. Vor Implementierung braucht
dieser Entwurf einen unabhängigen Security-Review durch Claude und Kaans Freigabe.
Die Technikentscheidungen sind von Kaan am 26.09.2026 getroffen
(`docs/DECISIONS.md`, Issue #44): Portal auf Vercel, Kern auf einem eigenen Server,
PostgreSQL mit synchronem `psycopg`, Portal-Passwörter mit `argon2-cffi`.

Dieses Dokument beschreibt Speichergrenzen und Datenmodelle. Sicherheitsvertrag,
Runtime-Code und Tests bleiben maßgeblich. Bei einem Widerspruch zu Code oder
`SECURITY.md` wird **nicht** still angepasst, sondern fail closed gestoppt und der
Widerspruch als eigenes Thema behoben.

## 1. Ziel

v0.2 ersetzt die prozesslokalen Speicher des Kernsystems durch dauerhafte,
konkurrenzsichere Zustände. Ein Neustart oder eine zweite Instanz darf insbesondere:

- dieselbe `job_id` nicht erneut ausführen;
- denselben Handoff nicht erneut als Ergebnis annehmen;
- einen verbrauchten Approval-Token nicht wieder gültig machen;
- einen wartenden Approval-Job nicht still verlieren;
- eine gekürzte oder veränderte Audit-Kette nicht akzeptieren.

Der Browser ist nicht vertrauenswürdig. Portal-Daten erzeugen keine Rechte im Kern.
Identität, Rechte, Tier, Worker und Job-Kennung bleiben serverseitig bestimmt.

## 2. Topologie und Vertrauensgrenzen

### 2.1 Kern

Der GeniusNew-Kern läuft auf dem eigenen Server und verwendet PostgreSQL über
`psycopg` (synchron). Die Verbindung kommt ausschließlich aus serverseitiger
Laufzeitkonfiguration.

Die **Core-Datenbank** enthält Job-/Annahme-Ledger, wartende Jobs, Approval-Zustand,
Audit-Kette sowie API-Key-Digests und Principal-Zuordnung.

### 2.2 Portal

Das Portal läuft auf Vercel und besitzt eine **separate PostgreSQL-Datenbank** mit
eigenen Zugangsdaten. Core- und Portal-Daten liegen nicht nur in getrennten Schemas,
sondern in getrennten Datenbanken und Runtime-Rollen. Das Portal speichert Nutzer, Sessions,
Rollen und nicht-authoritative Anzeige-/Verlaufsdaten.

Der Browser greift weder auf die Core-Datenbank noch auf deren Zugangsdaten zu. Der
Portal-Backend-Code spricht mit dem Kern ausschließlich über eine authentisierte
Server-zu-Server-Schnittstelle. Ein vom Browser geliefertes `subject`, `tier`,
`user_id`, `tools` oder `job_id` darf niemals Core-Autorität werden.

### 2.3 Audit-Anker

Der Ankerzustand liegt **nicht** in der Core-Datenbank und verwendet **nicht** deren
Zugangsdaten. Die Core-Datenbank darf diesen Store nicht schreiben.

**Aktuelle Grenze:** Der vorhandene `AnchorProcess` läuft noch unter demselben
Betriebssystem-Nutzer wie der Dienst; `SECURITY.md` hält diese Grenze ausdrücklich
offen. Dieses Datenbankdesign schließt sie nicht. Der Betrieb unter einem eigenen
OS-Nutzer ist ein separates Security-/Deployment-Thema mit eigenem Test und
`SECURITY.md`-Änderung.

### 2.4 Technikvergleich

| Kriterium | SQLite | PostgreSQL |
| --- | --- | --- |
| Betrieb | einzelne lokale Datei, sehr einfach | eigener DB-Dienst |
| Gleichzeitige Core-Instanzen | Schreibkonkurrenz/Dateisperren werden schnell zum Engpass | Transaktionen, Row Locks und Unique Constraints für mehrere Instanzen |
| Vercel-Portal | keine dauerhaft lokale, gemeinsam erreichbare DB | netzwerkfähig und für Vercel geeignet |
| Exactly-once-Reservation | möglich auf einem Host, schwächer für mehrere Prozesse/Hosts | atomare Inserts und `SELECT ... FOR UPDATE` |
| Betriebsaufwand | geringer | höher, Backups/Monitoring nötig |
| Ergebnis | sinnvoll für lokale Einzelinstanz/Tests | **gewählt** für v0.2-Produktion |

PostgreSQL ist bereits von Kaan entschieden. Der Vergleich dokumentiert die verworfene
Alternative, ohne die Technikentscheidung neu zu öffnen.

## 3. Kanonische Speicherregeln

1. **Signierte oder gehashte Daten werden byte-genau gespeichert.** Handoff-Wires,
   Ergebnis-Wires, Audit-Events und Approval-Record-Payloads sind `bytea`, nicht `jsonb`.
2. `jsonb` ist nur für nicht signierte Portal-Metadaten zulässig.
3. Zeitwerte sind `bigint` in Unix-Sekunden, passend zu `now: int`.
4. `job_id` ist `text`, nicht UUID.
5. SHA-256-Digests sind lowercase `char(64)` und werden beim Laden neu geprüft.
6. Raw API-Keys, Session-Tokens, Approval-Tokens und Passwörter werden nie gespeichert.
7. Alle Sicherheitsmutationen laufen in expliziten Transaktionen.
8. DB nicht erreichbar, Schema unbekannt oder Integritätsprüfung fehlgeschlagen:
   Dienst nimmt keine Aufträge an.

## 4. Core-Schema

### 4.1 `schema_migrations`

```sql
create table schema_migrations (
    version bigint primary key,
    checksum char(64) not null,
    applied_at bigint not null
);
```

Eine bereits angewendete Version mit anderem Checksum ist Startfehler.

### 4.2 `job_ledger`

Persistiert die heute prozesslokale Reservation des Orchestrators.

```sql
create table job_ledger (
    job_id text primary key,
    subject text not null,
    handoff_sha256 char(64) not null,
    state text not null check (
        state in ('PENDING_APPROVAL', 'RESERVED', 'EXECUTION_COMMITTED', 'COMPLETED', 'REFUSED')
    ),
    created_at bigint not null,
    reserved_at bigint,
    updated_at bigint not null,
    expires_at bigint not null,
    unique (job_id, handoff_sha256),
    check (
        (state = 'PENDING_APPROVAL' and reserved_at is null)
        or (state in ('RESERVED', 'EXECUTION_COMMITTED', 'COMPLETED')
            and reserved_at is not null)
        or state = 'REFUSED'
    )
);
```

**Atomare Reservation:** Der Primary Key ist die globale Replay-Sperre. Ein Konflikt
bedeutet `JOB_ID_REUSED`. Bei approval-pflichtigen Jobs werden die
`job_ledger`-Zeile im Zustand `PENDING_APPROVAL` und die zugehörige
`pending_jobs`-Zeile **in derselben Transaktion** angelegt. Bei Jobs ohne Approval wird
die Reservation vor der Ausführungsgrenze angelegt.

**Crash-Regel:** Jede vorhandene `job_id` bleibt verbrannt. Es gibt keinen automatischen
Retry mit derselben ID. Ein abgelaufener Pending-Job wird auf `REFUSED` gesetzt; seine
ID wird nicht wieder freigegeben.

**DB-erzwungene Zustandsmaschine:** Die Runtime-Rolle hat auf `job_ledger` kein
`DELETE`. Ein `BEFORE UPDATE`-Trigger erlaubt ausschließlich
`PENDING_APPROVAL → RESERVED | REFUSED`, `RESERVED → EXECUTION_COMMITTED | REFUSED`
und `EXECUTION_COMMITTED → COMPLETED`. `COMPLETED` und `REFUSED` sind terminal.
`job_id`, `subject`, `handoff_sha256`, `created_at` und `expires_at` sind nach
dem Insert unveränderlich; `reserved_at` darf nur beim Übergang nach `RESERVED`
erstmals gesetzt werden und danach nicht mehr geändert werden.

Vor Übergabe an den Worker wird der Zustand auf `EXECUTION_COMMITTED` gesetzt. Ein Crash
danach bedeutet: Wirkung möglicherweise eingetreten, Ergebnis unbekannt. Dieser Zustand
wird **nie automatisch erneut ausgeführt**.

Abnahmetests aus Konflikt 4 / Issue #44:

- gleicher `job_id` nach Neustart → keine zweite Ausführung;
- zwei Instanzen reservieren gleichzeitig → genau eine gewinnt;
- Crash an der Effect-Grenze → Reservation bleibt und wird nicht erneut ausgeführt.

### 4.3 `acceptance_ledger`

```sql
create table acceptance_ledger (
    handoff_sha256 char(64) primary key,
    job_id text not null,
    handoff_wire bytea not null,
    result_sha256 char(64) not null unique,
    result_wire bytea not null,
    accepted_at bigint not null,
    foreign key (job_id, handoff_sha256)
        references job_ledger(job_id, handoff_sha256)
);
```

Eine vorhandene `handoff_sha256` bedeutet `RESULT_ALREADY_ACCEPTED`. Beim Laden
werden `handoff_wire` und `result_wire` gemeinsam neu validiert: Handoff-Signatur,
Job-/Worker-Bindung, Handoff-Digest, Ergebnis-Signatur und `result_sha256` müssen zu
den gespeicherten Bytes passen. Zusätzlich erzwingt der zusammengesetzte Foreign Key,
dass die angenommene `handoff_sha256` exakt diejenige des `job_ledger`-Eintrags
derselben `job_id` ist.

**Zustandsinvariante:** Ein Acceptance-Datensatz darf nur aus einem
`job_ledger`-Eintrag im Zustand `EXECUTION_COMMITTED` entstehen. Ein
`BEFORE INSERT`-Trigger sperrt die zugehörige Ledger-Zeile und verweigert
`PENDING_APPROVAL`, `RESERVED`, `REFUSED` und bereits `COMPLETED`. Ein
`AFTER INSERT`-Trigger setzt genau diese gesperrte Ledger-Zeile von
`EXECUTION_COMMITTED` auf `COMPLETED`; aktualisiert er nicht exakt eine Zeile,
schlägt die Transaktion fehl. Die Anwendung führt keinen separaten
`COMPLETED`-Update außerhalb dieser Transaktion aus. Im dauerhaft gespeicherten
Zustand gilt daher: **jede** `acceptance_ledger`-Zeile gehört genau zu einem
`job_ledger(COMPLETED)` mit identischer `job_id` und identischem
`handoff_sha256`. Die Startprüfung validiert diese Invariante erneut und startet bei
jeder Abweichung fail closed.

### 4.4 `pending_jobs`

Schema aus Issue #44:

```sql
create table pending_jobs (
    job_id text primary key references job_ledger(job_id),
    subject text not null,
    wire bytea not null,
    trace_id text not null,
    expires_at bigint not null
);
```

Es gibt **kein** `handoff_data`. Der Handoff wird beim Laden ausschließlich aus `wire`
neu validiert.

`job_ledger(PENDING_APPROVAL)` und `pending_jobs` entstehen in **einer Transaktion**.
Beim Start ist eine Pending-Zeile ohne passende Ledger-Zeile oder eine
`PENDING_APPROVAL`-Ledger-Zeile ohne Pending-Zeile ein Integritätsfehler; der Dienst
startet nicht. Umgekehrt darf es für `RESERVED`, `EXECUTION_COMMITTED`, `COMPLETED`
oder `REFUSED` **keine** `pending_jobs`-Zeile geben.

Ein falscher oder fremder Approval-Token verändert weder Ledger noch Pending-Zeile. Bei
korrektem Approval werden **in derselben Transaktion** ein neuer
`approval_records(CONSUMED)`-Datensatz angelegt, der `approval_tokens`-Pointer auf
diesen direkten Nachfolger verschoben, die `pending_jobs`-Zeile gelöscht, die
Ledger-Zeile von `PENDING_APPROVAL` nach `RESERVED` überführt und `reserved_at`
erstmals gesetzt. Bei Ablauf oder endgültigem Refusal wird ebenfalls in **derselben
Transaktion** die Pending-Zeile gelöscht und der Ledger-Zustand auf `REFUSED` gesetzt.
Sobald B6 umgesetzt ist, gehört auch das zugehörige Audit-Event in genau diese
Transaktion. Dadurch kann kein erfolgreicher oder abgelaufener Approval-Pfad eine stale
Pending-Zeile hinterlassen. Erst nach dem erfolgreichen Commit des Approval-Übergangs
kann die Ausführungsgrenze erreicht werden. `created_at` existiert in jedem Zustand und
darf nicht als Ersatz für den tatsächlichen Reservationszeitpunkt verwendet werden.

### 4.5 Approval-Speicher

Raw Tokens werden nie gespeichert. Die Token-Identität ist `sha256(token)`. Übergänge
werden append-only gespeichert, damit Audit-Events Grant und Verbrauch referenzieren
können.

```sql
create table approval_records (
    token_digest char(64) not null,
    record_hash char(64) not null unique,
    scope bytea not null,
    issued_at bigint not null,
    expires_at bigint not null,
    state text not null check (state in ('GRANTED', 'CONSUMED', 'REVOKED')),
    changed_at bigint not null,
    previous_hash char(64),
    primary key (token_digest, record_hash),
    foreign key (token_digest, previous_hash)
        references approval_records(token_digest, record_hash)
);

create unique index approval_one_root_per_token
    on approval_records(token_digest) where previous_hash is null;

create unique index approval_one_successor_per_record
    on approval_records(token_digest, previous_hash)
    where previous_hash is not null;

create table approval_tokens (
    token_digest char(64) primary key,
    current_record_hash char(64) not null,
    foreign key (token_digest, current_record_hash)
        references approval_records(token_digest, record_hash)
);
```

`scope` sind die kanonischen Bytes der Approval-Scope-Fakten. `record_hash` wird beim
Laden neu berechnet. Die zusammengesetzten Foreign Keys erzwingen, dass
`previous_hash` und `current_record_hash` zum **gleichen `token_digest`** gehören.
Für den ersten `GRANTED`-Record ist `previous_hash = NULL`; spätere Records müssen
einen Vorgänger desselben Tokens nennen. Die beiden Unique-Indizes erlauben genau einen
Root und höchstens einen Nachfolger je Record: die Historie kann nicht verzweigen.

Die Runtime-Rolle erhält auf `approval_records` **INSERT und SELECT, aber kein UPDATE
und kein DELETE**. Ein `BEFORE INSERT`-Trigger auf `approval_records` erzwingt die
Zustandsmaschine: `GRANTED` ist nur als Root mit `previous_hash IS NULL` zulässig;
ein Nachfolger ist ausschließlich `CONSUMED` oder `REVOKED`, und sein Vorgänger
muss `GRANTED` sein. `scope`, `issued_at` und `expires_at` müssen
byte-/wertgleich zum Vorgänger bleiben; `changed_at` darf nicht vor dem Vorgänger
liegen. Damit kann nach `CONSUMED` oder `REVOKED` nie wieder ein gültiger
`GRANTED`-Zustand entstehen.

Ein DB-Trigger auf `approval_tokens` erzwingt beim ersten INSERT, dass der Pointer auf
den einzigen `GRANTED`-Root mit `previous_hash IS NULL` zeigt. Bei jedem UPDATE muss
der neue `current_record_hash` auf **genau den bereits validierten direkten
Nachfolger** zeigen, dessen `previous_hash` dem alten Pointer entspricht. Consume und
Revoke sperren die Token-Zeile mit `SELECT ... FOR UPDATE` und sind nur aus
`GRANTED` zulässig.

Beim Start wird für jeden Token die vollständige unverzweigte Record-Kette vom Root bis
zum Pointer geprüft, jeder `record_hash` neu berechnet und verlangt, dass der Pointer
auf dem einzigen Tip liegt. Ein verwaister Record, ein zweiter Root/Nachfolger, ein
Rücksprung oder eine ungültige Zustandsfolge ist ein Startfehler.

### 4.6 `audit_chain`

```sql
create table audit_chain (
    index bigint primary key check (index >= 0),
    previous_hash char(64) not null,
    record_hash char(64) not null unique,
    event bytea not null
);
```

Index beginnt bei 0 und ist lückenlos. Beim ersten Record ist `previous_hash` der
All-zero-Digest aus 64 `0`-Zeichen — niemals `NULL`. `event` sind die exakten
kanonischen Event-Bytes.

Beim Start wird die **gesamte Kette** geladen. Für jeden Record wird das Event
rehydriert und anschließend verlangt, dass die gespeicherten `event`-Bytes **bytegleich**
mit `rehydrated_event.to_bytes()` sind. Damit werden auch Änderungen nur an
Whitespace, Schlüsselreihenfolge oder anderer nicht-kanonischer JSON-Darstellung
abgelehnt. Erst danach werden Event-Hash, `previous_hash`, `record_hash`, lückenlose
Indizes und der signierte Kopf geprüft und die vollständige Kette gegen den externen
Anker verifiziert.

### 4.7 Signierte Audit-Köpfe

Zu jedem dauerhaft angehängten Audit-Record wird der dazugehörige **bereits signierte**
Kopf in derselben Datenbanktransaktion gespeichert:

```sql
create table audit_heads (
    count bigint primary key check (count > 0),
    version text not null,
    head_hash char(64) not null unique,
    signature bytea not null check (octet_length(signature) = 64),
    created_at bigint not null
);
```

Vor dem Commit erzeugt die `AuditAuthority` den Kopf für den neuen vollständigen
Kettenstand. `audit_chain`-Append und `audit_heads`-Insert committen atomar. Beim
Laden wird aus `version`, `count`, `head_hash` und `signature` ein
`AuditHead` rekonstruiert und mit dem öffentlichen Audit-Schlüssel geprüft. Ein per
SQL angehängter Record ohne passenden gültig signierten Kopf ist ein Startfehler.

Ist die DB dem externen Anker voraus, darf beim Neustart **nur ein bereits in
`audit_heads` gespeicherter und verifizierter Kopf** erneut an den Anker geschickt
werden; der Dienst signiert beim Recovery niemals einen neuen Kopf über unverankerte
DB-Inhalte.

### 4.8 Principal-Registry

```sql
create table api_key_digests (
    digest char(64) primary key,
    subject text not null,
    created_at bigint not null,
    revoked_at bigint
);
```

`digest` ist SHA-256 des hochentropischen API-Keys wie in `geniusnew/http_entry.py`.

### 4.9 D1-Replay-Reservation (Entwurfszusatz, noch nicht freigegeben)

Grundlage sind die D1-Entscheidungen vom 18.10.2026 in
[`DECISIONS.md`](DECISIONS.md), dokumentiert in
[PR #158](https://github.com/Kaancodm/GeniusNew/pull/158). Dieser Zusatz setzt sie
für den Speicher um; die Vertragsüberarbeitung in
[`PORTAL-CORE-D1-DRAFT.md`](PORTAL-CORE-D1-DRAFT.md) gehört zu
[Issue #159](https://github.com/Kaancodm/GeniusNew/issues/159). D1 bleibt HOLD bis
zum Abgleich beider Entwürfe, Claudes unabhängigem Security-Review und Kaans
Freigabe. Die Zahlen unten sind ein **Vorschlag**, keine Betriebsmessung oder
bereits erteilte Freigabe. Alle D1-Tabellen gehören ausschließlich zur
Core-Datenbank; Portal, Browser und Portal-Runtime haben keinen direkten Zugriff.

#### 4.9.1 Replay-Zeilen und Rechte

```sql
create table d1_replay_reservations (
    domain text collate "C" not null
        check (domain = 'geniusnew.portal-core.request.d1'),
    issuer text collate "C" not null
        check (length(issuer) between 1 and 64
               and issuer !~ '[^A-Za-z0-9._-]'),
    nonce bytea not null check (octet_length(nonce) = 32),
    expires_at bigint not null
        check (expires_at > 0 and expires_at < 4102444800),
    primary key (domain, issuer, nonce)
);
```

`nonce` ist die exakte 32-Byte-Dekodierung der 64 lowercase Hex-Zeichen aus dem
signierten D1-Envelope. `domain` bleibt über Schlüsselrotation und Deployments
stabil; eine neue `key_id` ändert den Unique Key nicht. `expires_at` ist ein
Nachweisfeld und löst allein keine Löschung aus. Nur der getrennte, auditierte
Bereinigungspfad aus 4.9.5 darf Zeilen entfernen, deren Ablauf **länger als
65 Sekunden** zurückliegt. Der alte signierte Request bleibt abgelaufen; seine
Job-ID und seine Audit-Historie bleiben erhalten. Uhr-Rücksprung,
Schlüsselrotation und Neustart geben keine Reservation frei. Ein voller oder
nicht erreichbarer Speicher verweigert neue D1-Aufträge.

Die Core-Runtime darf auf dieser Tabelle nur `SELECT` und `INSERT`; ausdrücklich
kein `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER` oder DDL. Sie ist
nicht Eigentümerin der Tabelle, nicht Superuser, hat kein `BYPASSRLS`,
`CREATEROLE` oder `CREATEDB` und ist weder direkt noch über Mitgliedschaften,
geerbte Rollen, `SET ROLE`, Schemaprivilegien oder ausführbare
`SECURITY DEFINER`-Funktionen zu diesen verbotenen Operationen fähig.
Das schließt effektive Rechte über `PUBLIC`, `pg_write_all_data` und, soweit
verfügbar, `pg_maintain` sowie das Tabellenrecht `MAINTAIN` ein.
Die Portal-Runtime hat **keine** effektiven Rechte auf die Tabelle, auch nicht
über `PUBLIC`, Mitgliedschaften oder `SECURITY DEFINER`-Funktionen. Beide
Runtime-Rollen und ihre indirekten Rechte sind beim D1-Start zu prüfen.
Default-Privileges und Grants sind beim Start zu prüfen. Nur die getrennte
Migrationsrolle darf Schemaänderungen vornehmen. Die Core-Runtime bekommt auch
über den Bereinigungspfad kein effektives Löschrecht.

Alle D1-Zugriffe benennen Core-Schema und Tabelle vollständig. Der D1-DB-Zugang
hat einen festgelegten sicheren `search_path` und kein `TEMP`-Recht, sodass
Namensauflösung nicht auf eine andere Tabelle oder Funktion ausweichen kann.

Alle Core-Instanzen, die dasselbe `(issuer, audience)` akzeptieren, müssen
dieselbe autoritative, transaktionale Replay-Tabelle auf demselben schreibbaren
Primary benutzen. Getrennte schreibbare Kopien und Split-Brain sperren D1.
Restore oder Failover mit möglicherweise verlorenen Reservationen verlangen den
angehaltenen, geprüften Wiederanlauf aus 4.9.4; ein unbekannter Replikations-
oder Failover-Stand ist REFUSE.
`audience` ist absichtlich nicht Teil des Unique Keys: Ein Nonce desselben
`issuer` bleibt auch bei Wechsel des Core-Ziels derselben Replay-Sperre unterstellt.

#### 4.9.2 Eine Annahme-Transaktion (Gate 9)

Nach erfolgreicher Signaturprüfung und den Last-/Policy-Gates erzeugt der Kern
die zufällige Job-ID **vor COMMIT**. Nonce-Reservation, `job_ledger`-Eintrag,
zugehöriges Audit-Event und bereits signierter Kopf in `audit_heads` entstehen
in **derselben PostgreSQL-Transaktion auf derselben Verbindung**. Es gibt keinen
vorherigen separaten Nonce-COMMIT und kein nachträgliches Anlegen des Jobs.

Die Reservation verwendet weiterhin genau einen Insert gegen den Primary Key:
`INSERT ... ON CONFLICT (domain, issuer, nonce) DO NOTHING RETURNING domain,
issuer, nonce`. Nur genau eine zurückgegebene Zeile, deren drei Werte exakt mit
den Insert-Werten übereinstimmen, ist ein Gewinnernachweis. Null Zeilen,
abweichende Rückgabe oder jeder SQL-/Serialisierungsfehler sind REFUSE ohne
automatischen Retry; die gesamte Annahme-Transaktion wird zurückgerollt.
Ledger und Annahme-Event dürfen ohne Gewinnernachweis nicht committen.
Nach jeder DB-/Lock-Wartezeit und unmittelbar vor COMMIT werden die Core-Uhr
und die Request-Gültigkeit erneut geprüft; Ablauf bedeutet ROLLBACK aller
Annahme-Mutationen.

Das Annahme-Event bindet die Core-Job-ID, die vorhandene Principal-/Handoff-
Bindung, die Envelope-Version und `sha256(envelope_bytes)` der exakt geprüften
kanonischen Bytes. Zusätzlich muss es die Replay-Zeile eindeutig referenzieren:
als versionierten, domänenseparierten SHA-256 über die kanonischen Werte
`domain`, `issuer`, `nonce` als lowercase Hex und `expires_at`; `expires_at`
bleibt auch als eigenes Nachweisfeld lesbar. Raw-Envelope, Signatur,
Auftragstext und Zugangsdaten gehören nicht ins Audit. Die bestehenden
`AuditEvent`-Versionen 1/2 haben diese D1-Felder noch nicht; Event-Version,
Digest-Profil und Abgleich sind im überarbeiteten D1-Vertrag ausdrücklich
festzulegen, nicht still als Zusatzfelder einzuführen.

Die Annahme-Transaktion setzt `SET LOCAL synchronous_commit TO on` nach `BEGIN`
und prüft den wirksamen Wert unmittelbar vor `COMMIT`; dazwischen darf kein
Befehl oder Savepoint-Rollback ihn ändern. `fsync=on` und
`full_page_writes=on` sind beim Start und vor jeder Reservation als wirksame
Serverwerte zu prüfen. Kann eine Prüfung oder die unveränderte Betriebs-
Konfiguration bis zum COMMIT nicht sichergestellt werden, sperrt D1.
Erst ein bestätigter synchroner COMMIT auf dauerhaftem WAL erlaubt die
Fortsetzung bis zur bestehenden Ankerprüfung aus §6. Erst deren Bestätigung
erlaubt Erfolg und Ausführung. Ein unbekannter Commit-Ausgang ist UNKNOWN/REFUSE,
ohne automatischen Retry. Hat COMMIT stattgefunden, bleiben Nonce, Job und Audit
gemeinsam erhalten; Recovery erzeugt keinen Ersatzjob und führt keinen Job
automatisch erneut aus. Der Worker läuft außerhalb dieser Transaktion; alle
bestehenden Ausführungs-/TTL-Gates bleiben wirksam, auch nach Anker-Wartezeit.

Der **Annahmepfad ist entschieden**. Für signaturgeprüfte Ablehnungen **ohne
Reservation** gilt im vorgeschlagenen Speichermodell: **höchstens ein
D1-Ablehnungs-Event je `(issuer, nonce)`**, auch bei parallelen Requests,
Neustart oder Schlüsselrotation. Ein Event setzt das für diese Anfrage bereits
erfolgreich belastete Dienst-Token voraus, keine zweite Buchung. Nach ROLLBACK einer
gegebenenfalls begonnenen
Annahme-Transaktion entstehen in einer eigenen dauerhaften Audit-Transaktion
nur dieses versionierte Event und sein bereits signierter Kopf. Das Event
bindet Envelope-Version/-Digest, geprüfte Dienstidentität als Digest, Prüfzeit
und einen geschlossenen Ablehnungsgrund, etwa Nonce-Konflikt oder Ablauf nach
Signaturprüfung. Es entstehen weder Replay-Zeile noch Job; Job-/Handoff-Felder
werden nicht durch erfundene IDs ausgefüllt.

Für die Deduplizierung bindet das Event zusätzlich einen versionierten,
domänenseparierten Digest der kanonischen Werte `issuer` und `nonce`.
`expires_at`, `key_id`, `audience`, Envelope-Digest und Ablehnungsgrund gehören
**nicht** in diesen Schlüssel: geänderte Werte erlauben kein zweites Event.
Der Replay-Zeilendigest mit `expires_at` ist dafür kein Ersatz. Prüfung auf
einen vorhandenen Ablehnungsnachweis und Append erfolgen unter derselben
datenbankgestützten Audit-Serialisierung über alle D1-Annehmer. Die verifizierte
Audit-Kette ist die autoritative Deduplizierungsquelle; ein abgeleiteter Cache
darf keinen ungeprüften oder veralteten Stand verwenden. Beim Start wird der
Nachweis aus der geprüften Kette rekonstruiert; mehrere Ablehnungs-Events zu
derselben Identität sind ein Startfehler. Replay-Bereinigung entfernt diesen
Nachweis nicht. Recovery erkennt den Ereignistyp ausdrücklich als Ablehnung
ohne Job-/Replay-Gegenstück und prüft weiterhin Signatur, Kette und Anker.
Die neue Event-Version muss diese Form ausdrücklich zulassen.

Weitere Ablehnungen derselben Identität belasten, soweit verfügbar, je ein
Dienst-Token und dürfen ausschließlich begrenzte, aggregierte Zähler erhöhen.
Sie erzeugen kein weiteres Audit-Event und keinen requestbezogenen Logeintrag.
**Bei leerem Dienst-Bucket wird sofort ohne Audit oder requestbezogenes Logging
abgelehnt**, auch wenn noch kein Event zu dieser Identität existiert. Zähler
haben feste Kategorien und lösen keine eigene dauerhafte Buchung je Versuch
aus; eine zusätzliche unbegrenzte Tabelle oder Queue pro Nonce ist unzulässig.
Leer bedeutet hier: Die anfängliche Dienst-Tokenbuchung für diese Anfrage
scheitert. Ein bereits budgetierter neuer Ablehnungsnachweis bleibt dagegen
erforderlich, auch wenn die erfolgreiche Buchung das letzte Token verbraucht hat.

Die erforderliche Ablehnungs-Transaktion beginnt erst nach Freigabe der äußeren
Sperren. COMMIT und Ankerbestätigung bleiben für einen neuen Nachweis Pflicht;
ein fehlgeschlagenes oder unklares erforderliches Ablehnungs-Audit sperrt
weitere D1-Annahmen, ohne Erfolg oder automatischen Retry zu behaupten.
Ein bereits belastetes Dienst-Token bleibt verbraucht; ein leerer Bucket
erzeugt kein Ersatz-Token. Dieses Modell und seine Crash-/Abgleichsfälle sind
mit #159 festzulegen und unabhängig zu prüfen; **Gate 9 insgesamt bleibt bis
dahin offen**.

#### 4.9.3 Gespeicherte Höchstzeit (Gate 2)

Vorgeschlagen wird eine vorinitialisierte Singleton-Tabelle auf demselben
autoritativen Core-Primary:

```sql
create table d1_clock_state (
    id smallint primary key check (id = 1),
    max_seen_at bigint not null
        check (max_seen_at >= 0 and max_seen_at < 4102444800)
);
```

`max_seen_at` ist die höchste erfolgreich gespeicherte, geprüfte Core-UTC-Zeit
in ganzen Unix-Sekunden. Nur der freigegebene Installationsablauf legt genau
eine Zeile mit geprüfter Startzeit an. Die Core-Runtime erhält `SELECT` und
spaltengebundenes `UPDATE (max_seen_at)`, kein `INSERT`, `DELETE`, `TRUNCATE`
oder DDL. Ein von der Migrationsrolle verwalteter Guard verbietet das Absenken
der Zeit und eine Änderung von `id`; Runtime und Bereinigungsaufrufer können
ihn weder ändern noch umgehen. Fehlende Zeile, falsche Form, ungültiger Guard
oder unklare effektive Rechte sperren D1.

Die erste Zeitprüfung eines Requests vor der Signaturprüfung liest die
Höchstzeit ausschließlich mit `SELECT`, ohne `FOR UPDATE` und ohne
Fortschreibung. Für die späteren, zum Schreiben berechtigten Prüfungen
serialisieren alle Core-Instanzen Vergleich und Fortschreiben über dieselbe
Zeile (`SELECT ... FOR UPDATE`); nach Lock-Wartezeit wird die Core-Uhr frisch
gelesen und erneut geprüft. `now < max_seen_at` verweigert D1. Eine höhere
gespeicherte Marke wird **nicht** mit `max(now, max_seen_at)` als Ersatzuhr
verwendet. Die Prüfung im
Prozess vergleicht zusätzlich UTC-Fortschritt und monotone Laufzeit; NTP muss
nachweislich synchronisiert sein. Rücksprung, eingefrorene Uhr oder unbekannter
Zeitstatus sperren Annahme und Bereinigung. Zulässige Messauflösung und
Drifttoleranz sind Teil des noch zu prüfenden Betriebsprofils.

Eine **anfragegetriebene Fortschreibung von `max_seen_at` erfolgt erst nach
erfolgreicher Signaturprüfung und Belastung des Dienst-Buckets**. Unsignierte
oder ungültig signierte Requests und Requests bei leerem Dienst-Bucket
verursachen keine Fortschreibung. Gespeichert wird ausschließlich die frisch
geprüfte Core-UTC-Zeit, niemals ein Zeitwert aus dem Envelope. Danach bleibt
eine höhere Probe auch dann dauerhaft zu sichern, wenn der Request später
abgelehnt wird. Kurze Uhr-Transaktionen sind von der Annahme-Transaktion zu
unterscheiden: sie legen weder Nonce noch Job an. Die letzte Prüfung/
Fortschreibung vor Annahme-COMMIT läuft innerhalb der gemeinsamen
Annahme-Transaktion; eine bei deren ROLLBACK verlorene höhere Probe muss vor
weiterer Annahme separat dauerhaft gesichert werden. Die intern ausgelöste
Startprüfung und der berechtigte Bereinigungspfad verwenden denselben
Uhrmechanismus ohne externe Request-Signatur; sie dürfen nicht durch beliebige
Requests ausgelöst werden. Persistenzfehler oder unbekannter Commit-Ausgang
sperren D1, kein In-Memory-Ersatz.

Separate dauerhafte Uhr-/Budgetbuchungen enden vor der Annahme- beziehungsweise
Bereinigungstransaktion. Wie im bestehenden `anchor_lock()` wird zuerst die
Audit-/Ankersperre erworben, danach beginnt die Transaktion; darin folgen
Uhrzeile und Replay-/Ledger-Zustand. Weitere Zeitproben verwenden dieselbe
Verbindung, keine zweite Verbindung mit konkurrierender Uhr-Fortschreibung.
Ein separater Uhr-Nachtrag nach ROLLBACK beginnt erst nach Freigabe der äußeren
Sperren. Diese Reihenfolge gilt auch für Ablehnungs-Audit und Bereinigung.

**Betriebsgrenze:** Eine mit der Datenbank gemeinsam zurückgespielte Höchstzeit
beweist keinen unabhängigen Uhr-Rollback-Schutz. NTP, Prozessprüfung und
DB-Höchstzeit sind die am 18.10.2026 gewählte Grenze; ihr Eintrag in
`SECURITY.md` folgt in einem eigenen PR. Weder die Uhr-Tabelle noch dieser
Entwurf ersetzt den getrennten Audit-Anker.

#### 4.9.4 Start, Backup und Restore (Gates 3 und 7)

**Architektur-Gate 7: entschieden am 18.10.2026.** Die D1-spezifische Prüfung
von Primary Key, Zeilenform und Rechten ist zulässig. Der Beschluss vom
30.09.2026 gegen einen allgemeinen Schema-Fingerprint bleibt bestehen.

Vor D1-Freigabe werden Migration, nicht deferrable Replay-Primary-Key,
Zeilenform, tatsächliche Insert-Zieltabelle, effektive Rechte einschließlich
Bereinigungspfad und Uhr-Guard sowie die Abwesenheit von Insert-umschreibenden
Replay-Triggern, Rules, RLS-Policies und nicht freigegebenen Partitionen/
Kindtabellen geprüft. Unklare Katalog-, Schema- oder Zeitwerte sperren D1.
Eine fehlende, leere Ersatz- oder beschädigte Tabelle darf nicht automatisch
initialisiert werden.

Nach **jedem** Start nimmt D1 mindestens **65 Sekunden** nichts an: 60 Sekunden
maximale Envelope-Gültigkeit plus 5 Sekunden erlaubter Vorlauf. Die Quarantäne
beginnt erst, wenn **alle Startprüfungen erfolgreich abgeschlossen** sind:
Schema-/Rechte- und Speicherprüfung, vollständiger B5/B6-Abgleich von Job-Ledger
und Audit, Prüfung der signierten Köpfe gegen den getrennten Anker sowie
Uhrprüfung nach 4.9.3. Erst danach werden die monotone Startmarke und die
geprüfte Core-UTC-Startzeit der Quarantäne gesetzt; die Dauer der Startprüfungen
zählt nicht zu den 65 Sekunden. Eine fehlgeschlagene oder unklare Startprüfung
startet keine Quarantäne und gibt D1 auch nach 65 Sekunden nicht frei. Mindestens
65 Sekunden monotone Laufzeit **und** entsprechender geprüfter UTC-Fortschritt
müssen verstrichen sein; bei unklarem Zeitverlauf bleibt D1 gesperrt. Während
der Quarantäne werden Requests abgelehnt, nicht für eine spätere Annahme
zwischengespeichert. Jede neue Instanz durchläuft sie selbst.

Vor Restore oder Failover mit möglicherweise verlorenen Reservationen müssen
**alle** D1-Annehmer gestoppt beziehungsweise nachweislich gesperrt sein; sie
durchlaufen anschließend die Startprüfungen und die volle Quarantäne. Eine
neue Instanz in Quarantäne schützt keine weiterlaufende alte Instanz. Nach dem
geprüften Wiederanlauf sind die vor dem Ausfall annehmbaren Envelopes abgelaufen.
Ein zusätzlicher kryptografischer Replay-Fortschrittsnachweis ist gemäß der
Entscheidung zu Gate 3 nicht mehr Voraussetzung.

Die bestehenden B5/B6-Prüfungen bleiben Startbedingungen: Job-Ledger und Audit
werden weiter bidirektional abgeglichen, signierte Köpfe gegen den getrennten
Anker geprüft. Jede vorhandene Replay-Zeile braucht genau ihre versionierte
Annahme-Bindung im Audit und den passenden Job; eine dort erwartete fehlende
Zeile braucht einen gültigen Bereinigungsnachweis. Ein isoliert zurückgespielter
Replay-Stand mit widersprechender Audit-Historie bleibt HOLD. Die 65 Sekunden
heilen weder einen Anker-Vorlauf noch einen fehlenden Audit-Präfix. Kein
Anchor-Reset und keine Löschung der Job- oder Audit-Historie.

Die D1-Migration ist **nie** Teil des automatischen Migrationslaufs vor
Dienststart. Erstinstallation und spätere Migrationen brauchen einen getrennt
freigegebenen Ablauf für Replay-Tabelle, Höchstzeit, Guards und Rollen. Er
erhält den vorhandenen Zustand beziehungsweise belegt eine echte Erstinstallation;
bei ungewissem Vorzustand bleibt D1 gesperrt.

#### 4.9.5 Auditierte Bereinigung durch eigene Rolle

Die Bereinigung läuft **im Kern-Prozess**. Ein interner Wartungspfad verwendet
eine eigene DB-Verbindung mit eigener DSN als **`genius_d1_cleanup`**; die
normale Runtime-DSN als `genius_core` bleibt davon getrennt und ohne DELETE auf
Replay. Normale Request-/Job-Pfade erhalten weder diesen Zugang noch seine
Verbindung. Die vorhandene `AuditAuthority` im Kern signiert den
Bereinigungs-Kopf; es gibt keinen externen Bereinigungsprozess mit einer
Kopie ihres privaten Schlüssels und keine neue Signier-RPC.

Nach erfüllter Start-Quarantäne darf dieser Wartungspfad ausschließlich
Zeilen mit `expires_at < safe_now - 65` bereinigen. `safe_now` kommt aus einer
frischen, bestandenen Uhrprüfung nach 4.9.3, niemals aus einem Caller-Parameter
oder einer ungeprüften DB-/Host-Uhr. Bei exakt 65 Sekunden Abstand bleibt die
Zeile erhalten. Unter der gemeinsamen Audit-/Ankersperre werden Zeit und
Grenze direkt vor dem begrenzten `DELETE ... RETURNING` erneut geprüft; DELETE
sperrt die betroffenen Zeilen. Sein tatsächlicher Rückgabesatz bestimmt den
Bereinigungsnachweis. Ein vorgelagertes `SELECT ... FOR UPDATE` auf Replay ist
kein Teil dieses Rechteprofils. Unbekannter Zeit-/Speicherzustand oder eine
abweichende Rückgabe ist REFUSE mit ROLLBACK der gesamten Transaktion.

Vorgeschlagene Rechte: Der eigene DB-Zugang **`genius_d1_cleanup`** erhält nur
`EXECUTE` auf den eng begrenzten, geprüften Bereinigungspfad, **kein direktes
DELETE**. Dessen Routine verwendet verbindlich **`SECURITY DEFINER`**;
ihr separater, minimal berechtigter `NOLOGIN`-Eigentümer erhält ausschließlich
Replay-`SELECT`/`DELETE`, `SELECT`/`INSERT` auf `audit_chain` und `audit_heads`
sowie `SELECT` und
`UPDATE (max_seen_at)` auf `d1_clock_state` unter dessen Monotonie-Guard.
Er ist weder Tabelleneigentümer noch DDL-/Anker-Administrator. Die Zeitprüfung/
Fortschreibung erfolgt über denselben geprüften Uhrmechanismus aus 4.9.3;
der Bereinigungsaufrufer erhält diese Tabellenrechte nicht. Der Aufruf erzwingt
feste Zieltabellen,
sicheren `search_path`, Zeitgrenze, begrenzte Zeilenmenge und Audit-Atomizität.
Für `PUBLIC`, die normale Core-Runtime-DB-Rolle und die Portal-Runtime sind
`EXECUTE`, Rollenmitgliedschaft und `SET ROLE` auf diesen Pfad ausgeschlossen.
Diese Trennung betrifft die DB-Identitäten innerhalb des Kern-Prozesses; sie
behauptet keine zusätzliche Prozessisolation. Weder Zugangsdaten noch private
Signierschlüssel gehören ins Repository. Der interne Wartungskoordinator
leitet den Audit-Nachweis aus dem tatsächlichen DELETE-Rückgabesatz ab und
lässt die `AuditAuthority` den Kopf innerhalb derselben noch offenen
Bereinigungstransaktion signieren. Die begrenzte Schnittstelle muss einen
COMMIT ohne diesen passenden Nachweis verhindern; konkrete Routine, Guards
und Schnittstelle werden vor DB-Code mitgeprüft.

Replay-DELETE, versioniertes Bereinigungs-Event und dessen **bereits signierter**
Kopf committen in **einer Transaktion** unter denselben Durability-Gates wie
4.9.2. Der Audit-Vertrag muss für jede entfernte Zeile ihren Annahme-Event-
Verweis/Zeilendigest und die geprüfte Bereinigungszeit nachvollziehbar halten;
eine bloße Anzahl oder ein nicht auflösbarer Batch-Summendigest reicht nicht.
`expires_at` aus der Annahme-Bindung erlaubt die Prüfung der 65-Sekunden-Grenze
auch nach DELETE. Raw-Nonce und Auftragstext werden dafür nicht protokolliert.
Fehlendes Event, fehlender gültiger Kopf oder Teilfehler führen zu ROLLBACK.
Nach COMMIT gilt die Ankerbestätigung aus §6 auch für diesen Pfad. Bei unklarem
Ausgang bleibt D1 gesperrt; Recovery gleicht die atomare Löschung samt Audit ab,
bevor ein weiterer Bereinigungslauf zugelassen wird.

Gelöscht werden ausschließlich die qualifizierten Replay-Zeilen. `job_ledger`,
Audit-Kette, signierte Köpfe und Höchstzeit werden nie zur Platzgewinnung
gekürzt. Die normale Core-Runtime-DSN behält auf Replay **nur SELECT und INSERT**.

#### 4.9.6 Lastbudget — Zahlenvorschlag für Kaans Freigabe (Gate 5)

Das Startprofil gilt für die geschlossene Beta mit Kaan als einzigem
Portal-Nutzer und einem D1-Dienst-Principal. Die Raten gelten insgesamt für
den angegebenen Bereich, **nicht je Prozess, Worker, Session oder Schlüssel**.
Bei den Core-Token-Buckets ist die Kapazität die gesamte sofort verfügbare
Tokenzahl; jede Anfrage kostet ein Token.

| Bereich | Vorgeschlagene Grenze | Wann belastet / Verhalten bei Erschöpfung |
| --- | --- | --- |
| D1 am Proxy, je verifizierter Transport-Quelle | 30 Anfragen/min, nginx `burst=5 nodelay` | Vor Core-Weiterleitung; auch unsignierte/ungültige Versuche; weiterer Überschuss REFUSE, keine Warteschlange |
| Globales D1-Vorab-Budget im Kern | 60 Anfragen/min, Token-Kapazität 10 | Nach billigen Framing-/Größenprüfungen, vor JSON, Body-Digest und Signatur; auch ungültige Versuche; REFUSE |
| D1-Dienst-Bucket | 6 Anfragen/min, Token-Kapazität 2 | Unmittelbar nach erfolgreicher Signaturprüfung; auch spätere Replay-/Zeit-Ablehnungen verbrauchen das Token ohne Erstattung; REFUSE |
| D1-Verbindungen | höchstens 4 insgesamt über alle Core-Instanzen | Zusätzlich zur bestehenden Core-Verbindungsgrenze; keine Erhöhung durch weitere Instanzen |
| Gleichzeitig laufende D1-Jobs | höchstens 2 insgesamt, keine Warteschlange | Vor Reservation; belegt zusätzlich das bestehende Core-`max_in_flight`, dessen strengere Grenze immer gewinnt |
| Replay-Zeilen | Warnung ab 64, Annahmestopp bei 128 vorhandenen Zeilen | Gesamte gemeinsame Tabelle, einschließlich abgelaufener, noch nicht bereinigter Zeilen; kein 129. Insert |
| Bereinigung | höchstens 30 s zwischen erfolgreichen Läufen, höchstens 32 Zeilen je atomarem Lauf | Erst nach Quarantäne und nur nach 4.9.5; keine unlimitierte Aufholschleife |
| Portal, vor jedem Signierversuch | 10/min je serverseitig geprüfter Nutzerreferenz UND portalweit, Token-Kapazität jeweils 2; je 200 Versuche pro UTC-Tag | Einschließlich fehlgeschlagener Versuche, über Sessions/Instanzen/Neustarts; konservatives Ein-Nutzer-Profil, nicht 10 Core-Annahmen/min |
| Freier DB-/WAL-Speicher | D1-Stopp bei weniger als 2 GiB je betroffenem Volume oder UNKNOWN | Zusätzliche Mindestreserve; falls gemessener Bedarf für Audit, WAL, Jobs oder Cleanup größer ist, früher sperren |
| Serialisierte Anker-Anfrage einschließlich Protokollhülle | Warnung ab 32 MiB, D1-Annahmestopp ab 48 MiB | Unterhalb des bestehenden harten 64-MiB-Transportlimits |
| Berechnete Anker-Zustandsgröße | Warnung ab 8 MiB, D1-Annahmestopp ab 12 MiB | Unterhalb des bestehenden harten 16-MiB-Dateilimits |

Quelle am Proxy ist die tatsächliche Peer-Adresse des geschützten Ingress oder
eine durch einen ausdrücklich vertrauten vorgelagerten Hop verifizierte
Transport-Identität. Browser-`Forwarded`-/`X-Forwarded-*`-Werte und Envelope-IDs
bestimmen diesen Schlüssel nicht. Geteilte Vercel-/NAT-Adressen teilen das
Quellbudget; Quellwechsel erzeugt keinen neuen Dienst-Bucket. nginx-`burst`
bezeichnet den erlaubten Überschuss, nicht dieselbe Token-Kapazität wie im Kern;
der Proxytest muss die tatsächliche Grenze belegen. Der geschützte Ingress
bleibt Voraussetzung; das Quellenlimit allein verhindert kein Aushungern
durch verteilte Quellen.

Vorab-/Dienst-Buchungen und Replay-Kapazitätsprüfung müssen über alle aktiven
Core-Instanzen atomar wirksam sein. Ein `COUNT` mit anschließendem ungeschütztem
INSERT genügt nicht. Buchungen werden bei späterer Ablehnung oder ROLLBACK der
Job-Transaktion nicht erstattet. Neustart, Schlüsselrotation oder eine weitere
Instanz dürfen keine zusätzlichen Burst-Tokens erzeugen. Erforderlich ist ein
gemeinsamer dauerhafter Budgetzustand; UNKNOWN/unerreichbar bedeutet REFUSE,
kein In-Memory-Fallback. Verbindungs- und Jobgrenzen brauchen gemeinsame
Durchsetzung oder fest verteilte Teilkontingente mit höchstens derselben Summe.
Die konkrete Budget-Speicherung und das Portal-Budget vor dem Signieren gehören
zum D1-Lastnachweis. Die heutigen prozesslokalen Limits sind dafür kein Beleg.

Vor der Signaturprüfung einschließlich der ersten Zeitprüfung werden nur
Quell- und Vorab-Budget belastet. Signaturgeprüfte Replay-/Zeit-Ablehnungen
belasten gemäß Gate 8 den Dienst-Bucket, **keine Nutzerquote**. Bestehende
Core-Verbindungsgrenzen, Body-/Headerlimits, Policy- und Anker-Gates bleiben
wirksam. Dieser Entwurf erweitert keine Nutzerrechte.

**Begründung der Startzahlen (nur Vorschlag, keine Messung):** Proxy 30/min entspricht einer Anfrage je 2 s pro verifizierter Transportquelle; `burst=5 nodelay` statt Burst 10 vermindert sofortige Spitzen (nginx-Burst ist kein Kern-Token-Bucket). Das globale Vorab-Budget 60/min mit Kapazität 10 erlaubt bis zur doppelten Quellrate, ohne vor der Signaturprüfung 20 sofortige Token freizugeben. Der signaturgeprüfte Dienst-Bucket 6/min mit Kapazität 2 statt Kaans Ausgangswert 20/min und Burst 5 begrenzt die teure gemeinsame Audit-Kette für eine geschlossene Beta mit genau einer Person. Vier gleichzeitig verarbeitete D1-Verbindungen statt acht je Quelladresse decken zwei laufende Jobs mit geringem Reserveplatz ab. Zwei statt vier D1-Jobs lassen einen zweiten Auftrag zu, ohne `max_in_flight` auszuweiten. Diese Zahlen müssen unter realer Last nachjustiert und durch Kaan freigegeben werden.

**Portalbudgets:** Vor dem Signieren muss die Portal-Runtime atomar sowohl den Zähler der *serverseitig verifizierten* Nutzerreferenz als auch einen gemeinsamen Portal-Zähler prüfen und belasten: 10/min, Kapazität 2, höchstens 200 Versuche pro geprüftem UTC-Kalendertag. 10/min lässt kurze Bedienphasen zu; 200/Tag begrenzt unbegrenzte Signierarbeit und Kosten. Fehlgeschlagene Versuche verbrauchen ebenfalls das Portal-Signierbudget und werden nicht erstattet; ein Core-Refusal belastet dagegen gemäß Gate 8 **keine** Nutzerquote. Es gibt keine neu erzeugte Kapazität pro Session, Neustart oder Instanz. Unbekannter Zähler-/Zeitstatus bedeutet REFUSE. Ein zweiter Portal-Nutzer ist vor D3 weiterhin unzulässig.

**Replay-/Bereinigungsprofil:** Der rechnerische Normalbestand bei 6/min, Kapazität 2 und 161 Sekunden Aufbewahrungsfenster ist `ceil(2 + (6/60) × 161) = 19` Zeilen. Warnung bei 64 und Stopp bei 128 lassen somit rechnerisch 45 bzw. 109 Zeilen Reserve im rückstaufreien Betrieb; die ursprünglichen 100.000 wären für diese Beta keine frühe Störungserkennung. Eine Bereinigung von 32 je 30 s deckt höchstens fünf neu budgetierte Dienstversuche je 30 s ab, aber keinen unbegrenzten Altrückstau. Strikte atomare Kapazitätsgrenze, 2 GiB Mindestfreiraum je DB-/WAL-Volume und bestehende Audit-/Anker-Schranken wirken gemeinsam. Warn- und Ankergrenzen bei 32/48 MiB gegenüber 64 MiB Transport-Hardlimit sowie 8/12 MiB gegenüber 16 MiB Zustands-Hardlimit sollen Betriebsreserve für notwendige Ereignisse lassen; ihre **Angemessenheit ist nicht nachgewiesen**. Bei unbekanntem oder nicht ausreichendem Freiraum D1 verweigern; `DELETE` gibt nicht automatisch physische Relationsgröße frei.

**Rechenbasis:** Bei 6/min (= 0,1/s) und Token-Kapazität 2 werden höchstens
`2 + 0,1 × t` Dienst-Tokens in einem Zeitraum von `t` Sekunden verbraucht.
Jede Reservation braucht ein solches Token. Zwischen frühestmöglicher Annahme
und Bereinigbarkeit liegen höchstens 65 + 65 Sekunden;
mit 30 s bis zum nächsten erfolgreichen Bereinigungslauf und einer Sekunde für
die strikte Ablaufgrenze ergibt das konservativ
`ceil(2 + 0,1 × 161) = 19` Zeilen.
Die 19 Zeilen gelten im rückstaufreien Dauerbetrieb mit wirksamen Budgets und
gültiger Uhr, wenn jede bereinigbare Zeile spätestens im nächsten Lauf entfernt
wird. Nach Quarantäne, Ausfällen oder mit Altbestand kann der Bestand höher sein:
ein erfolgreicher Lauf mit höchstens 32 Löschungen beseitigt nicht zwingend den
gesamten Rückstau. Warnung bei 64 und die atomare Sperre bei 128 bleiben maßgeblich;
sie sind keine gemessene DB-Kapazität oder garantierte Reaktionsfrist.

Audit-Historie wächst weiter. Ein neues D1-Ablehnungs-Event setzt das für diese
Anfrage bereits erfolgreich belastete Dienst-Token voraus und darf je
`(issuer, nonce)` höchstens einmal entstehen. Scheitert die anfängliche
Dienst-Tokenbuchung, entsteht keines. Die Zahl neu budgetierter
Ablehnungsnachweise wird damit durch das **Dienst-Budget von 6/min mit
Token-Kapazität 2** begrenzt,
nicht allein durch das Vorab-Budget. Erfolgreiche Jobs können mehrere
Audit-Events erzeugen. Die vorgeschlagene Ankerreserve muss die größten
zulässigen Events, laufende Jobs, noch ausstehende budgetierte
Ablehnungsnachweise und erforderliche Bereinigungs-Events abdecken; das ist
vor Freigabe zu messen. Bei fehlender oder unbekannter Reserve sperrt D1 schon
vor weiterer Signaturarbeit. Die bestehende Kapazitätsprüfung vor jedem
Audit-COMMIT bleibt maßgeblich; eine kleine Replay-Tabelle hebt sie nicht auf.

Der Zeilendeckel ist kein Langzeitbudget für Job-Ledger, Audit, WAL oder freien
Plattenplatz. Überwachung und ein vor deren Erschöpfung wirksames Stopprofil
bleiben Teil des Betriebsnachweises. DELETE garantiert zudem keine Verkleinerung
der Relationsdateien. Lastprobe, Alarmnachweis und Kaans Zahlenfreigabe stehen aus.

**Abnahme vor aktivem DB-Code:** echte PostgreSQL-Prüfungen für gemeinsamen
COMMIT/ROLLBACK und Crash-Ausgänge; parallele Nonce-/Kapazitätskonflikte;
Uhr-Rücksprung im Prozess, nach Neustart und zwischen Instanzen; fehlende/
abgesenkte Höchstzeit und unzulässige Rollenrechte; Quarantänegrenzen auch bei
einer simulierten Startprüfdauer von mehr als 65 Sekunden: vor Ablauf weiterer
65 Sekunden nach erfolgreichem Abschluss bleibt D1 REFUSE; fehlgeschlagene
Startprüfungen starten keine Freigabefrist; eingefrorene Uhr; koordinierter
Restore; Bereinigung bei exakt/mehr als
65 Sekunden sowie fehlendem Audit/Kopf und Crash nach COMMIT; bidirektionaler
Abgleich auch nach Bereinigung; Budgeterschöpfung, Neustart und unbekannter
Speicherzustand. Zusätzlich: parallele und nach Neustart wiederholte
Ablehnungen je `(issuer, nonce)` erzeugen höchstens ein Event, auch bei
veränderten Envelope-Feldern; weitere Versuche und leere Dienst-Buckets
erzeugen kein requestbezogenes Logging. Die erste Zeitprüfung und ungültige
Signaturen dürfen `max_seen_at` weder sperren noch fortschreiben; spätere
berechtigte Zeitprüfungen erkennen zwischenzeitliche Rücksprünge. Der interne
Bereinigungspfad signiert ausschließlich über die `AuditAuthority` im Kern;
die normale Runtime-DSN kann weder DELETE noch den Bereinigungspfad ausführen.
Link-, Konsistenz- und Diff-Prüfungen dieses Entwurfs belegen diese späteren
Laufzeiteigenschaften nicht.

#### 4.9.7 Geschützter Eingang vor dem Vorab-Budget (Gate 10)

**Entwurf zur getrennten Freigabe:** Der Portal-Backend-Dienst auf Vercel spricht den Kern ausschließlich über HTTPS und einen geschützten Core-Proxy an. Vercels ausgehende IP-Adressen stammen standardmäßig aus einem dynamischen Pool; feste ausgehende Adressen dürfen nicht als bestehende Voraussetzung unterstellt werden ([Vercel: IP-Allowlisting](https://vercel.com/kb/guide/how-to-allowlist-deployment-ip-address)). Alternativ verfügbare [Static IPs](https://vercel.com/docs/networking/static-ips) sind keine Authentisierung und werden hier nicht eingerichtet.

Für die Ein-Nutzer-Beta wird ein **separater hochentropischer Ingress-Schlüssel** in einem nur serverseitig gesetzten Transportheader, vorgeschlagen `X-Genius-Ingress-Key`, als einfache Zugangsgrenze vorgeschlagen. Der erste TLS-terminierende Proxy-Hop muss bereits auf der rohen Headerliste vor Zusammenführung, Mapping oder Protokollübersetzung ASCII-case-insensitiv prüfen, dass der Header **genau einmal** vorkommt, und dessen Wert vor Core-Weiterleitung verifizieren. Fehlender, falscher oder doppelter Header wird am Proxy abgelehnt und darf kein globales D1-Vorab-Token im Kern verbrauchen. Kein alternativer öffentlicher Listener, Port oder Route darf diese Prüfung umgehen.

**Vertragskonflikt explizit offen:** Der aktuelle D1-Proxy-Vertrag in [Issue #159](https://github.com/Kaancodm/GeniusNew/issues/159) verbietet das Entfernen sicherheitsrelevanter Header. Ein Entfernen **ausschließlich dieses zusätzlichen, bereits erfolgreich verifizierten Ingress-Headers** vor dem Core wird vorgeschlagen, damit er weder im Core noch in Logs erscheint und niemals als `Authorization`, D1-Signatur oder Core-Principal verwendet werden kann. Diese eng begrenzte Ausnahme braucht die ausdrückliche Aktualisierung des Vertrags in #163, unabhängiges Review und Kaans Freigabe. Bis dahin **Gate 10 HOLD**; andere D1-/Framing-Header werden nicht geändert, und der Kern lehnt ihre Duplikate unverändert anhand der rohen eingehenden Liste ab. Der neue Header darf keine User-/Job-Autorität ersetzen.

Schlüssel nur in getrennten Secret-Stores für Vercel-Backend und Proxy, niemals Browser, Repository, Fehlerantwort, Request-Log oder Trace; keine Ableitung aus Core-Root-Secret oder D1-Signierschlüssel. Rotation, Proxy, TLS, Firewall und Deployment bleiben unverändert und benötigen eigene Freigabe. **Betriebsnachweis:** tatsächliche Vercel-Runtime, nginx/TLS-/HTTP2-Übersetzung, Duplikat-/Mischheader, ungültige/fehlende Schlüssel, Bypass-Ports und Lastbudget müssen reproduzierbar geprüft werden. Ein korrekter Ingress-Schlüssel umgeht niemals Signatur-, Zeit-, Replay-, Policy-, Audit- oder Quotenprüfungen. mTLS ist eine mögliche spätere Alternative, hier nicht eingerichtet.

## 5. Portal-Schema

Portal-Tabellen sind keine Autorität für Core-Policy oder Worker-Rechte.

### 5.1 Nutzer

```sql
create table users (
    user_id text primary key,
    subject text not null unique,
    email_normalized text not null unique,
    password_hash text not null,
    status text not null check (status in ('ACTIVE', 'DISABLED')),
    created_at bigint not null,
    updated_at bigint not null
);
```

`password_hash` ist ausschließlich ein Argon2id-Hash über `argon2-cffi`.

### 5.2 Sessions

```sql
create table sessions (
    token_digest char(64) primary key,
    user_id text not null references users(user_id),
    created_at bigint not null,
    expires_at bigint not null,
    revoked_at bigint
);
```

Nur SHA-256 des zufälligen Session-Tokens wird gespeichert.

### 5.3 Rollen

```sql
create table user_roles (
    user_id text not null references users(user_id),
    role text not null check (role in ('USER', 'APPROVER', 'ADMIN')),
    primary key (user_id, role)
);
```

Eine Portalrolle allein erteilt noch kein Core-Approval. Der Server prüft die Rolle und
ruft danach die dedizierte Core-Approval-Schnittstelle auf.

### 5.4 Quoten

```sql
create table user_quotas (
    user_id text primary key references users(user_id),
    max_pending_jobs bigint not null check (max_pending_jobs >= 0),
    max_jobs_per_hour bigint not null check (max_jobs_per_hour >= 0),
    max_jobs_per_month bigint not null check (max_jobs_per_month >= 0),
    updated_at bigint not null
);

create table quota_usage (
    user_id text not null references users(user_id),
    window_kind text not null check (window_kind in ('HOUR', 'MONTH')),
    window_start bigint not null,
    jobs_started bigint not null check (jobs_started >= 0),
    primary key (user_id, window_kind, window_start)
);
```

Quoten werden serverseitig geprüft. Prüfung und Inkrement des passenden
`quota_usage`-Datensatzes erfolgen in einer Transaktion mit Row Lock/Upsert; zwei
parallele Requests dürfen das Limit nicht gemeinsam überschreiten. Portalwerte sind
keine Client-Autorität und dürfen nicht aus dem Browser übernommen werden.

### 5.5 Portal-Verlauf

```sql
create table portal_jobs (
    user_id text not null references users(user_id),
    job_id text not null,
    created_at bigint not null,
    last_status text not null,
    details jsonb,
    primary key (user_id, job_id)
);
```

Diese Tabelle ist nur UX-Zustand. Bei Widerspruch gewinnt der Kern.

## 6. Audit-Commit und Anchor-Crashfenster

Ein Audit-Eintrag wird zuerst dauerhaft in der Core-Datenbank committed. Danach wird der
neue Kettenkopf dem separaten Anker vorgelegt. **Bevor der Anker bestätigt hat, darf der
sicherheitsrelevante Ablauf nicht als erfolgreich fortgesetzt werden.**

Crash zwischen DB-Commit und Anchor-Bestätigung:

- DB-Kette und der in derselben Transaktion gespeicherte **bereits signierte** Kopf
  können dem externen Anker voraus sein;
- beim Neustart wird die **vollständige** Core-Kette ab Index 0 verifiziert und jeder
  gespeicherte Audit-Kopf kryptografisch geprüft;
- es darf keinen Record ohne passenden gültigen Kopf und keinen Kopf ohne passenden
  vollständigen Kettenpräfix geben;
- der bereits verankerte `count/head_hash` muss an exakt seiner Position in dieser
  vollständigen Geschichte wiedergefunden werden;
- erweitert die DB-Kette diesen Präfix konsistent, wird nur der höchste bereits vor dem
  Crash gespeicherte, gültig signierte Kopf zusammen mit dem vollständigen
  Record-Snapshot bis zu diesem Kopf erneut committed;
- ist der Anker der DB voraus, fehlt der verankerte Präfix, existiert ein unsignierter
  DB-Suffix oder teilt die DB nicht dieselbe Geschichte, startet der Dienst nicht.

## 7. Transaktionen und Parallelität

- Security-Reservationen verlassen sich auf DB-Constraints, nicht auf Prozess-Locks.
- Job-Reservation: atomarer `INSERT` mit Primary-Key-Constraint.
- Acceptance: zugehörige `job_ledger`-Zeile sperren, Zustand
  `EXECUTION_COMMITTED` verlangen, Acceptance einfügen und Ledger in **derselben
  Transaktion** nach `COMPLETED` überführen; der DB-Trigger verweigert jeden anderen
  Ausgangszustand.
- Pending-Auflösung: Approval, Ablauf oder endgültiger Refusal entfernen
  `pending_jobs` und ändern den Ledger-Zustand in **derselben Transaktion**.
- Approval-Zustandswechsel: `SELECT ... FOR UPDATE`; bei erfolgreichem Consume sind
  Record-Insert, Pointer-Update, Pending-Löschung und Ledger-Reservation eine
  Transaktion.
- Audit-Append: Event-Record und bereits signierter Audit-Kopf sind eine Transaktion.
- Ab B6: jede sicherheitsrelevante Ledger-/Approval-Mutation und ihr Audit-Event werden
  atomar gemeinsam committed; kein Zustand darf ohne seinen Audit-Nachweis sichtbar
  werden.
- Kein Worker läuft innerhalb einer lang gehaltenen DB-Transaktion.
- Ein Commit-Fehler ist ein Refusal; kein In-Memory-Fallback.

## 8. Startprüfung — fail closed

Vor Öffnen des HTTP-Listeners:

1. Datenbank erreichbar;
2. erwartete Migrationen mit korrekten Checksums vorhanden;
3. Digest-, Zeit- und State-Felder formal gültig; unbekannte oder gegenüber dem Code
   **neuere Migrationen** sind ebenso ein Startfehler wie fehlende/falsche Checksums;
4. gespeicherte Handoff-/Result-Wire-Paare gemeinsam erneut prüfen und für jede
   Acceptance exakt einen `job_ledger(COMPLETED)`-Eintrag mit identischer `job_id`
   und identischem `handoff_sha256` verlangen;
5. Pending-Wires aus Bytes neu parsen, Signatur prüfen und **bidirektional 1:1** mit
   `job_ledger(PENDING_APPROVAL)` abgleichen; zusätzlich müssen
   `sha256(pending_jobs.wire) = job_ledger.handoff_sha256`, `subject` und
   `expires_at` exakt übereinstimmen;
6. Approval-Hashes, Vorgängerketten, erlaubte Zustandsfolgen und unveränderliche
   Scope-/Zeitfelder prüfen;
7. vollständige Audit-Kette **und alle gespeicherten signierten Audit-Köpfe** prüfen;
   ein Record ohne gültigen Kopf ist ein Startfehler;
8. Audit-Kopf gegen Anchor-Store verifizieren; liegt die DB konsistent vor dem Anker,
   nur den höchsten **bereits vor dem Crash gespeicherten und verifizierten** Kopf
   zusammen mit dem vollständigen Record-Snapshot erneut committen;
9. ab B6 Ledger und Approval-Speicher in beide Richtungen gegen die Audit-Kette
   abgleichen: `acceptance_ledger ↔ RESULT_ACCEPTED`,
   `job_ledger ↔ HANDOFF_ADMITTED/folgende Zustandsereignisse` und
   Approval-Records ↔ `approval_record_hash`;
10. erst danach Requests annehmen.

Für einen späteren aktiven D1-Pfad kommen vor Schritt 10 die Prüfungen aus 4.9
hinzu: D1-Schema/Rechte, Höchstzeit, Replay-/Job-/Audit-Abgleich, Lastbudget und
65-s-Start-Quarantäne. Bei einem Fehler bleibt D1 gesperrt. Der frühere separate
Replay-Fortschrittsnachweis ist durch Gate 3 ersetzt; die bestehende
Core-/Anker-Startprüfung bleibt erforderlich.

Fehlt eine Tabelle, Migration, Signatur, Hash-Verknüpfung oder der Anchor-Store:
**Start verweigern**.

## 9. Migrationen

Reihenfolge:

1. `0001_core_foundation`: Migrationstabelle, Job-/Acceptance-Ledger, CI-PostgreSQL;
2. `0002_pending_jobs`;
3. `0003_approval_store`;
4. `0004_audit_chain`: Audit-Records plus `audit_heads`;
5. `0005_portal_identity`: users, sessions, roles, quotas;
6. `0006_portal_history`.

Eine spätere, separat freizugebende D1-Migration legt die Tabellen und geprüften
Guards/Rollen aus 4.9 an.
Sie erhält eine neue Version nach den bestehenden Migrationen, ist aber vom
automatischen Lauf ausgenommen. D1-Code darf nicht vorher aktiviert und die
Tabellen nicht beim Dienststart ad hoc erzeugt werden. Es gilt das Restore- und
Installations-Gate aus 4.9.

Migrationen laufen vor Dienststart, einzeln in Transaktionen. Kein automatisches
„drop and recreate“ bei Fehlern.

## 10. Secrets und DB-Rollen

Mindestens getrennte Rollen:

- **Migrationsrolle:** Eigentümerin der Core-Tabellen und einzige Rolle mit DDL,
  `TRIGGER`, `TRUNCATE` oder `REFERENCES`;
- **`genius_core`:** Runtime, ausdrücklich **nicht** Eigentümerin der Tabellen und ohne
  DDL/`TRIGGER`/`TRUNCATE`/`REFERENCES`;
- **`genius_d1_cleanup`:** eigener DB-Zugang des internen Bereinigungspfads im
  Kern-Prozess, eigene DSN; nur `EXECUTE` auf den geprüften
  `SECURITY DEFINER`-Pfad aus 4.9.5, kein direktes Tabellen-DELETE. Dessen
  begrenzter `NOLOGIN`-Eigentümer ist für die normale Runtime-DB-Rolle weder
  erbbar noch per `SET ROLE` oder `EXECUTE` erreichbar; `AuditAuthority`
  signiert im Kern-Prozess;
- **`genius_portal`:** nur Portal-Tabellen, ohne Zugriff auf Core-Tabellen.

Mindest-Rechte der Core-Runtime pro Tabelle:

| Tabelle | Runtime-Rechte |
| --- | --- |
| `schema_migrations` | SELECT |
| `job_ledger` | SELECT, INSERT, UPDATE; **kein DELETE** |
| `acceptance_ledger` | SELECT, INSERT; kein UPDATE/DELETE |
| `pending_jobs` | SELECT, INSERT, DELETE; kein UPDATE |
| `approval_records` | SELECT, INSERT; kein UPDATE/DELETE |
| `approval_tokens` | SELECT, INSERT, UPDATE; kein DELETE |
| `audit_chain` | SELECT, INSERT; kein UPDATE/DELETE |
| `audit_heads` | SELECT, INSERT; kein UPDATE/DELETE |
| `api_key_digests` | SELECT |
| `d1_replay_reservations` (erst nach freigegebener D1-Migration) | SELECT, INSERT; kein UPDATE/DELETE/TRUNCATE/DDL |
| `d1_clock_state` (erst nach freigegebener D1-Migration) | SELECT, UPDATE nur `max_seen_at` unter Monotonie-Guard; kein INSERT/DELETE/TRUNCATE/DDL |

Die Runtime darf Schutztrigger weder ändern noch deaktivieren. Der Anchor-Store verwendet
keine dieser Zugangsdaten. Connection-Strings, Passwörter, Raw-Tokens und Dumps gehören
nicht ins Repository.

## 11. Umsetzungsgates

Jeder DB-PR muss:

- mit einem zuerst roten Regressionstest beginnen;
- neue Ablehnungen in `scripts/refusals.py` aufnehmen;
- PostgreSQL in CI verwenden, nicht nur einen Mock;
- Tests, Demo und Refusal-Guard nacheinander grün haben;
- Byte-Manipulation testen: ändert sich ein gespeichertes Byte in Wire/Event/Result,
  muss Laden oder Start fehlschlagen;
- Acceptance gegen einen anderen als den im Job-Ledger gebundenen Handoff muss durch
  DB-Constraint und Startprüfung scheitern;
- Acceptance aus `PENDING_APPROVAL`, `RESERVED` oder `REFUSED` muss durch den
  DB-Trigger scheitern; der Acceptance-Trigger selbst muss den Übergang nach
  `COMPLETED` in derselben Transaktion erzwingen;
- `job_ledger` darf nicht gelöscht oder rückwärts bewegt werden; verbotene
  Zustandsübergänge und Änderungen an Identitätsfeldern müssen DB-seitig scheitern;
- Approval, Ablauf und endgültiger Refusal eines Pending-Jobs müssen die Pending-Zeile
  und den Ledger-Übergang atomar auflösen; Neustart danach darf keine stale
  `pending_jobs`-Zeile finden;
- Approval-Records sind für die Runtime append-only; `GRANTED` darf nur Root sein,
  Nachfolger nur `CONSUMED`/`REVOKED` aus `GRANTED`; Scope und Gültigkeitsfenster
  dürfen sich nicht ändern; Pointer-Rücksprung oder Fork müssen DB-seitig scheitern;
- ein per SQL angehängtes Audit-Event ohne in derselben Transaktion gespeicherten,
  gültig signierten Kopf muss beim Start abgelehnt werden;
- ab B6 müssen Ledger/Approval-Zustand und Audit-Ereignisse in beide Richtungen
  gegeneinander geprüft werden;
- bei geschlossener `SECURITY.md`-Grenze den offen gehaltenen Test umkehren.

Implementierungsreihenfolge:

1. PostgreSQL-Verbindung + hash-gepinnte `psycopg`-Abhängigkeit + Migration
   `0001_core_foundation`;
2. persistentes Job-Ledger;
3. persistentes Acceptance-Ledger;
4. Pending Jobs;
5. Approval Store;
6. Audit Chain + Startup-Reconciliation mit Anchor;
7. Portal-Identität und Sessions;
8. Portal.
