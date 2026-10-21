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

**Nachbesserung zu #161, Entwurf vom 21.10.2026:** Die folgenden Präzisierungen
adressieren F1/F2/N3/N5/N6 aus
[Claudes Review](https://github.com/Kaancodm/GeniusNew/pull/161#issuecomment-6250132366).
Sie sind eine neue Reviewvorlage, keine übernommene Reviewfreigabe. Insbesondere
die Cleanup-Schnittstelle, der Portal-Zeit-/Budgetvertrag und der Ingress bleiben
vor Code unabhängig zu prüfen. Zahlen, Betriebsort, Restore-Epochen und
`SECURITY.md`-Grenzen werden hier nicht freigegeben.

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
über den versionierten `replay_key_sha256` und das getrennt gebundene
`expires_at`. Raw-Envelope, Signatur,
Auftragstext und Zugangsdaten gehören nicht ins Audit. Die bestehenden
`AuditEvent`-Versionen 1/2 haben diese D1-Felder noch nicht; Event-Version,
Digest-Profil und Abgleich folgen dem Entwurfsprofil `d1-audit-v1` aus
[`PORTAL-CORE-D1-DRAFT.md`](PORTAL-CORE-D1-DRAFT.md), nicht stillen Zusatzfeldern.
Dabei ist `replay_key_sha256` der dort definierte Digest über genau
`domain`, `issuer` und `nonce`; `expires_at` ist ein getrennt gebundenes
Nachweisfeld und kein Bestandteil dieses Schlüsseldigests.

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
und genau `NONCE_CONFLICT` oder `REQUEST_EXPIRED` als Ablehnungsgrund.
Nur der erste auditierte Versuch erhält eine zufällige `attempt_id` im eigenen
Versuch-Namensraum. Es entstehen weder Replay-Zeile noch Job; Job-/Handoff-Felder
werden nicht durch erfundene IDs ausgefüllt.

Für die Deduplizierung bindet das Event zusätzlich einen versionierten,
domänenseparierten Digest der kanonischen Werte `issuer` und `nonce`.
Das feste Profil heißt `d1-refusal-dedupe-v1`: SHA-256 über die ASCII-Kanonform
von genau diesen drei Feldern nach dem D1-JSON-Profil, ohne Zeilenumbruch:

```text
{"domain":"geniusnew.portal-core.refusal-dedupe.d1.v1","issuer":<Issuer>,"nonce":<64 lowercase Hex-Zeichen>}
```

`expires_at`, `key_id`, `audience`, Envelope-Digest und Ablehnungsgrund gehören
**nicht** in diesen Schlüssel: geänderte Werte erlauben kein zweites Event.
Der Replay-Schlüsseldigest ist dafür kein Ersatz. Prüfung auf
einen vorhandenen Ablehnungsnachweis und Append erfolgen unter derselben
datenbankgestützten Audit-Serialisierung über alle D1-Annehmer. Die verifizierte
Audit-Kette ist die autoritative Deduplizierungsquelle; ein abgeleiteter Cache
darf nur unter derselben Audit-Sperre mit dem gerade verifizierten Kettenzähler
und Kopf übereinstimmen; jede Abweichung verwirft den Cache. Beim Start wird der
Nachweis aus der geprüften Kette rekonstruiert; mehrere Ablehnungs-Events zu
derselben Identität sind ein Startfehler. Replay-Bereinigung entfernt diesen
Nachweis nicht. Recovery erkennt den Ereignistyp ausdrücklich als Ablehnung
ohne Job-/Replay-Gegenstück und prüft weiterhin Signatur, Kette und Anker.
Die neue Event-Version muss diese Form ausdrücklich zulassen.

Für jeden weiteren Request derselben Identität gilt dieselbe anfängliche
Dienst-Tokenbuchung; es gibt keine zusätzliche zweite Buchung für das Audit.
Weitere Ablehnungen dürfen ausschließlich begrenzte, aggregierte Zähler erhöhen.
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

**Geschlossene Fallunterscheidung für F1 (Entwurf):** Die Reihenfolge gilt vor
jeder Mutation; eine globale Sperre gewinnt vor einem Einzelrequest-Grund.

| Zustand / Auslöser | Dauerhafte Wirkung dieses Requests | Sperre / Wiederanlauf |
| --- | --- | --- |
| Framing-/Formatfehler, ungültige Signatur oder erste Zeitprüfung vor Signatur | Kein D1-Audit, keine Reservation und kein Job; nur bereits zulässige Vorab-Budgets und begrenzter Kategorie-Zähler | Keine neue Freigabe; bestehende Sperren bleiben wirksam |
| Start-Quarantäne, Uhrfehler, unbekannter Integritäts-/Budget-/Speicherstand oder Kapazitätsstopp | Kein requestbezogenes Audit und Logging, kein Job; begrenzter Kategorie-Zähler, soweit verfügbar | D1 bleibt gesperrt; keine Signaturarbeit oder neue Buchung als Ersatz für fehlende Integrität |
| Anfängliche Dienst-Tokenbuchung scheitert | Kein Audit, auch beim ersten `(issuer, nonce)`; keine Erstattung anderer bereits zulässiger Buchungen | Keine Annahme; kein Ersatz-Token |
| Signatur gültig, Dienst-Token gebucht, bestätigtes vollständiges ROLLBACK und erstmals `NONCE_CONFLICT` oder `REQUEST_EXPIRED` vor Annahme-COMMIT | Genau der erste mögliche `D1_REQUEST_REFUSED`-Nachweis; nur Audit-Event und signierter Kopf, anschließend Anker | Fehler oder unbekannter Ausgang sperrt D1; keine erneute Jobannahme |
| Bereits nachgewiesene Ablehnung für dasselbe `(issuer, nonce)` | Kein zweites Event und keine neue Versuch-ID, nur begrenzter Kategorie-Zähler | Die Deduplizierung muss unter der Audit-Sperre bekannt sein |
| Unbekannter Annahme- oder Ablehnungs-COMMIT / fehlende Ankerbestätigung | UNKNOWN, weder neue Ablehnung ohne Job noch Erfolg behaupten | Alle D1-Annehmer bis zum gemeinsamen Recovery-/Ankerabgleich sperren |
| Ablauf nach bestätigtem Annahme-COMMIT | Bestehender Job erhält seinen atomaren Ledger-/Audit-Refusal | Kein `D1_REQUEST_REFUSED` ohne Job und kein neuer Nonce |

Die Zähler sind flüchtige, sättigende Aggregatzähler je fester Kategorie und
Prozess; sie enthalten keine Nonces, Envelope-Digests oder Nutzerreferenzen und
sind kein Sicherheitsnachweis. Ihr Verlust beim Neustart erlaubt keine Annahme.
Auch eine sonst auditierbare Ablehnung wird während Quarantäne, Integritäts-
oder Kapazitätssperre nicht durch einen neuen Auditversuch ersetzt.

Eine unbekannte erforderliche Audit-/COMMIT-/Ankerentscheidung sperrt den
**gesamten D1-Dienst über alle Instanzen**, nicht nur den betroffenen Request.
Für die Wiederfreigabe müssen alle D1-Annehmer nachweislich angehalten oder
gesperrt sein; danach vollständiger Abgleich der gespeicherten Operation mit
Replay, Job, Audit und Anker sowie alle Startprüfungen und die neue volle
Quarantäne nach 4.9.4. Ein Prozessneustart oder ein lokales Boolean reicht nicht.
Der dauerhafte gemeinsame Sperr-/Operationsnachweis ist Bestandteil des noch
zu reviewenden DB-Protokolls; solange dessen Crashfestigkeit nicht belegt ist,
bleibt die aktive D1-Implementierung HOLD. Widersprüchliche oder doppelte
append-only Audit-Nachweise erlauben weder Löschen noch automatische Heilung;
sie bleiben gesperrt und verlangen einen gesondert freizugebenden Recoveryweg.

Ein Crash nach bestätigtem ROLLBACK, aber vor dem ersten Ablehnungs-COMMIT kann
den Ablehnungsnachweis verlieren. Das Modell garantiert **höchstens einen**,
nicht einen in jedem Crashfall vorhandenen Nachweis. Ein späterer neuer,
erneut budgetierter Versuch darf nur nach eindeutigem Abgleich den ersten
Nachweis anlegen; ein Request wird dafür nicht automatisch wiederholt.

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
beginnt erst, wenn **alle Startprüfungen erfolgreich abgeschlossen** sind,
einschließlich Schema-/Rechte- und Speicherprüfung, vollständigem B5/B6-Abgleich von Job-Ledger
und Audit, Prüfung der signierten Köpfe gegen den getrennten Anker sowie
Uhrprüfung nach 4.9.3, D1-Replay-/Job-/Audit-Abgleich, bekanntem Budgetzustand,
Durability-Werten und nachgewiesener DB-/WAL-/Ankerreserve. Erst danach werden
die monotone Startmarke und die
geprüfte Core-UTC-Startzeit der Quarantäne gesetzt; die Dauer der Startprüfungen
zählt nicht zu den 65 Sekunden. Eine fehlgeschlagene oder unklare Startprüfung
startet keine Quarantäne und gibt D1 auch nach 65 Sekunden nicht frei. Mindestens
65 Sekunden monotone Laufzeit **und** entsprechender geprüfter UTC-Fortschritt
müssen verstrichen sein; bei unklarem Zeitverlauf bleibt D1 gesperrt. Während
der Quarantäne werden Requests abgelehnt, nicht für eine spätere Annahme
zwischengespeichert. Jede neue Instanz durchläuft sie selbst.
Scheitert eine dieser Prüfungen während der Quarantäne, werden beide
Startmarken ungültig; nach Behebung beginnen alle Prüfungen und anschließend
die vollen 65 Sekunden erneut. Eine Teilwartezeit wird nicht gutgeschrieben.

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

**Schnittstellenskizze für F2, ausdrücklich noch zu reviewen:** Die bestehende
Identitätsprüfung von `AuditStore.append(transaction=...)` bleibt unverändert.
Der Cleanup-Zugang wird weder als dessen eigene Verbindung ausgegeben noch
über einen optionalen Fremdverbindungsparameter eingeschleust. Vorgeschlagen
wird ein eigener `D1CleanupStore`, dessen Transaktions- und Ankersperre immer
an **seine eine** `genius_d1_cleanup`-Verbindung gebunden sind:

1. Nach erfolgreicher Uhr-/Reserveprüfung erwirbt dieser Store dieselbe
   datenbankgestützte Audit-/Ankersperre auf seiner Verbindung, beginnt eine
   neue Transaktion und setzt die Durability-Gates aus 4.9.2. Eine bereits
   offene oder fremde Transaktion ist REFUSE.
2. Eine eng berechtigte Routine, Arbeitsname `d1_cleanup_begin`, hält diese
   Sperre und erzeugt einen einmaligen Cleanup-Handle, gebunden an aktuelle
   DB-Transaktionsidentität und Verbindung. Sie bestimmt die erlaubte Menge
   selbst aus geprüfter Zeit und dem festen Limit, führt DELETE aus und
   bindet den **tatsächlichen** Rückgabesatz an diesen Handle. Der Aufrufer
   liefert weder beliebige Zielzeilen noch eine eigene Löschgrenze. Pro
   Verbindung/Transaktion ist höchstens ein offener Handle zulässig.
3. Der vorgeschlagene, migrationsverwaltete Konsistenzguard hält zu diesem
   Handle die genaue Menge der gelöschten Replay-Bindungen und ihrer
   ursprünglichen Annahme-Event-Digests fest. Eine `DEFERRABLE INITIALLY
   DEFERRED`-Prüfung verweigert COMMIT, solange ein Handle nicht vollständig
   durch genau seinen Bereinigungsnachweis abgeschlossen ist. Die
   `genius_d1_cleanup`-Rolle hat keine direkten Schreibrechte an diesem
   Guardzustand; `PUBLIC` und `genius_core` erreichen auch die Routinen nicht.
   Ein bloßes Python-`try/finally` ist kein Ersatz für den DB-Guard.
4. Python baut aus genau diesem geprüften Rückgabesatz einen Record des neuen
   Bereinigungsprofils und prüft die vollständige bisherige Kette, nächsten
   Index, Vorgängerhash und reservierte Ankergröße unter derselben Sperre.
   Die vorhandene `AuditAuthority` signiert den daraus berechneten Kopf.
   Kein anderer Prozess und keine SQL-Routine bekommt ihren privaten Schlüssel.
5. Eine zweite Routine, Arbeitsname `d1_cleanup_finish`, nimmt Record und
   bereits signierten Kopf **auf derselben noch offenen Verbindung** entgegen.
   Sie verlangt denselben Handle und dieselbe DB-Transaktionsidentität,
   identischen Ausgangskopf, genau dieselbe Löschmenge sowie genau die dazu
   gehörenden Annahmeverweise. Nur dann darf sie Event und Kopf einfügen und
   den Handle abschließen. Handle-Replay, Fremdverbindung, fehlender Kopf,
   Teilmenge oder zusätzliche Bindung sind REFUSE. Die vorhandenen Append-
   und Kapazitätsinvarianten dürfen dabei nicht umgangen werden.
6. Der Store prüft Record und Signatur vor COMMIT mit dem öffentlichen
   Audit-Verifier. Die SQL-Guards sichern Mengen-/Transaktionskonsistenz;
   sie behaupten keine bisher nicht vorhandene Ed25519-Prüfung durch
   PostgreSQL. Kryptografisch ungültige Daten bleiben zusätzlich im
   vollständigen Recovery-/Startabgleich gesperrt. Nach bestätigtem COMMIT
   wird ausschließlich der gerade gespeicherte Kopf verankert. UNKNOWN
   sperrt weitere Arbeit nach 4.9.2; es gibt keinen erneuten DELETE-Versuch
   vor der Reconciliation.

Die neue Eventform muss außerhalb der bisherigen `AuditEvent`-Versionen 1/2
geschlossen definiert werden: `D1_REQUEST_REFUSED` trägt Versuch-ID und
Ablehnungsbindungen ohne Job-/Handoff-Felder; das Bereinigungsprofil trägt
eine begrenzte Liste **genau** der Annahme-Event-Digests, Job-IDs,
`replay_key_sha256` und ursprünglichen `expires_at` samt geprüfter
Bereinigungszeit. Annahmeevents behalten ihre echten Job-/Handoff-Bindungen.
Falsche Formen und unbekannte Profilversionen sind REFUSE; kein generelles
Optionalmachen der bestehenden Pflichtfelder. Name und Schema des
Guardzustands, Routinen-DDL, Bereinigungs-Eventtyp, deren Parser und der
gemeinsame Sperr-/Operationsnachweis sind **Designreview-Gates vor DB-Code**,
keine mit dieser Skizze genehmigte Migration oder neue Runtime-Rechte.

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
| D1 am Proxy, je verifizierter Transport-Quelle | 30 Anfragen/min, nginx `burst=5 nodelay` | Erst nach erfolgreicher Ingress-Authentisierung, vor Core-Weiterleitung; auch unsignierte/ungültige D1-Versuche; weiterer Überschuss REFUSE, keine Warteschlange |
| Globales D1-Vorab-Budget im Kern | 60 Anfragen/min, Token-Kapazität 10 | Nach billigen Framing-/Größenprüfungen, vor JSON, Body-Digest und Signatur; auch ungültige Versuche; REFUSE |
| D1-Dienst-Bucket | 6 Anfragen/min, Token-Kapazität 2 | Unmittelbar nach erfolgreicher Signaturprüfung; auch spätere Replay-/Zeit-Ablehnungen verbrauchen das Token ohne Erstattung; REFUSE |
| D1-Verbindungen | höchstens 4 insgesamt über alle Core-Instanzen | Zusätzlich zur bestehenden Core-Verbindungsgrenze; keine Erhöhung durch weitere Instanzen |
| Gleichzeitig laufende D1-Jobs | höchstens 2 insgesamt, keine Warteschlange | Vor Reservation; belegt zusätzlich das bestehende Core-`max_in_flight`, dessen strengere Grenze immer gewinnt |
| Replay-Zeilen | Warnung ab 64, Annahmestopp bei 128 vorhandenen Zeilen | Gesamte gemeinsame Tabelle, einschließlich abgelaufener, noch nicht bereinigter Zeilen; kein 129. Insert |
| Bereinigung | höchstens 30 s zwischen erfolgreichen Läufen, höchstens 32 Zeilen je atomarem Lauf | Erst nach Quarantäne und nur nach 4.9.5; keine unlimitierte Aufholschleife |
| Portal, vor jedem Signierversuch | 10/min je serverseitig geprüfter Nutzerreferenz UND portalweit, Token-Kapazität jeweils 2; je 200 Versuche pro UTC-Tag | Einschließlich fehlgeschlagener Versuche, über Sessions/Instanzen/Neustarts; Ein-Nutzer-Vorschlag, nicht 10 Core-Annahmen/min |
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
Die Ingress-Authentisierung muss **vor** dem D1-Quellenlimit aus dieser Tabelle
wirken. Ein fremder Mandant derselben NAT-/Vercel-Adresse ohne gültigen
Ingress-Zugang darf weder dieses Quellenkontingent noch das Core-Vorab-Budget
verbrauchen. Allgemeine Transport-/Verbindungsgrenzen bleiben davon getrennt.
Kein erfolgreicher Test darf eine ungeprüfte Client-Headeradresse als Quelle
verwenden. Die tatsächliche Proxy-Phasenreihenfolge ist nach 4.9.7 nachzuweisen.

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
Auch ein dauerhafter Vorab-Budget-Write vor Signaturprüfung ist erst hinter
dem wirksamen geschützten Ingress zulässig; sonst würde unautorisierte Last
bereits Datenbankarbeit erzwingen. Gate 10 ist deshalb Voraussetzung, keine
nachträglich ergänzbare Optimierung.

Vor der Signaturprüfung einschließlich der ersten Zeitprüfung werden nur
Quell- und Vorab-Budget belastet. Signaturgeprüfte Replay-/Zeit-Ablehnungen
belasten gemäß Gate 8 den Dienst-Bucket, **keine Nutzerquote**. Bestehende
Core-Verbindungsgrenzen, Body-/Headerlimits, Policy- und Anker-Gates bleiben
wirksam. Dieser Entwurf erweitert keine Nutzerrechte.

**Begründung der Startzahlen (nur Vorschlag, keine Messung):** Proxy 30/min entspricht einer Anfrage je 2 s pro verifizierter Transportquelle; `burst=5 nodelay` statt Burst 10 vermindert sofortige Spitzen (nginx-Burst ist kein Kern-Token-Bucket). Das globale Vorab-Budget 60/min mit Kapazität 10 erlaubt bis zur doppelten Quellrate, ohne vor der Signaturprüfung 20 sofortige Token freizugeben. Der signaturgeprüfte Dienst-Bucket 6/min mit Kapazität 2 statt Kaans Ausgangswert 20/min und Burst 5 begrenzt die teure gemeinsame Audit-Kette für eine geschlossene Beta mit genau einer Person. Vier gleichzeitig verarbeitete D1-Verbindungen statt acht je Quelladresse decken zwei laufende Jobs mit geringem Reserveplatz ab. Zwei statt vier D1-Jobs lassen einen zweiten Auftrag zu, ohne `max_in_flight` auszuweiten. Diese Zahlen müssen unter realer Last nachjustiert und durch Kaan freigegeben werden.

**Portalbudgets:** Vor dem Signieren muss die Portal-Runtime atomar sowohl den
Zähler der *serverseitig verifizierten* Nutzerreferenz als auch einen gemeinsamen
Portal-Zähler prüfen und belasten. Speicher- und Zeitquellenvorschlag stehen
in §5.7; die Zahlen aus der Tabelle bleiben ungeprüft. 10/min liegt über dem
Dienst-Bucket von 6/min und schützt ihn nicht vor Erschöpfung durch den einen
zugelassenen Nutzer. Fehlgeschlagene Versuche verbrauchen ebenfalls dieses
**Signierversuchsbudget** und werden nicht erstattet. Dieses Budget ist von
der Job-Nutzerquote aus §5.4 getrennt; die Gate-8-Regel „keine Nutzerquote bei
Core-Ablehnung“ erstattet keine Signierversuche. Es gibt keine neu erzeugte
Kapazität pro Session, Neustart oder Instanz. Unbekannter Zähler-/Zeitstatus
bedeutet REFUSE. Ein zweiter Portal-Nutzer ist vor D3 weiterhin unzulässig.

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
Dieser Vorschlag beschreibt den bisherigen Vercel-Plan; den in §5.6 genannten
Konflikt zum vorhandenen privaten Portal entscheidet er nicht. Die gemeinsame
Anforderung „geschützter Ingress vor D1-Budget“ gilt unabhängig vom später
freigegebenen Betriebsort. Keine Netz-, Hosting- oder Secret-Änderung erfolgt.

Für die Ein-Nutzer-Beta bleibt ein **separater hochentropischer Ingress-Schlüssel**
in einem nur serverseitig gesetzten Transportheader, vorgeschlagen
`X-Genius-Ingress-Key`, der zu prüfende Zugangsvorschlag. Er ändert weder die
Hostingentscheidung noch den bestehenden Proxy. Der erste TLS-terminierende
Hop muss einen gültigen Schlüssel prüfen, **bevor** D1-Quellenkontingent oder
Core-Vorab-Budget belastet werden. Fehlender, falscher oder nicht eindeutig
prüfbarer Wert wird dort abgelehnt, ohne Core-Weiterleitung. Kein alternativer
Listener, Port, Pfad oder Rewrite darf diese Prüfung umgehen.

Für N5 wird nicht mehr ungeprüft eine rohe Duplikaterkennung durch Standard-
nginx vorausgesetzt. Nach erfolgreicher Prüfung muss der Proxy **sämtliche
ASCII-case-insensitiven Instanzen dieses einen Ingress-Headers entfernen**;
anderenfalls muss er die Anfrage vollständig ablehnen. Keine Kopie darf durch
HTTP/2-Übersetzung, Alias, Request-Trace oder Fehlerpfad zum Kern gelangen.
Kann eine eingesetzte Proxykonfiguration das nicht nachweislich garantieren,
bleibt dieser Zugang HOLD. Der Kern muss als zusätzlicher Leckdetektor jede
Anfrage mit einer verbliebenen Instanz dieses Headers auf der rohen Headerliste
vor Mapping oder Signaturarbeit verweigern; weder Erfolg noch Logging des
Werts ist erlaubt. Dieser neue Refusal ist vor Aktivierung separat zu testen.

**Vertragskonflikt explizit offen:** Der D1-Proxy-Vertrag in
[`PORTAL-CORE-D1-DRAFT.md`](PORTAL-CORE-D1-DRAFT.md) erhält D1-/Framing- und
Autoritätsheader. Die gezielte Entfernung ausschließlich dieses zusätzlichen,
bereits erfolgreich geprüften Ingress-Headers bleibt eine noch abzugleichende
Ausnahme. D1-Envelope, `Authorization`, Framing- und sonstige Autoritätsheader
werden weder entfernt noch zusammengeführt; ihre Duplikate verweigert der Kern
weiter anhand der rohen Liste. Der neue Header ist niemals D1-Signatur,
Core-Principal oder User-/Job-Autorität. Vertragsabgleich, unabhängiges Review
und Kaans Freigabe stehen aus: **Gate 10 HOLD**.

**Abnahme für N5/N6:** Ein echter isolierter Probeproxy muss für HTTP/1.1 und
HTTP/2 zeigen: fehlende/falsche Schlüssel sowie uneindeutige Darstellungen
werden vor D1-Budgetbuchung gestoppt; gültige Schlüssel gelangen einschließlich
Duplikat-/Casevarianten nie zum Core; Envelope-/Authorization-Duplikate bleiben
erhalten und werden dort verweigert. Ein direkter Request mit verbliebenem
Ingress-Header scheitert am Core. Bei gleicher Transportquelle dürfen viele
unberechtigte Versuche das D1-Kontingent einer nachfolgenden berechtigten
Anfrage nicht aufbrauchen. „Kein Core-Request“ und unveränderte Budgetzähler
sind getrennt nachzuweisen. Eine bloße nginx-Konfigurationslektüre oder
Annahme über die Reihenfolge seiner Phasen genügt nicht.

Schlüssel nur in getrennten Secret-Stores für Vercel-Backend und Proxy, niemals Browser, Repository, Fehlerantwort, Request-Log oder Trace; keine Ableitung aus Core-Root-Secret oder D1-Signierschlüssel. Rotation, Proxy, TLS, Firewall und Deployment bleiben unverändert und benötigen eigene Freigabe. **Betriebsnachweis:** tatsächliche Vercel-Runtime, nginx/TLS-/HTTP2-Übersetzung, Duplikat-/Mischheader, ungültige/fehlende Schlüssel, Bypass-Ports und Lastbudget müssen reproduzierbar geprüft werden. Ein korrekter Ingress-Schlüssel umgeht niemals Signatur-, Zeit-, Replay-, Policy-, Audit- oder Quotenprüfungen. mTLS ist eine mögliche spätere Alternative, hier nicht eingerichtet.

## 5. Portal-Schema

Portal-Tabellen sind keine Autorität für Core-Policy oder Worker-Rechte.

**Sperre vor Implementierung:** Die SQL-Vorlagen in §5.1–5.5 sind ein älterer
Entwurfsstand und werden durch den HOLD-Vertrag in §5.6 in Bezug auf
Identität, Sessions und Migrationen überholt. **Nicht als Migration 0005/0006
übernehmen**, bis die D3-/Restore-Gates geschlossen und das Schema explizit
neu geprüft sind. Das bestehende lokale Portal-Schema ist zusätzlich ein
separater Istzustand und darf keinesfalls still migriert werden.

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


### 5.6 Portal-D2/D3-Wiring und Eigentumsgrenzen (Vorschlag, HOLD)

**Status: Doku-Entwurf, keine Migrations-/Runtime-Freigabe.** Das bereits
akzeptierte dunkelblaue GeniusNew-Portal ist eine UI-Vorlage für **ein**
bestehendes privates Portal, kein eigenständiges Produktivsystem. Der
Prototyp liefert Mock-Daten. Echte Statuswerte dürfen nur über den
authentisierten, serverseitigen Datenprovider dieses Portals kommen;
bis dahin bleiben sie UNKNOWN. Öffentliche Design-Vorschauen erhalten
keine Portal-DB-DSN, Core-URL oder Signier- und Auth-Schlüssel; dort sind
nur klar als MOCK/UNKNOWN gekennzeichnete Beispieldaten erlaubt.

**Read-only-Bestandsaufnahme 19.10.2026:** Auf dem Server wurde ein vorhandenes,
separat betriebenes Python-/Gunicorn-Portal auf Loopback mit einer
PostgreSQL-Verbindung beobachtet. Seine vorhandene SQL-Quelldefinition
enthält acht Tabellen (users, sessions, jobs, approvals, api_keys, quotas,
login_attempts, audit_events) und weicht deutlich von §5.1–5.5 ab.
Die tatsächlich angewandten Migrationen und der Schema-/Datenzustand
wurden **nicht** durch DB-Abfragen verifiziert (UNKNOWN). Das Portal ist
nicht mit dem signierten GeniusNew-Core- oder externen Ankerpfad verbunden.
Weder die Live-DB noch ihre Zugangsdaten werden in diesem PR berührt.
Vor jedem DB-Code ist die Migration vom Ist- zum Zielmodell getrennt zu
entwerfen und gegen eine Wegwerfkopie zu testen; **kein CREATE/ALTER am Server**.

**Wichtiger Konflikt mit 5.1–5.5:** Die vorhandenen Tabellen verwenden
für Portal-Konten den Schlüssel user_id und sogar subject. Der **selbst noch auf HOLD stehende**
D3-Entwurf vom 18.10.2026 nennt dagegen eine eigene, zufällige und
unveränderliche portal_user_ref aus 16 Bytes (= 32 lowercase Hex-Zeichen).
Diese Referenz ist weder eine Core-Personen-ID noch ein Core-Subject.
Die im D3-Entwurf beschriebene Bestätigung durch Kaan ersetzt keinen
noch ausstehenden Eintrag in DECISIONS.md und keinen Security-Review.
Die SQL-Vorlagen 5.1–5.5 sind daher noch **nicht** unmittelbar als
Migration 0005/0006 zu implementieren. Vor DB-Code sind sie nach
Security-Review entsprechend zu überarbeiten. Kein stilles Umbenennen
bereits angewandter Migrationen (derzeit sind Portal-Migrationen nicht
als auf main implementiert belegt).

**Vorgeschlagener relationaler Schlüsselwechsel (Review-Gate):**

- Portal users: portal_user_ref als 128-Bit-CSPRNG-Schlüssel, einmalig,
  unveränderlich, nie einer zweiten Person zuweisbar. Deaktivierte
  Konten bleiben als Tombstones; E-Mail ist nur Login-/UX-Attribut,
  niemals Identitätsbeweis für den Core.
- Sessions: SHA-256-Digest des 32-Byte-Tokens als einziger Schlüssel,
  Fremdschlüssel auf portal_user_ref, Ablauf und Sperrzeit; niemals
  das rohe Token, Cookie oder ein CSRF-Secret speichern. Login und
  Tokenrotation in einer Transaktion. Gesperrte/abgelaufene Sessions,
  DB-Ausfall und unsichere Zeit sind REFUSE.
- Rollen: weiterhin USER/APPROVER/ADMIN als reine Portal-UI-Rollen,
  keine Autorität für Core-Policy, C3-Approvals oder Job-Eigentum.
- Quoten und Nutzung: Schlüssel portal_user_ref; HOUR/MONTH und separater
  Dienst-Bucket werden in fester Reihenfolge in **einer** Transaktion
  belastet. Pro Fenster atomarer Upsert mit bedingter Erhöhung und
  RETURNING; null Zeilen bedeutet REFUSE. Nach Belastung und Versuch
  gibt es auch bei Core-REFUSED/UNKNOWN keine stille Erstattung.
  Core-Personen-Quoten bleiben **autoritativ** über Neueinladungen und
  Portalreferenz-Wechsel hinweg. Das Pending-Limit aus §5.4 benötigt
  einen eigenen synchronisierten Zähler/registrierte Core-Abfrage:
  bis dahin kein positives Pending-Limit-Versprechen. Beträge,
  Zeitquelle, Dienstbudget und Recovery-Grenze aus #161 sind offen;
  bei unbekanntem Budget wird keine Anfrage signiert.
- Portal-History: (portal_user_ref, job_id) nur nichtautoritativer
  Anzeige-/UX-Cache. Vor dem Netzwerkaufruf wird eine dauerhafte
  Submission-Zeile (neuer, getrennt zu reviewender Entwurf) mit
  zufälliger submission_id, Alias, Envelope-Digest und einem strikt
  validierten Zustand SUBMITTING/ACCEPTED/REFUSED/UNKNOWN angelegt.
  Ein Crash nach Core-COMMIT kann dadurch als UNKNOWN angezeigt und
  später nur durch eine neue signierte Core-Leseoperation abgeglichen
  werden; **keine erneute Core-Joberzeugung**. last_status benötigt
  ebenfalls einen geschlossenen Zustands-Check. Fremde Aufträge
  antworten einheitlich 404; der Browser darf Job-Eigentum oder
  Status nicht selbst festlegen.

**Identitäts- und Datenfluss:**

~~~text
Browser (untrusted)
  -> privates GeniusNew Portal Backend
    -> eigene Portal-PostgreSQL (Sessions/Konten/UX, Rolle genius_portal)
    -> signierte versionierte Portal->Core HTTP-Operation (D1/v2 HOLD)
      -> Core-seitig verwaltete Personenbindung und Policy
      -> persistenter Ledger, Audit-Kette und separater Anker
~~~

Der Core verwaltet die Bindung des Tupels
(portal_service_principal_id, portal_user_ref) an den Core-Subject
und die kanonische Personen-ID. Die Portal-Datenbank darf diese
Core-Registry nicht duplizieren oder als Autorität ersetzen.
Ein deaktivierter oder mehrfach gemappter Alias: REFUSE. Die
Portal-Signatur stellt die Dienstidentität sicher, nicht die
persönliche Approver-Identität. Ein zweiter Benutzer und Freigaben
bleiben ohne D3-v2-Gate gesperrt. D1s begrenzte Ein-Nutzer-Ausnahme
bleibt auf risikoarme Tools ohne Approvals beschränkt.

**Transaktions- und Recovery-Gates vor Code:**

1. Login: Argon2id aus PR #151, Token/CSRF aus PR #152; Session
   wird nur bei ACTIVE-Konto, gültigem Digest und Zeitfenster
   akzeptiert. Stateful Login-Drosselung gegen unbekannte Adressen
   wie gegen falsche Passwörter; nicht prozesslokal. Alle mutierenden
   Aktionen inkl. Login/Logout benötigen Origin-Prüfung und einen
   serverseitig HMAC-gebundenen CSRF-Header; der HMAC bindet die
   aktive Session und wird nach Rotation ungültig. __Host-Cookie,
   Secure, HttpOnly, Host-only, Path=/; für die geschlossene Beta
   SameSite=Strict **vorgeschlagen** (PR #152 beschreibt Lax;
   Profilentscheidung und Test noch ausstehend). Keine Mutation
   per GET und kein automatisches Demo-Login.
2. Kontosperrung wird erst nach **bestätigter Core-Registry-Sperre**
   endgültig, danach Portal-Konto DISABLED und Sessions atomar
   widerrufen. Ist die Core-Sperre unbestätigt, meldet das System
   PENDING/HOLD, niemals erfolgreichen Abschluss. Ein Job-Request
   hält das Portal-Konto nur während Session-/Quote-/Signaturprüfung
   (transaktionslokale Row-Sperre), **niemals während des HTTP-Aufrufs**.
   Bei Core-Annahme wird die aktuelle gesperrte Registry erneut geprüft.
   DISABLED ist terminal; DB-Test/Trigger verweigert DISABLED->ACTIVE.
   Alte portal_user_ref bleibt unveränderlich und wird nie recycelt.
3. Job: sichere Session -> in einer Transaktion persistente Quoten
   plus Submission-/Envelope-Digest -> Portal-Signatur über den nur
   serverseitig ermittelten Alias -> Core-Signatur-/Replay-/Policy-/
   Registry-Prüfung -> atomare Core-Annahme/Audit -> erst nach
   bestätigtem Ergebnis UX-Caching. Ungewisser COMMIT-Ausgang oder
   Netzverlust bleibt in der Submission-Zeile UNKNOWN/HOLD und wird
   nur durch signierten Core-Status abgeglichen, **niemals still neu
   signiert/abgesendet**.
4. Eine Core-Jobsignatur autorisiert nicht automatisch History,
   Approval oder API-Key-Verwaltung. Jede dieser Operationen
   braucht einen gesondert versionierten Vertrag und Test.
5. Portal-Laufzeit ohne Core-DB-DSN, Root-/Anker-Secret, DDL-,
   TRIGGER-, TRUNCATE-, Owner- oder Superuser-Recht. Die separate
   Portal-Migrationsrolle besitzt ausschließlich Portal-DDL.
   Die Runtime-Rolle erhält DML nur je tatsächlich benötigter
   Portal-Tabelle/Spalte; kein DELETE auf users und kein unkontrolliertes
   Rollen-/Statusupdate. Server-Konfiguration ist die einzige
   Quelle von Secrets; nie Browser-Bundle/Repo.
6. PostgreSQL-Integration nur in isolierter Wegwerf-DB:
   Sessionablauf, Rotation, Logout, parallele Sperre und Core-Block,
   fremdes Job-404, Budget-Race, Replay, DB-Ausfall, Restore und
   unbekannter COMMIT. Nach Restore werden **alle alten Sessions
   unabhängig vom wiederhergestellten DB-Inhalt ungültig**; Core-
   Sperrstatus und extern verankerte Sicherheitszustände gewinnen.
   Unbekanntes Budget: REFUSE. Verfahren für einen externen
   Session-Epochenanker/Rotation und Core-Reconciliation ist **HOLD**.
   Rote Negativtests zuerst, dann CI/Refusal-Guard, unabhängiges
   Claude DB Review am exakten SHA, Kaan-Merge.
7. OFFENE ARCHITEKTURFRAGEN: §2.2 und die bisherigen
   DECISIONS.md-Einträge sehen Vercel vor. Am 19.10.2026 beschrieb
   Kaan die neue UI-Vorschau als spätere Einbindung in ein einzelnes
   privates selbst gehostetes Portal. Dieser noch nicht nach
   DECISIONS.md übertragene Betriebsort-Widerspruch ist ausdrücklich
   **HOLD**; bis zu Kaans dokumentierter Entscheidung kein Wechsel
   von Host, TLS, Pooler oder Zugang. Bei Vercel nur transaktionslokale
   Locks und TLS-DB verify-full, bei gemeinsamem Host getrennte
   OS-Nutzer und separate DB-Cluster/Secrets als zu prüfende
   Anforderungen. Weiter offen: Einladungs-/E-Mail-Kanonisierung
   (Wiederaufnahme darf an unique Email nicht scheitern),
   Token-Formate und Session-Epochen, Quoten-/Zeitanker, Retention,
   administrative Core-Registry-Sperr- und Restore-Transaktionen.
8. Core-seitiger, eigener Datenbankentwurf (§4.10 als Folgethema)
   für kanonische Personen-ID, unveränderliche gesperrte Aliasse,
   getrennte D3/v2-Replay-Domäne und Schlüsselrotation ist vor
   D3-Code verpflichtend. §4.9 ist ausschließlich für D1 bestimmt
   und darf nicht still als v2-Tabelle umgedeutet werden.


### 5.7 Portal-Signierversuchsbudget (N3, Vorschlag zur Prüfung)

Dieses Budget liegt ausschließlich in der **separaten Portal-Datenbank** und
begrenzt Signierversuche, nicht Jobs und keine Core-Personenquote. Folgende
Arbeitsnamen konkretisieren den noch freizugebenden Migrationsvertrag; sie
sind kein bereits installiertes Schema und reservieren keine Migrationsnummer:

| Vorgeschlagene Tabelle | Gebundener Zustand |
| --- | --- |
| `portal_signing_budget_state` | Genau ein portalweiter Bucket und je eingeladener, serverseitig geprüfter `portal_user_ref` ein Bucket; Resttokens, letzte geprüfte Auffüllzeit, UTC-Tag und Tagesverbrauch |
| `portal_budget_clock_state` | Vorinitialisierte Singleton-Zeile mit höchster bestätigter Portal-DB-UTC; kein Client-Zeitwert und keine Core-DB-Verbindung |

Einladungs-/Migrationspfad legt zulässige Budgetidentitäten an; Requests dürfen
weder aus Browserwerten neue Buckets erzeugen noch den Portal-Bucket durch
neue Sessions, Schlüssel oder Instanzen vervielfachen. Die Runtime erhält
nur die konkret benötigten DML-Rechte unter Guards, keine DDL-/Owner-Rechte
und keine Rücksetzung von Zeit oder Verbrauch. Ein Bucket ist kein Datensatz
je Versuch: der Platz ist durch eingeladene Referenzen begrenzt; gesperrte
Referenzen werden nicht zu neuen Nutzern umgewidmet. Das genaue DDL und
atomare Verfahren bleiben vor DB-Code zu reviewen.

**Vorgeschlagene Zeitquelle:** frisches PostgreSQL-`clock_timestamp()` auf dem
autoritativen Portal-Primary, nach Erwerb der gemeinsamen Uhr-/Budgetlocks.
Der UTC-Kalendertag wird daraus serverseitig abgeleitet, nicht aus Vercel-
Instanzzeit, Session oder Request. Vergleich und Fortschreiben der gespeicherten
Höchstzeit, Auffüllen und Verbrauch beider Buckets sowie Tageszähler erfolgen
in einer kurzen Transaktion mit fester Lockreihenfolge: Uhrzeile, portalweiter
Bucket, Nutzerbucket. Ein Rücksprung unter die gespeicherte Höchstzeit,
unbekannte Zeit, fehlende Zeile oder unbestätigter COMMIT verweigert das
Signieren; kein lokaler Fallback. Beide Buckets müssen gemeinsam erfolgreich
belastet sein, bevor ein Signierversuch beginnt. Kein Lock bleibt während des
Core-HTTP-Aufrufs offen und keine spätere Ablehnung erstattet diese Buchung.

Die DB-Zeit ist **eine vorgeschlagene betriebliche Vertrauensgrenze**, kein
unabhängiger Zeitnachweis. NTP-/Provider-Zeitstatus, zulässige Vorwärtssprünge,
Budget-Auffüllung und Tageswechsel sind mit dem tatsächlichen Betriebsort zu
prüfen. Ein Restore derselben DB kann sowohl Zähler als auch Höchstzeit
zurücksetzen; daher ist dieser Vorschlag ausdrücklich keine Restore-Lösung.
Bei unbekanntem Restore-/Failover-/Budgetstand keine neue Signatur, bis der
noch separat freizugebende Reconciliation-/Epoch-Vertrag den Zustand bestätigt.
Hier wird weder eine neue Session-Epoche gewählt noch ein Rücksetzen auf volle
Tokens zugelassen. Lastzahlen aus 4.9.6 bleiben ebenfalls Vorschläge.

Ein kompromittiertes Portal-Backend hält seinen Signierschlüssel und könnte
dieses eigene Budget umgehen. Die unabhängige Schutzgrenze des Kerns bleiben
Ingress, Vorab-/Dienst-Bucket, Replay, Policy, Audit und Anker. Der Vorschlag
verspricht weder Schutz des Core-Buckets vor dem einzigen Nutzer noch neue
Core-Rechte durch eine Portal-Buchung.

Abnahme vor Aktivierung: parallele Signierversuche über zwei Portal-Instanzen
und mehrere Sessions überschreiten weder Nutzer- noch Portalbudget; ein
einseitig fehlgeschlagener Verbrauch rollt beide Budgetmutationen zurück,
nach bestätigter Buchung bleibt der Versuch trotz Signierfehler verbraucht.
Neustart, Tageswechsel, Uhrfehler, DB-Ausfall, unbekannter COMMIT und Restore
dürfen keine zusätzliche Kapazität erzeugen. Schema/Migration, Zeitprofil,
Reconciliation und Tests benötigen den unabhängigen Entwurfsreview; DB-Code
danach `Claude DB Review: APPROVED` am exakten Head. **N3 bleibt bis dahin HOLD.**

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

**HOLD für 0005/0006:** §5.1–5.5 beschreiben ein veraltetes Schema,
§5.6 den noch zu reviewenden D3-Identitätsvertrag. Die existierende
Portal-PostgreSQL-Installation hat ein abweichendes Schema; weder
bestehende Daten noch Migrationen dürfen ohne eigenständige
Bestands-/Rollback-/Restore-Prüfung überschrieben werden.
Einladung, Konto-Sperrlebenszyklus, Submission/UNKNOWN und Kosten-
Budget gehören in den freizugebenden neuen Portal-Migrationsvertrag.
Dieser umfasst auch die vorgeschlagenen getrennten Signierversuchs-/Uhrtabellen
aus §5.7; `quota_usage` ist dafür kein Ersatz. Keine der noch offenen
Portal-Migrationsnummern oder -versionen wird durch diesen Entwurf festgelegt.

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

**Portal-Rechte-Gate (vor Implementierung):** Ein eigenständiger
Portal-Migrationseigentümer besitzt DDL nur für das separate Portal-Schema.
Die Laufzeitrolle `genius_portal` hat keinen Superuser-, Owner-,
TRUNCATE-, CREATE-, REFERENCES- oder Core-DB-Zugang. SELECT und eng
begrenzte INSERT/UPDATE-Rechte werden je Portal-Tabelle begründet;
users sind für die Runtime nicht löschbar und Kernidentität bleibt
außerhalb der Portal-DB. Rollen-/Statuswechsel benötigen geprüfte
Administration statt freiem UPDATE. Diese Minimalbeschreibung ist
noch **keine** überprüfte produktive GRANT-Matrix (HOLD).

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
7. Portal-Identität und Sessions erst nach §5.6-D3-Baseline,
   Schema-/Migrationsreview, sicherer Session-Epoche/Restore-Regel;
8. Portal erst nach signierter Core-Identitätsanbindung und
   Ende-zu-Ende-Prüfung der unter D1/D3 getrennten Autorität.
