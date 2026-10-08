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

Dieser Zusatz konkretisiert nur den Speicher für den Entwurf
[`PORTAL-CORE-D1-DRAFT.md`](PORTAL-CORE-D1-DRAFT.md). Er aktiviert D1 nicht und
braucht vor DB-Code den vorgesehenen unabhängigen Security-Review und Kaans
Freigabe. Die Tabelle gehört ausschließlich zur Core-Datenbank; Portal, Browser
und Portal-Runtime-Rolle haben keinen direkten Zugriff.

```sql
create table d1_replay_reservations (
    domain text collate "C" not null
        check (domain = 'geniusnew.portal-core.request.d1'),
    issuer text collate "C" not null
        check (issuer ~ '^[A-Za-z0-9._-]{1,64}$'),
    nonce bytea not null check (octet_length(nonce) = 32),
    expires_at bigint not null
        check (expires_at > 0 and expires_at < 4102444800),
    primary key (domain, issuer, nonce)
);
```

`nonce` ist die exakte 32-Byte-Dekodierung der 64 lowercase Hex-Zeichen aus dem
signierten D1-Envelope. `domain` bleibt über Schlüsselrotation und Deployments
stabil; eine neue `key_id` ändert den Unique Key nicht. `expires_at` ist ein
Nachweisfeld, **kein** Löschtermin. D1 hat weder automatische Bereinigung noch
automatische Wiederfreigabe nach Ablauf, Uhr-Rücksprung oder Schlüsselrotation.
Ein voller oder nicht erreichbarer Speicher verweigert neue D1-Aufträge.

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
Default-Privileges und Grants sind beim Start
zu prüfen. Nur die getrennte Migrations-/Betriebsrolle darf Schemaänderungen
vornehmen; D1 sieht keinen
automatischen History-Löschpfad vor.

Alle D1-Zugriffe benennen Core-Schema und Tabelle vollständig. Der D1-DB-Zugang
hat einen festgelegten sicheren `search_path` und kein `TEMP`-Recht, sodass
Namensauflösung nicht auf eine andere Tabelle oder Funktion ausweichen kann.

Alle Core-Instanzen, die dasselbe `(issuer, audience)` akzeptieren, müssen
dieselbe autoritative, transaktionale Replay-Tabelle auf demselben schreibbaren
Primary benutzen. Getrennte schreibbare Kopien, Split-Brain und Failover auf
einen Stand ohne bereits bestätigte Reservationen sperren D1. Bei unbekanntem
Replikations- oder Failover-Stand wird die Annahme verweigert.
`audience` ist absichtlich nicht Teil des Unique Keys: Ein Nonce desselben
`issuer` bleibt auch bei Wechsel des Core-Ziels verbraucht.

Die Reservation ist ein einzelner transaktionaler Insert gegen den Primary Key:
`INSERT ... ON CONFLICT (domain, issuer, nonce) DO NOTHING RETURNING domain,
issuer, nonce`. Nur genau eine zurückgegebene Zeile, deren drei Werte exakt mit
den Insert-Werten übereinstimmen, ist ein Gewinnernachweis. Null Zeilen,
abweichende Rückgabe oder jeder SQL-/Serialisierungsfehler sind REFUSE ohne
automatischen Retry; ein COMMIT ohne diesen Nachweis erzeugt keinen Job. Die
Replay-Transaktion setzt `SET LOCAL synchronous_commit TO on` nach `BEGIN` und
prüft den wirksamen Wert unmittelbar vor `COMMIT`; dazwischen darf kein Befehl
oder Savepoint-Rollback ihn ändern. `fsync=on` und `full_page_writes=on` sind
beim Start und vor jeder Reservation als wirksame Serverwerte zu prüfen.
Kann eine Prüfung oder die unveränderte Betriebs-Konfiguration bis zum COMMIT
nicht sichergestellt werden, sperrt D1. Erst ein bestätigter synchroner COMMIT
auf dauerhaftem WAL erlaubt die Fortsetzung. Ein unbekannter Commit-Ausgang
wird nicht automatisch wiederholt. Nach bestätigtem Insert bleibt der Nonce
auch bei Crash vor Job-Erzeugung verbraucht.

**Start, Backup und Restore — HOLD:** Vor D1-Listener-Freigabe müssen Tabelle,
Migration, nicht deferrable Primary Key, Zeilenform, effektive Rechte beider
Runtime-Rollen sowie die Abwesenheit von Insert-umschreibenden Triggern, Rules,
RLS-Policies und nicht freigegebenen Partitionen/Kindtabellen geprüft werden.
Der Nachweis umfasst die tatsächliche Insert-Zieltabelle; unklare Katalog- oder
Schemawerte sperren D1. Eine fehlende, leere Ersatz- oder beschädigte Tabelle darf nicht
automatisch initialisiert werden. Ein älteres Backup kann alle späteren
Reservationen verlieren; weder Primary Key noch die bestehende Audit-/Anchor-
Prüfung belegen dann die Vollständigkeit der Replay-Historie. Ein von der
Core-Datenbank unabhängiger, kryptografisch prüfbarer Replay-Fortschrittsnachweis
mit definiertem Backup-/Restore-Abgleich ist ein **separates Security-Gate**.
Bis dessen Vertrag freigegeben und getestet ist, darf D1 nach einem Restore
nicht als sicher gestartet werden. Kein stilles Zurücksetzen, keine automatische
Neuinitialisierung, keine History-Löschung und kein Anchor-Reset. Der bestehende
Audit-Anker darf nicht ohne eigenen geprüften Vertrag zum Replay-Anker erklärt
werden.

Die D1-Migration ist **nie** Teil des automatischen Migrationslaufs vor
Dienststart. Eine erstmalige Installation oder spätere Migration braucht einen
getrennt freigegebenen Ablauf, der vorhandene
Replay-Historie erhält beziehungsweise deren Fehlen unabhängig belegt; bei
ungewissem Vorzustand bleibt D1 gesperrt.

**Architektur-Gate:** Der Beschluss vom 30.09.2026 in
[`DECISIONS.md`](DECISIONS.md) verwirft einen allgemeinen Schema-Fingerprint.
Ob die hier verlangte D1-spezifische Startprüfung von Primary Key, Zeilenform und
Rechten damit vereinbar ist, muss Kaan vor aktivem D1-Code ausdrücklich klären.
Bis dahin ist diese Prüfung eine Entwurfsanforderung, keine stillschweigende
Änderung des bestehenden Beschlusses.

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

Für einen späteren aktiven D1-Pfad kommt vor Schritt 10 die Prüfung aus 4.9
hinzu, einschließlich des unabhängigen Replay-Rollback-Nachweises. Ohne diesen
Nachweis bleibt D1 gesperrt; die bestehende Core-Startprüfung wird nicht
abgeschwächt.

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

Eine spätere, separat freizugebende D1-Migration legt die Tabelle aus 4.9 an.
Sie erhält eine neue Version nach den bestehenden Migrationen, ist aber vom
automatischen Lauf ausgenommen. D1-Code darf nicht vorher aktiviert und die
Tabelle nicht beim Dienststart ad hoc erzeugt werden. Es gilt das Restore- und
Installations-Gate aus 4.9.

Migrationen laufen vor Dienststart, einzeln in Transaktionen. Kein automatisches
„drop and recreate“ bei Fehlern.

## 10. Secrets und DB-Rollen

Mindestens getrennte Rollen:

- **Migrationsrolle:** Eigentümerin der Core-Tabellen und einzige Rolle mit DDL,
  `TRIGGER`, `TRUNCATE` oder `REFERENCES`;
- **`genius_core`:** Runtime, ausdrücklich **nicht** Eigentümerin der Tabellen und ohne
  DDL/`TRIGGER`/`TRUNCATE`/`REFERENCES`;
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

## 12. Audit-Wachstum — Entwurfszusatz, nicht freigegeben

**Auftrag:** Kaan, 08.10.2026; ChatGPT, Branch `chatgpt/db-audit-growth`.
**Geprüfte Baseline:** `main` `7f81e240f6ec4b16d876f081249afb3d47240901`.
Dieser Zusatz entwirft die Ablösung des Vollsnapshot-Protokolls aus §§4.6, 4.7,
6 und 8. Er ist **keine Implementierungs-, Migrations- oder Betriebsfreigabe**.
Bis zu den Gates in §12.11 bleiben die bisherigen Verträge und Grenzen gültig.
§4.9/D1, Governance und `SECURITY.md` werden hier weder geändert noch freigegeben.

### 12.1 Befund, Ziel und ausdrücklich verbleibende Grenze

Am genannten SHA liest `PostgresAuditChain._append` nach dem
`pg_advisory_xact_lock` über `_read` sämtliche Records und Köpfe, einschließlich
`count`, `sum(octet_length(event))` und `max(octet_length(event))`.
`_AnchoredAudit._commit_locked` lädt anschließend über `snapshot` nochmals die
Geschichte. `_commit_request` serialisiert sie vollständig. Die Kosten eines
Appends wachsen damit mit der bisherigen Geschichte; nur `_append` zu ändern
würde den zweiten Vollscan und den vollen Transport nicht beseitigen.

Die Größen sind getrennt zu betrachten: `_MAX_STORED_RECORDS` ergibt mit der
270-Byte-Minimal-Kopfzeile `16777216 // 270 = 62137`. Der Preflight
`_check_commit_size` verwendet jedoch `head.count * len(_head_line(head))`:
bei fünfstelliger Anzahl ist eine v2-Kopfzeile 274 Bytes lang. Dadurch passen
höchstens **61230** Records in diese konservative Zustandsrechnung
(`61230 * 274 = 16777020`; `61231 * 274 = 16777294 > 16777216`). Das ist eine
lokale Nachrechnung der gelesenen Kanonform, **kein Lasttest**. Das 64-MiB-Limit
für die komplette Socket-Anfrage einschließlich Nonce-Envelope kann je nach
Eventgröße früher greifen. `_MAX_COUNT` in `audit_chain.py` begrenzt zusätzlich
die bisherige materialisierte Kette auf höchstens eine Million Records.

**Ziel:** konstante Anzahl gelesener Nutzzeilen pro normalem Append, begrenzte
Delta-Frames und begrenzter aktiver Ankerzustand. Indexzugriffe sind nicht als
konstante Laufzeit garantiert. Gesamtarchiv und dauerhafte Replay-Indizes wachsen
weiter; endlicher Speicher kann keine unbegrenzte Historie aufnehmen.

**Sicherheits-Gate:** Ein Tip-only-Append erkennt eine nach der Startprüfung
vorgenommene Manipulation eines alten, nicht gelesenen Records oder Kopfes nicht
sofort. Ein korrekt fortgesetzter Kopf beweist nicht die fortdauernde Verfügbarkeit
aller historischen Bytes. Die Vollprüfung erkennt das weiterhin beim nächsten
Start bzw. einer ausdrücklich ausgeführten vollständigen Integritätsprüfung.
Das ist nicht dieselbe Erkennungsfrist wie beim heutigen Vollscan vor jedem
Append. Ohne Kaans ausdrückliche Entscheidung, unabhängige Reviews und eine
begleitende Änderung der betroffenen `SECURITY.md`-Grenze darf der schnelle Pfad
nicht aktiviert werden. Rechte werden nicht erweitert; ein Advisory Lock ist
insbesondere kein Schutz gegen einen DB-Eigentümer, der ihn ignoriert.

### 12.2 Inkrementeller Append und instanzübergreifende Ordnung

Vorgeschlagener Normalpfad für eine bereits vollständig geprüfte Installation:

1. `_AnchoredAudit` hält weiterhin den Prozess-/Connection-Lock und den bestehenden
   **Session-Lock** `anchor_lock()` mit `_AUDIT_LOCK` über DB-Commit bis zur
   Ankerbestätigung. Alle Core-Instanzen benutzen dieselbe DB und denselben Anker.
   Der zusätzliche `pg_advisory_xact_lock(_AUDIT_LOCK)` bleibt in `_append`.
   Reihenfolge: Session-Lock, DB-Transaktion, Xact-Lock, Fachzeilen-Locks.
   Kein Netzwerk-Ack in einer offenen DB-Transaktion; kein Worker unter diesen Locks.
2. Nach **jeder** Übernahme des Session-Locks den Anker mit frischer C2-Nonce nach
   seinem Zustand fragen und mit dem DB-Tip abgleichen. `_needs_reanchor` ist nur
   ein lokaler Hinweis, kein instanzübergreifender Nachweis. Ein fremder Crash kann
   sonst einen unverankerten Suffix hinterlassen. Diesen vor neuer Mutation nach
   §12.8 abarbeiten oder ablehnen; keine neue Arbeit am ungeklärten Suffix vorbei.
3. Innerhalb des Xact-Locks nur den letzten Record und letzten gespeicherten Kopf
   lesen: im bisherigen Layout je `ORDER BY index DESC LIMIT 1` bzw.
   `ORDER BY count DESC LIMIT 1`, im Epochenlayout zusätzlich exakt die aktive
   `(chain_id, epoch)` einschränken. Höchstens eine feste Anzahl kleiner
   Epochen-Metadatenzeilen kommt hinzu. Keine Vollaggregate, kein `OFFSET`, kein
   `snapshot`, keine Historienmaterialisierung in diesem Pfad.
4. Die Reads müssen einen nach Erwerb der Sperre gültigen Zustand sehen: für den
   Schreibpfad `READ COMMITTED` mit getrenntem Lock-Statement und nachfolgenden
   Reads; kein vor dem Warten erzeugter `REPEATABLE READ`-Snapshot. Timeout,
   Deadlock, Verbindungs-/Lock-Verlust oder unklarer Commit-Ausgang sind Refusal,
   nicht Anlass für einen verdeckten Wiederholungsversuch der Fachmutation.
5. Tip-Event byte-genau kanonisch rehydrieren, Größe und Record-Hash neu prüfen;
   Kopf mit öffentlichem Audit-Schlüssel prüfen. Index/Count, Epoch-Bindung,
   Kopf-Hash und `created_at == event.occurred_at` müssen zusammenpassen. Ein
   leerer Record-Store bei vorhandenem Kopf oder umgekehrt ist Fehler. Beide leer
   sind nur beim ausdrücklich initialisierten Genesis-Zustand zulässig, nicht als
   Deutung eines verschwundenen Stores. Der bestätigte Anker darf nicht voraus sein.
6. Neuen Record aus genau diesem Tip bilden. Event-/Frame-Grenzen und verbleibendes
   Epochen-/Journalbudget **vor** dem Commit prüfen. Record und bereits signierten
   Kopf mit der B6-Fachmutation atomar speichern. Zähler/Bytebudgets werden unter
   derselben Sperre fortgeschrieben und beim Start vollständig nachgerechnet;
   sie sind keine Ersatzbeweise für Integrität. Fehler rollt die ganze Mutation zurück.
7. Nach bestätigtem DB-Commit nur die neu committed Records und ihren gespeicherten
   Endkopf an den Anker senden. Keine neue Signatur und kein erneuter Vollsnapshot.
   Nur ein authentisiertes, exakt zum Zielzustand passendes Ack erlaubt Fortsetzung.

Der letzte Record und Kopf werden bei jedem Append aus PostgreSQL gelesen, nicht
nur aus einem Prozesscache. Runtime-Rechte auf Historie bleiben append-only;
Constraints, Trigger, Byte-Limits und die B6-Atomarität werden nicht abgeschwächt.
Die Änderung umfasst später auch `head()`, Recovery und `_commit_locked`, soweit
sie heute den versteckten Vollscan auf dem normalen Commitpfad auslösen.

### 12.3 Delta-Vertrag an der Prozessgrenze

**Vorgeschlagene, noch nicht implementierte Version:**
`geniusnew-anchor-delta-v1`. Die Transportgrenze und Rollen bleiben erhalten.
Der Anker bekommt nur den öffentlichen Audit-Schlüssel; ausschließlich die
Audit-Rolle signiert Köpfe/Checkpoints. Der separate C2-Antwortschlüssel bleibt
beim Anker. Kein HMAC, kein gemeinsam verwendeter privater Schlüssel, keine neue
Abhängigkeit und kein automatischer Fallback auf das Vollsnapshot-Protokoll.

Dieser neue Vertrag zielt auf den separat betriebenen C2-Ankerdienst. Der alte
Kindprozess-/Pipe-Modus bleibt unverändert Legacy und ist kein Ersatzpfad bei
fehlenden C2-Schlüsseln; seine Umstellung braucht einen gesondert geprüften
Vertrag. Keine Antwort ohne Nonce-Bindung wird im neuen Dienstpfad akzeptiert.

Der Zustandsvergleich umfasst mindestens `chain_id`, `epoch`, `count`,
`head_hash`, den Checkpoint-Digest und `OPEN`/`SEALED`, nicht nur einen Zähler.
`chain_id` ist eine einmalig festgelegte, im unabhängigen Anker gebundene
32-Byte-Identität; Restore oder Neustart darf sie nicht neu erzeugen.

| Operation (Entwurf) | Eingabe | Erfolgsbedingung |
| --- | --- | --- |
| `status` | exakte Protokollversion, Chain-Identität | nonce-signierter vollständiger Zustand, einschließlich Epoch-/Checkpoint-Position |
| `commit_delta` | Version, erwarteter `base`-Zustand, geordnete neue `records`, bereits signierter Ziel-`head` | exakte Erweiterung des aktuell gehaltenen Zustands und dauerhaft gespeicherter Zielzustand |
| `seal_epoch` | erwarteter Zustand und bereits in der DB gespeicherter signierter Abschluss | Abschluss bindet exakt den gehaltenen letzten Kopf; Epoche wird dauerhaft geschlossen |

Für einen normalen Delta-Commit muss `base` exakt dem aktuellen Ankerzustand
entsprechen. Die Records beginnen bei `base.count`, sind lückenlos und enthalten
keinen alten Präfix. Der erste `previous_hash` ist der verankerte `head_hash`;
jeder folgende Record bindet den unmittelbar vorherigen. Alle kanonischen Bytes,
Typen, Indizes und Hashes werden geprüft. Der signierte Zielkopf hat
`count = base.count + len(records)` und genau den letzten Record-Hash. Neue
Records gehören derselben offenen Epoche; der gesonderte Öffnungsfall steht in
§12.4. Ein nur größerer, aber nicht von der gehaltenen Geschichte abstammender
signierter Kopf bleibt verboten, auch bei kompromittiertem Audit-Signierschlüssel.

**Ack und Wiederholung:** Das vorhandene C2-Envelope mit zufälliger 32-Byte-Nonce,
festem öffentlichen Antwortschlüssel und Label
`geniusnew/anchor-reply/ed25519/v1\n` bleibt. Das versionierte `reply` bindet
zusätzlich Operationsart, SHA-256 der kanonischen inneren Anfrage und den
vollständigen Zustand. Auch Refusals werden bei gültigem Envelope nonce-signiert.
Der Client prüft Nonce, Signatur, Request-Digest und sämtliche erwarteten
Zielkoordinaten; ein signiertes Ack für einen anderen Kopf ist kein Erfolg.

Ist nach Antwortverlust das Ziel bereits exakt der aktuelle Ankerzustand, ist
nur die **identische zuletzt dauerhaft bestätigte innere Anfrage** idempotent:
der Anker hält dafür ihren Digest zusammen mit Zielzustand und Kopf. Gleiche
Anfrage mit neuer Transport-Nonce liefert ein neu signiertes Ack, ohne erneutes
Journal-Append. Gleicher Count mit anderem Hash/Checkpoint, anderer Request-Digest,
Rückschritt, Teilüberlappung oder ein inzwischen überholtes Ziel werden abgelehnt.
Nach jedem unklaren Ausgang zuerst `status`, nicht blind alte Frames wiederholen.
Ein `status`-Nachweis des exakten bereits gespeicherten Zielkopfes kann Recovery
abschließen; er autorisiert keine erneute Jobausführung.

**Begrenzung:** Als zu entscheidender Startwert werden höchstens 128 Records und
1 MiB für den gesamten Delta-Frame vorgeschlagen; beide Grenzen gelten zugleich.
Event-Limit 8192 Bytes, Antwortlimit 4096 Bytes und Transport-Timeout bleiben
mindestens so streng wie bisher. Ein einzelner nicht darstellbarer Record wird
vor DB-Commit abgelehnt. Ein größerer gültiger Recovery-Suffix wird anhand der
bereits gespeicherten Zwischenköpfe in begrenzte Deltas zerlegt. Jeder Teil endet
an einem verifizierten, vor dem Crash signierten Kopf. Niemals einen ganzen
Epoch-/Lebenszeit-Count als Erlaubnis zur Speicherallokation verwenden.

### 12.4 Epochen, Abschluss und Beginn der nächsten Epoche

Für neu eröffnete Epochen ist eine **neue Kopf-/Record-Version** nötig, nicht
stilles Zurücksetzen des v2-Counts. Vorschlag für den signierten Kopfkörper:
`version = geniusnew-audit-head-v3`, `chain_id`, `epoch`, lokaler `count`,
`head_hash`, `previous_checkpoint_sha256`. Alle Felder werden mit der vorhandenen
Audit-Signierrolle über die kanonischen Bytes signiert. Record-Hashes binden
Version, Chain-Identität, Epoche, lokalen Index, `previous_hash` und die typisierte
Payload. Alte Köpfe und Records werden nicht umgeschrieben oder nachsigniert.

Lokale Indizes beginnen in jeder neuen Epoche bei 0. `(chain_id, epoch, index)`
bzw. `(chain_id, epoch, count)` werden die eindeutigen Schlüssel; Indizes und
Counts sind nur **innerhalb derselben Epoche** vergleichbar. Epoch-Nummern steigen
exakt um eins und sind begrenzte nichtnegative `bigint`-Werte, keine
Materialisierungsgrößen. Ein Überlauf wird abgelehnt. Die historische
`_MAX_COUNT`-Schranke wird nicht pauschal erhöht oder deaktiviert; neue Decoder
trennen Epochenposition, lokale Record-Anzahl und Frame-Limits ausdrücklich.

Ein Abschluss `geniusnew-audit-epoch-close-v1` enthält die Chain-Identität,
die aktuelle und genau nächste Epoche, den vollständigen finalen signierten Kopf,
den Digest des vorigen Abschlusses sowie Anzahl, Bytezahlen und SHA-256 der
geordneten Record- und Kopf-Archivströme. Der Abschluss wird kanonisch mit
Ed25519 durch die Audit-Rolle signiert und unverändert in einer vorgeschlagenen
append-only-Tabelle `audit_checkpoints` gespeichert; höchstens ein Abschluss je
`(chain_id, epoch)`. Der Digest gilt für die vollständigen signierten
Abschlussbytes, nicht nur für einen ungebundenen Metadatenzeiger.

**Wechselreihenfolge unter dem Session-Lock:** Zuerst aktuellen Endkopf vollständig
verankern; Epoche vollständig einschließlich aller gespeicherten Signaturen und
Archivströme prüfen; Abschluss signieren und dauerhaft in der DB speichern;
`seal_epoch` bestätigen lassen. Danach ist kein weiteres Event in dieser Epoche
zulässig. Der erste Record der nächsten Epoche ist ein eigener typisierter
Checkpoint-Record mit den **exakten signierten Abschlussbytes** als Payload und
deren Digest als `previous_hash`. Sein Index ist 0, sein signierter Kopf hat
Count 1 und bindet dieselbe Chain-Identität, `epoch + 1` und den Abschlussdigest.
Es gibt keinen All-zero-Neubeginn nach der ersten Genesis und keine Zyklik, in der
ein Abschluss seinen eigenen Hash voraussetzt. Der neue Record ist kein als
gewöhnliches `AuditEvent` getarntes Metadatenfeld; sein eigener Typ braucht
explizite Validierung, Versionierung und Tests.

Der Anker nimmt diesen Öffnungs-Delta nur aus dem exakt passenden `SEALED`-Zustand
an. Er prüft Abschluss-Signatur, finalen eigenen Kopf, Vorgängerabschluss,
Nachfolger-Epoche, Genesis-Record und neuen Kopf. Ohne bestätigten Beginn bleibt
die nächste Epoche für fachliche Events gesperrt. Ein Checkpoint ist eine
Kontinuitätsbindung, **kein Ersatz für Records, Original-Wires oder Startprüfung**.
Archiv-Digests werden durch den Kern gegen die Bytes geprüft; der Anker behauptet
mit seinem Ack weder Archivverfügbarkeit noch die fachliche Richtigkeit von B6.

Als Startwerte zur Entscheidung: höchstens 16384 Records oder 64 MiB kanonische
Record-Bytes pro neuer Epoche, was zuerst erreicht wird. Zusätzlich muss das
aktive Ankerjournal unter 16 MiB bleiben, mit vorab reservierten 64 KiB für
Abschluss-/Übergangsdaten. Tatsächliche kodierte Größen entscheiden; kein
Hochrechnen ausschließlich mit Minimalzeilen. Rotation erfolgt vor Überschreiten
einer Grenze. Scheitert sie, wird abgelehnt, statt Limits anzuheben oder Historie
zu vergessen. Grenzwerte und Platzreserve müssen zusammen getestet werden.

### 12.5 Speicherung und Archivierung ohne neues Lebenszeitlimit

**Core, vorgeschlagen:** `audit_epochs` beschreibt die eindeutige Genesis,
Epoche, unveränderlichen Bereich und Archiv-Digests. `audit_chain` und
`audit_heads` behalten pro Record die Originalbytes und seine gespeicherte
Signatur; geschlossene Bereiche werden in einen lesbaren PostgreSQL-Kaltbereich
verschoben. Die aktive Abfrage benennt genau eine Epoche und hat einen passenden
Index. Ein globaler `MAX`/Vollscan über alle Archive ist kein aktiver Pfad.
Zustandsmarker oder Archivpfade sind keine Autorität: maßgeblich bleiben die
signierten Checkpoints und der unabhängige Anker. Die Runtime darf geschlossene
Records, Köpfe und Checkpoints weder ändern noch löschen.

Archivströme werden deterministisch nach lokalem Index/Count gebildet, ein
kanonischer Datensatz pro Zeile mit abschließendem LF; Typ, Bereich und Länge
sind Teil des signierten Deskriptors. Kopfströme enthalten auch die ursprünglichen
`created_at`-Werte. Bytea-Inhalte werden reversibel und eindeutig kodiert, nicht
als neu normalisierte Nutzdaten. Bei legacy-v2-Records werden vor dem Export die
ursprünglichen Event-Bytes gegen die Kanonform geprüft. Digests werden über die
tatsächlich archivierten Bytes berechnet. Kein Vollarchiv oder Wire wird ins
Git-Repository, Audit-Event, Anker-Reply oder NotebookLM kopiert.

**Anker pro Epoche:** Die aktive Epoche hält ein begrenztes append-only-Journal
akzeptierter Zielköpfe, Operation-/Request-Digests und des Abschlusses. Persistente
Einträge binden außerdem ihren Vorgängerzustand; Start prüft diese Übergänge.
Dafür signiert der Anker jeden kanonischen Journal-Eintrag mit seinem vorhandenen
eigenen C2-Schlüssel, jedoch unter der getrennten Zustands-Domäne
`geniusnew/anchor-state/ed25519/v1\n`: Vorgängereintrag-Digest, Base/Ziel,
Operation, Request-Digest und die vollständigen Audit-Kopf-/Abschlussbytes sind
gebunden. Eine Audit-Kopfsignatur allein authentisiert den Request-Digest nicht.
Start prüft beide Signaturrollen; der Core bekommt keinen privaten Ankerschlüssel.
Ein manipuliertes Idempotenzfeld darf auch nach Neustart kein Ack auslösen.
Geschlossene Journale einschließlich Abschluss werden unter der Kontrolle des
Ankers unveränderlich archiviert, nicht in der Core-DB und nicht mit deren
Zugangsdaten. Im aktiven Speicher liegen nur aktueller Kopf, Epoche, Phase,
letzter Request-Digest und letzter Checkpoint. Die Genesis und die verketteten
Abschlussnachweise werden dauerhaft aufbewahrt und seitenweise gelesen.

Insbesondere werden **nicht alle Epochenabschlüsse in dieselbe auf 16 MiB
begrenzte Datei oder Antwort angehängt**. Begrenzte Epochen-/Katalogsegmente und
ein kleiner aktueller Positionszeiger verhindern bloßes Verschieben des Limits.
Die Zahl archivierter Segmente und ihr Plattenbedarf bleiben wachsend.
Ein Positionszeiger allein genügt nicht zum Start: Manifest, Abschlusskette,
alle Ankerjournale und der referenzierte aktuelle Stand müssen zusammenpassen.

Ack erst nach dauerhaftem Journal-Eintrag. Neue Dateien/Segmente werden zuerst
vollständig geschrieben und `fsync`-gesichert; Veröffentlichung/Umbenennung und
Verzeichniseinträge müssen ebenfalls dauerhaft sein, bevor der neue Zeiger oder
ein Ack sichtbar wird. Der exklusive Lease schützt die gesamte Store-Identität
über Dateirotationen hinweg, nicht nur den gerade geöffneten alten Inode.
Namespace-/Hardlink-Grenzen des vorhandenen Leases werden nicht für gelöst erklärt.
Eine zerrissene autoritative Datei wird niemals automatisch abgeschnitten;
fehlende oder mehrdeutige Zustände bleiben HOLD. Ein unreferenziertes Staging-
Objekt darf unbenutzt bleiben, aber keinen Initialisierungs-/Reset-Pfad auslösen.

**Archivwechsel:** Kopie bzw. PostgreSQL-Kaltbereich zuerst erstellen und byte-genau
prüfen; dann in einer kontrollierten, transaktionalen Bereichsumschaltung den
Archivort veröffentlichen. Erst danach kann eine redundante heiße Kopie durch
die getrennte Betriebs-/Migrationsrolle entfernt werden. Kein Verlust der
logischen Historie, kein `DROP` der letzten Kopie und kein Runtime-`DELETE`-Grant.
Ein physischer PostgreSQL-Archivwechsel erhält Foreign Keys und die Sichtbarkeit
für die vollständige Startprüfung. Ein zusätzliches Offline-/Objektspeicherarchiv
ist **nicht** Teil dieses Vorschlags; seine Einführung braucht einen eigenen
Verfügbarkeits-, Rechte- und Restore-Vertrag.

### 12.6 Vollständige Startprüfung pro Epoche — Ergänzung zu §8

Der Listener bleibt geschlossen, während ein konsistenter Zustand unter der
instanzübergreifenden Sperre geprüft wird. Die Vollprüfung wird nur gestreamt und
nach Epochen gegliedert, **inhaltlich nicht auf die aktive Epoche reduziert**:

1. Migrationen, effektive Rechte, Identität des unabhängigen Ankers und eindeutige
   Genesis prüfen. Jede fehlende, zusätzliche, doppelte oder falsch verkettete
   Epoche sowie ein unbekannter Formatwechsel ist ein Fehler.
2. In **jeder** aktiven und archivierten Epoche alle Records, kanonischen Bytes,
   Indizes, Hash-Verknüpfungen und **jeden** gespeicherten signierten Kopf prüfen;
   1:1-Zuordnung und Zeitbindung bleiben bestehen. Der Kern prüft seine Abschlüsse;
   der Anker prüft beim eigenen Start alle seine Journal-/Archivsegmente samt
   Vorgängern, bevor er einen autoritativen C2-Status ausgibt. Der Core erhält
   dafür weder Dateizugriff noch Anker-Zugangsdaten. Kein `verified=true` ersetzt
   eine der beiden vollständigen Startprüfungen.
3. Archivbytes, Bereiche und Digests gegen die Abschlüsse prüfen; jeden Beginn
   der nächsten Epoche gegen den exakten Vorgängerabschluss prüfen. Auch ein
   entferntes komplettes letztes Archiv muss über den gehaltenen Ankerstand
   auffallen. Fehlt ein erforderliches Archiv, startet der Dienst nicht.
4. Alle Job-/Acceptance-/Pending-/Approval-Bindungen aus §8 über die **Vereinigung
   sämtlicher Epochen und Archive** in beide Richtungen prüfen. Ein Job kann in
   Epoche E beginnen und in E+1 enden. Keine epocheweise Filterung, die solche
   Beziehungen oder entfernte globale Replay-Einträge übersieht. Arbeitsmengen
   werden in begrenzten Seiten verarbeitet; nötige vollständige Vergleiche können
   über DB-Joins bzw. sortierte Streams erfolgen, nicht über endlose Python-Tupel.
5. Den gesamten Ankerzustand an seiner exakten Position wiederfinden. Nur einen
   vollständig geprüften, bereits signierten DB-Suffix in begrenzten Deltas
   nachverankern. Übergänge nach §12.8 verwenden ausschließlich bereits dauerhaft
   gespeicherte Abschluss-/Kopfbytes. Im Recovery ist Signieren verboten.
6. Erst bei vollständiger Übereinstimmung Listener freigeben. Eine größere
   benötigte Prüfzeit erlaubt weder Auslassen von Archiven noch Annahme während
   der Prüfung. Gesamtkosten des Starts bleiben linear in der Historie; dieser
   Entwurf verspricht keinen konstanten Neustart.

Die bestehende Bindung historischer Acceptances an aktuelle Policy/Prüfschlüssel
bleibt bestehen. Archivierung ist keine Freigabe für eine neue Policy-Historie,
Schlüsselrotation, Wiederherstellung hinter dem Anker oder Aktivierung von D1.

### 12.7 Übriges Wachstum: Annahmen und abgelaufene Pending-Jobs

**Annahmen:** Ein Audit-Digest ersetzt nicht die Original-Wires. Vorgeschlagen ist
eine Trennung von dauerhaftem, schmalem `acceptance_ledger` und unveränderlichen
`acceptance_payloads` (neuer logischer Tabellenname). Das Ledger behält globalen
Primary Key `handoff_sha256`, globalen Unique Key `result_sha256`, `job_id`,
`accepted_at`, die Job-/Handoff-Bindung und eine eindeutige Payload-Referenz mit
Digest und Speicher-Epoche. Die Payload speichert `handoff_wire` und `result_wire`
weiterhin byte-genau als `bytea`; geschlossene Payload-Bereiche werden lesbar
archiviert. Ledger, Payload, `COMPLETED`-Übergang und `RESULT_ACCEPTED` committen
atomar. Constraints müssen vor Commit die eindeutige vollständige Payload und
den bisherigen Job-Foreign-Key erzwingen; ein nackter Tombstone ist keine
nachweisbare Acceptance. Start prüft auch archivierte Wire-Paare vollständig.

Die globalen Replay-Schlüssel bleiben **unpartitioniert und online**. Eine
Unique-Bedingung nur auf `(epoch, handoff_sha256)` oder `(epoch, job_id)` würde
dieselbe Identität in einer anderen Epoche zulassen und ist verboten. Eine
physische Aufteilung der Payloads muss weiterhin genau eine referenzierte Payload
je Ledger-Eintrag erzwingen; zusätzliche/verwaiste Payloads sind Startfehler.
`job_ledger` selbst bleibt mit jeder verbrauchten ID und seinem terminalen Zustand
erhalten. TTL, Archivwechsel, Neustart und neue Schlüssel geben weder Job-IDs,
Handoffs noch Ergebnis-Digests frei. Sonst könnte eine alte signierte Anfrage
nach Archivierung erneut als neu gelten. Die schmalen Indizes wachsen deshalb
bewusst weiter; ein Bloom-Filter, Cache oder nicht erreichbares Archiv ersetzt
keinen transaktionalen Konfliktnachweis.

**Pending / offene Entscheidung 5 aus `ROADMAP-V02.md`:** Der dort dokumentierte
Mindeststand lässt abgelaufene Zeilen liegen, zählt sie aber nicht mehr gegen die
aktive Kapazität. Das ist keine Bereinigung. Vorgeschlagen ist ein separat
freizugebender auditierter Sweep, kein stiller Cleanup beim Lesen.

Der Sweep arbeitet in begrenzten Portionen nach `expires_at, job_id` mit passendem
Index und derselben Sperrreihenfolge wie fachliche Approval-Auflösung. Pro Job
unter Row Lock den weiterhin gültigen Zustand `PENDING_APPROVAL`, die aktuelle
serverseitige Zeit und `expires_at <= now` nochmals prüfen. Dann **in derselben
Transaktion** die vollständige bisherige Pending-Zeile in eine vorgeschlagene
append-only-Tabelle `pending_job_archive` übernehmen, `job_ledger` auf `REFUSED`
setzen, den aktiven Pending-Eintrag entfernen und den bisherigen B6-Nachweis
`HANDOFF_REJECTED / PENDING_APPROVAL_EXPIRED` samt signiertem Kopf speichern.
`reserved_at` wird dabei nicht erfunden. Archiv und Event binden dieselbe Job-ID,
Handoff-Digest, ursprüngliche Felder und den tatsächlichen Ablehnungszeitpunkt.

Nach Commit denselben Anker-Ack abwarten; ohne ihn weder Sweep-Erfolg melden noch
weitere Mutation über die ungeklärte Grenze fortsetzen. Gewinnt parallel ein
Approval, entsteht kein Ablauf-Event; gewinnt der Sweep, wird Approval abgelehnt.
Ein bereits terminaler Job wird nicht erneut verändert oder auditiert. Historische
Approval-Records und verbrauchte Token bleiben erhalten. Aktives Pending und
archiviertes Pending sind verschiedene Relationen: die 1:1-Regel aus §8 gilt für
aktives Pending; Archive verlangen den passenden terminalen Ledger-Zustand und
Ablaufnachweis. Start mit einem abgelaufenen, noch nicht gesweepten aktiven Job
bleibt konsistent, aber der Job darf nicht mehr ausgeführt werden.

**Kapazität/Betrieb:** Auch Approval-Historien, Ankerarchive, PostgreSQL-Indizes,
WAL und Backups brauchen Platz. Überwacht werden aktive Bytes, Archivbytes,
Replay-Indexgröße, ältestes unaufgelöstes Pending, Anker-Lag und freie Kapazität.
Disk-full, fehlendes Archiv oder unbekannter Restore-Stand führen weiterhin zu
Refusal/HOLD, niemals zur Löschung verbrauchter Identitäten. Warnschwellen und
Sweep-Takt entscheidet Kaan; dieser Entwurf richtet keinen Timer ein.

### 12.8 Crashfenster — erneute Prüfung von §6

Die Tabelle beschreibt vorgeschlagenes Recovery, nicht bereits getestete Wirkung.
Jede Zeile braucht später einen echten PostgreSQL-/Anker-Neustarttest. B7-Regeln
für die Effect-Grenze bleiben zusätzlich bestehen.

| Crash-/Fehlerfenster | Dauerhafter Zustand und zulässige Reaktion |
| --- | --- |
| Vor DB-Commit, einschließlich fehlgeschlagenem Kopf-Insert | Record, Kopf und B6-Mutation rollen gemeinsam zurück; kein Delta und keine Wirkung freigeben. |
| DB-COMMIT-Ausgang unbekannt | Keine erneute Fachmutation. Verbindung verwerfen, vollständiger Startabgleich entscheidet, was gespeichert wurde; verbrauchte IDs bleiben verbraucht. |
| DB committed, vor Delta-Sendung | Suffix mit gespeicherten Köpfen erhalten; vollständig prüfen und nur diesen Suffix nachverankern, ohne Signaturerzeugung. |
| Delta empfangen/geprüft, Anker noch nicht dauerhaft | Kein Ack. Nach Neustart gilt nur der dauerhaft belegte Ankerzustand; bei intaktem alten Stand gespeichertes Delta erneut anbieten. Zerrissener autoritativer Eintrag bleibt HOLD. |
| Anker dauerhaft, Antwort verloren/falsche Nonce | Keine Erfolgsmeldung. Frischer signierter Status oder identische letzte Anfrage mit neuer Nonce bestätigt das exakte Ziel; niemals zweites Event/Job erzeugen. |
| Teilweise nachverankerter mehrteiliger Recovery-Suffix | Nach Neustart erneut vollständig prüfen; ab tatsächlich gehaltenem Zwischenkopf fortsetzen, nicht ab einem lokalen Versandzähler. |
| Ack erhalten, vor Rückgabe an den Aufrufer | Historie bleibt verbraucht. Ein verlorenes Resultat rechtfertigt keinen Job-Retry; `EXECUTION_COMMITTED` bleibt unbekannte Wirkung, wenn B7 das so feststellt. |
| Abschluss noch nicht in DB committed | Epoche bleibt offen am bestätigten Kopf; kein Epochenbeginn. Recovery erstellt keinen Ersatzabschluss. |
| Abschluss in DB, Anker noch OPEN | Vollständig gespeicherten Abschluss prüfen und `seal_epoch` erneut vorlegen; keine neuen Fach-Events in die zum Abschluss vorgesehene Epoche schreiben. |
| Anker SEALED, Beginn der nächsten Epoche fehlt | Alten Abschluss wiederfinden; Betrieb bleibt gesperrt, bis der reguläre, geprüfte Übergang den neuen Beginn atomar gespeichert und bestätigt hat. Recovery signiert ihn nicht nach. |
| Neuer Beginn in DB, Öffnungs-Ack fehlt | Genau dessen gespeicherten Kopf/Checkpoint-Record nachverankern; keine zweite Genesis und kein stilles Zurückspringen. |
| Ankerjournal-/Zeigerrotation unterbrochen | Nur eine vollständig durable, konsistente Generation wählen. Widerspruch, fehlender referenzierter Abschluss oder torn autoritative Datei: HOLD, kein automatisches Truncate/Reset. |
| Archivkopie fertig, Bereichsumschaltung nicht committed | Alter autoritativer Bereich bleibt gültig; Staging ist keine neue Historie. |
| Archivumschaltung committed, heiße Kopie noch vorhanden | Ein logischer Bereich, nicht doppelt zählen. Kopien müssen identisch sein; fehlende/abweichende Archivbytes blockieren den Start. |
| Pending-Sweep/Acceptance-Archivierung unterbrochen | Vor Commit kompletter Rollback; nach Commit Archiv, Replay-Zeilen und Audit gemeinsam prüfen, gegebenenfalls gespeichertes Delta nachverankern. Kein erneutes Acceptance-/Ablauf-Event. |
| DB oder Archive hinter dem Anker; Anker-Rollback oder Fork | Start verweigern, C5/Operator-Gate. Keine längere fremde Geschichte akzeptieren, keine fehlenden Daten neu signieren, kein Anchor-Reset. Gemeinsamer privilegierter Rollback aller Nachweise bleibt die bekannte Grenze. |

### 12.9 Neue bzw. neu zu konkretisierende Ablehnungen

Die Namen sind **Entwurfs-Reason-Codes**, noch keine vorhandene Runtime-API.
Jeder einzelne negative Fall braucht einen Test, der beim Entfernen der
Ablehnung rot wird. Ankerfehler bewirken keine Fach-Fortsetzung; lokale
Vorprüfungsfehler verhindern den DB-Commit. Start-/Archivfehler sperren den Listener.

| Reason-Code (Vorschlag) | Fälle |
| --- | --- |
| `AUDIT_TIP_INVALID` | Record/Kopf fehlt einseitig; Bytes, Hash, Signatur, Index/Count, Zeit oder Epoch-Bindung passen nicht. |
| `AUDIT_GENESIS_UNPROVEN` | Leerer/fehlender Store ohne unabhängig belegte erste Genesis; neue Chain-ID bei Restore. |
| `AUDIT_LOCK_STATE_INVALID` | Falsche Verbindung/Transaktionslage, vorzeitige Snapshot-Sicht, nicht gehaltener/verlorener Lock, Timeout oder Deadlock. |
| `AUDIT_COMMIT_UNKNOWN` | Persistenzausgang nicht belegbar; kein automatischer Fach-Retry. |
| `AUDIT_PROTOCOL_UNSUPPORTED` | Unbekannte/gemischte Version, zusätzliche/fehlende Felder, ungültige Typen oder nicht-kanonische Frames. |
| `ANCHOR_BASE_MISMATCH` | Falsche Chain/Epoche/Phase/Checkpoint-Position, veraltete Basis, Rückschritt, gleicher Count mit anderem Hash oder fremder längerer Fork. |
| `ANCHOR_DELTA_INVALID` | Leeres normales Delta, Lücke, Duplikat, Überlappung, falscher erster Vorgänger oder fehlerhafte interne Verkettung. |
| `ANCHOR_TARGET_INVALID` | Ungültige Signatur, falsche Domäne/Epoche/Chain, Endhash oder Count passt nicht zur Delta-Länge. |
| `ANCHOR_REPLAY_CONFLICT` | Ziel bereits erreicht, aber innerer Request-Digest weicht ab; alter Request hinter einem neueren Zustand. |
| `ANCHOR_ACK_UNTRUSTED` | Falsche/fehlende Signatur, falsche Nonce, Operation, Request-Digest, Version oder Zielzustand; ungebundene Refusal-Antwort. |
| `ANCHOR_UNAVAILABLE` | Timeout, unterbrochener Frame, nicht erreichbarer/unerwartet neu gestarteter Anker; kein lokaler Ersatz. |
| `AUDIT_CAPACITY_EXCEEDED` | Einzelrecord, Frame, Kopf, Journal oder Epoche überschreitet die jeweilige Grenze bzw. Abschlussreserve fehlt. |
| `AUDIT_EPOCH_TRANSITION_INVALID` | Append in geschlossene Epoche, übersprungene/zweite Genesis, Überlauf, falscher Vorgängerabschluss, Öffnung vor Seal-Ack. |
| `AUDIT_CHECKPOINT_INVALID` | Unsignierter/fremder Abschluss, abweichender finaler Kopf, Bereich, Anzahl, Bytezahl oder Archiv-Digest; zweiter Abschluss derselben Epoche. |
| `ANCHOR_DURABILITY_UNPROVEN` | Write/fsync/Verzeichnis-Persistenz gescheitert, verlorener Lease, beschädigtes/ungültig signiertes Journal (auch Request-Digest oder Vorgänger) oder mehrdeutige Zeigergeneration; kein Ack. |
| `AUDIT_HISTORY_INCOMPLETE` | Anker voraus; unsignierter Suffix; fehlende, doppelte, vertauschte oder manipulierte Records, Köpfe, Epochen oder Archive. |
| `ARCHIVE_BINDING_INVALID` | Fehlende/mehrfache Payload, falscher Digest/Bereich/Job-Foreign-Key oder Inkonsistenz zwischen heißer und kalter Kopie. |
| `REPLAY_HISTORY_INVALID` | Gelöschte/globale Replay-Sperre fehlt oder widerspricht Audit; epochenlokaler Unique Key würde Wiederverwendung zulassen. |
| `PENDING_SWEEP_CONFLICT` | Unter Lock nicht mehr abgelaufen/pending oder andere Row-Bindung: keine Ablaufmutation; Archiv-/Audit-Insertfehler rollt alles zurück. Bereits terminale Jobs sind ein belegter No-op, kein zweites Refusal-Event. |
| `AUDIT_UPGRADE_UNPROVEN` | Ungeprüfter Legacy-Stand, unzulässiger Mischbetrieb, fehlende Migrations-/Rechtefreigabe oder unbekannter Backup-/Restore-Stand. |

Bereits bestehende Byte-/Schema-/Signatur-/Rollen-Ablehnungen bleiben bestehen.
Der Refusal-Guard wird später durch Codex für jede betroffene bzw. neue
Implementierungsdatei ergänzt, nicht in diesem Dokument-PR geändert.

### 12.10 Roter Messtest und Security-Nachweise für Codex

**Zuerst rot, bevor Produktionscode geändert wird:** vorgeschlagener Test
`PostgresAuditTest.test_append_reads_bounded_rows_as_history_grows` in der
vorhandenen Datei `tests/test_postgres_audit.py`, auf Basis von
`tests/postgres_support.py::PostgresDatabase`. Das ist ein neuer Testname, kein
bereits existierender PASS.

In einer isolierten echten PostgreSQL-Testdatenbank gültige Ketten mit
N = 100, 1000 und 10000 gleich großen synthetischen Events sowie je einem korrekt
signierten Kopf pro Record aufbauen. Aufbau linear/bulk außerhalb der Messung,
nicht über N Aufrufe des bereits quadratischen Baseline-Appends. Startprüfung und
Vorverankerung vollständig durchführen, dann Messzähler zurücksetzen. Gemessen
wird **ein vollständiger `_AnchoredAudit.append` einschließlich Anker-Ack** auf
dem bereits aufgebauten Zustand, ohne Rotation oder Recovery.

Eine testseitige `psycopg`-Cursor-Instrumentierung zählt die tatsächlich an Python
zurückgegebenen Record-/Kopfzeilen sämtlicher Reads, nicht nur SQL-Aufrufe oder
`fetchone()` auf `count(*)`. Vorgeschlagenes Normalpfad-Budget: höchstens acht
Record-/Kopfzeilen plus vier kleine Metadatenzeilen je Append, unabhängig von N;
keine historische `snapshot`-/`_read`-Ausführung. Der heutige Code muss wegen
seiner wachsenden Zeilenmenge rot werden, nicht wegen eines fehlenden neuen API-Namens.

Zusätzlich aufgezeichnete reine SELECTs mit
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` in derselben isolierten Testumgebung
prüfen: bei den größeren Fixtures kein historienweiter Seq Scan, Aggregat oder
Sortieren aller Records unter einem oberflächlich konstanten `LIMIT`. Passender
Indexpfad und untersuchte Zeilen werden als Evidenz gespeichert. Keine
Planner-Schutzmaßnahme abschalten, um einen günstigen Plan zu erzwingen.
Der echte C2-Frame wird mitgemessen: ein neues Event, gespeicherter Endkopf und
feste Metadaten, kein mit N wachsendes `records`-Array. Getrennt wird bei wachsender
Anzahl archivierter Epochen geprüft, dass aktive Reads keine alten Partitionen
scannen. Die Vollstartprüfung liegt außerhalb des Append-Budgets, bleibt aber
ein eigener Pflichtnachweis über **alle** N Records/Köpfe.

Latenz p50/p95, gelesene Bytes, Frame-Bytes und DB-Plan pro N dokumentieren;
Timing allein ist wegen Cache/CI-Streuung kein rotes Gate. Korrektheit und
begrenzte Zeilen-/Frame-Arbeit sind deterministische Assertions. Dieser PR hat
keinen solchen Test ausgeführt und behauptet keine gemessene Beschleunigung.

Weitere obligatorische Fälle für die spätere Umsetzung:

- Zwei echte DB-Verbindungen und zwei Core-Instanzen, blockiertes/verlorenes Ack,
  Crash des ersten Schreibers: zweite Instanz verifiziert/repariert den gehaltenen
  Präfix vor eigener Mutation. Alle Crashfenster aus §12.8, auch SIGKILL beim
  Journal-/Epochenwechsel, ohne Doppelwirkung und ohne Nachsignieren.
- Delta-Fork trotz gültiger Audit-Signatur; falsche Nonce/Antwortdomäne; alte
  signierte Antwort; gleiche Position mit anderem Checkpoint; identischer Retry
  ohne zweite Journalzeile; manipuliertes persistentes Request-Digest-Feld,
  Journal-Signatur-/Domänenfehler nach Neustart; ungültige Überlappung und alle
  §12.9-Ablehnungen.
- Mehrere Epochen mit absichtlich kleinen Testlimits, danach separater Langlauf
  über mindestens 70000 Records: alte Lebenszeitgrenze überschritten, aktive
  Budgets eingehalten, vollständiger Neustart erfolgreich. Ein zu großer
  Einzelrecord oder nicht ausführbare Rotation bleibt Refusal vor DB-Commit.
- Manipulierte ältere Signatur, nicht-kanonische Eventbytes, fehlendes komplettes
  Archiv, falsche Abschlusskette, unsignierter SQL-Suffix, fehlender Replay-Key,
  fehlende/falsche Acceptance-Payload: vollständiger Start verweigert.
- Gleiche Job-ID/Handoff/Result-Digest vor und nach Archivierung, Epochwechsel und
  Neustart, einschließlich Parallelität: weiterhin Replay-Refusal. Approval/Sweep-
  Rennen, wiederholter Sweep, Uhr-Rücksprung und Crash zwischen seinen Statements:
  kein aktiver Job fälschlich abgelaufen, kein zweiter Ablaufnachweis.

**Bestehende Tests und `SECURITY.md`-Zeilen am Baseline-SHA:**

| Zeilen / Thema | Erhalten oder gesondert ändern |
| --- | --- |
| `SECURITY.md:56-57` — volle Persistenzprüfung, Vollsnapshot/64 MiB/16 MiB | Nur ein späterer Code-PR darf die Transport-/Lebenszeitgrenze schließen. `test_oversized_anchor_commit_refuses_before_a_database_commit` und `test_anchor_state_bound_refuses_before_a_database_commit` in `tests/test_postgres_audit.py` werden in Delta-/Epochen-Budgettests überführt, nicht gelöscht; Schutz vor nicht verankerbarem DB-Commit bleibt. `test_aggregate_event_bytes_refuse_before_bulk_fetch` und `test_record_count_refuses_before_bulk_fetch` erhalten begrenzte Seiten-/Epochen-Entsprechungen. |
| `SECURITY.md:56,58` — alle Köpfe, nur gespeicherte Signaturen, B6 | `test_sql_unsigned_suffix_is_a_start_refusal_not_a_recovery_signature`, `test_altered_canonical_bytes_and_earlier_signature_are_refused`, `test_recovery_reuses_the_precrash_signature_and_proves_the_anchor_prefix` und `test_runtime_cannot_update_delete_or_truncate_either_audit_table` bleiben wirksam; `tests/test_b6_reconciliation.py` und `tests/test_b7_crash_recovery.py` um Archive/Epochen ergänzen. |
| `SECURITY.md:50-51` — Anker-Rollback und Lease-Grenze | Der ausdrücklich offen gehaltene Test `tests/test_anchor_process.py::test_a_file_rolled_back_to_an_older_signed_head_is_accepted_and_this_is_the_boundary` bleibt begründet offen. Checkpoints schließen privilegierten gemeinsamen Rollback nicht. `AnchorClientTest`, Zwei-Anker-/Hardlink-Tests und der Served-Anchor-Test aus `tests/test_serve.py` bleiben; Rotation darf den Lease nicht umgehen. |
| `SECURITY.md:47-48,52` — Policy-/Schlüsselbindung, dauerhaft verbrauchte IDs, Pending | `test_changed_trusted_policy_or_keys_refuse_historical_acceptance` in `tests/test_acceptance_ledger.py` sowie `PersistentAcceptanceTest` und `tests/test_pending_database.py` gelten auch für Archive. Keine Lösch-/TTL-Ausnahme und keine nebenbei geschlossene Policy-Historiengrenze. |
| `SECURITY.md:49` — Signaturrollen | Neue Formate behalten Ed25519 und getrennte Signaturrollen; der Anker erhält keinen Audit-Privatschlüssel. Die Root-Secret-/Worker-Schlüsselgrenze bleibt unverändert. |

Neu offen zu halten und **nur nach Kaans Grenzentscheidung** einzuführen ist
beispielsweise `test_historical_corruption_after_start_is_detected_on_full_recheck_not_tip_append_and_this_is_the_boundary`:
einen alten Nicht-Tip-Record nach erfolgreichem Start mit der Test-Eigentümerrolle
beschädigen; nachweisen, dass ein schneller Append diesen nicht liest, die
vollständige Nachprüfung und jeder Neustart ihn aber ablehnen. Dieser Test wäre
keine Heilung der Grenze. Verlangt Kaan weiterhin Erkennung vor jedem Append,
bleibt der inkrementelle Pfad HOLD; ein Performanceziel darf keinen bestehenden
Sicherheitsnachweis still umkehren.

### 12.11 Umsetzungsschnitt, Migration und Gates

**Kein Code in diesem PR.** Betroffene spätere Implementierungspfade sind
`geniusnew/audit_store.py` (Tip-/Suffix-Lesen, Epochen- und B6-Prüfung),
`geniusnew/anchor_process.py` (Delta/C2/Journalwechsel),
`geniusnew/audit_chain.py` (versionierte Epochen-/Checkpoint-Validierung),
`geniusnew/wiring.py::_AnchoredAudit` (Commit/Ack/Recovery) und die bestehenden
DB-/Migrationspfade für Payload- und Pending-Archivierung. Diese Benennung ist
keine Schreibfreigabe für ChatGPT. SQL-Migrationen erhalten neue Versionen;
bestehende Migrationen und ihre Checksums bleiben unverändert.

**Legacy-Cutover, Empfehlung:** kontrolliertes Wartungsfenster, alle Core-Schreiber
anhalten, DB und separaten Anker samt Archive sichern, alte vollständige Prüfung
und exakten Ankerabgleich bestehen. Den alten v2-Bestand als unveränderte
Legacy-Epoche 0 versiegeln. Nur dieser ausdrücklich geprüfte Übergang darf einen
v2-Endkopf in einem neuen Abschluss binden und Epoche 1 eröffnen. Das ist eine
neue Übergangssignatur über bereits geprüfte/belegte Geschichte, kein Umdeuten
alter Record-/Kopfsignaturen. Bereits ungültige oder übergroße Altzustände werden
nicht durch verkürztes Einlesen migriert; ein gesonderter Recovery-Entwurf ist nötig.
Kein ungeprüfter Live-Mischbetrieb alter und neuer Schreiber und kein automatischer
Downgrade nach dem ersten neuen Ankerzustand. Restore zurück hinter diesen Stand
ist keine zulässige Rollback-Abkürzung. Separate Freigabe vor realer Migration.

Der spätere Arbeitsauftrag an Codex wird erst nach Entscheidungen aufgeteilt:
(a) rote Wachstums-/Grenztests und versionierter Audit-/Ankerpfad samt Epochen,
(b) lesbare Payload-Archive mit globalen Replay-Sperren,
(c) auditierter Pending-Sweep. Jeder PR basiert auf `main`, nie auf einem noch
offenen PR. Ein isolierter Tip-only-Zwischenschritt darf nicht als fertig oder
produktionsfähig gelten, solange Volltransport, Recovery oder Archivierung fehlen.

**Review-Reihenfolge:** Gemini-Design-Vorprüfung der Prozessgrenze/Kryptografie
im Draft-PR anfordern, mit vollem Head-SHA und Format aus `COLLABORATION.md`;
danach unabhängiger Claude Security Review des Entwurfs. Kaan entscheidet die
untenstehenden Punkte, gibt den Entwurf frei und mergt selbst. Vor Code müssen
Gemini-Befunde geklärt sein; kein Werkzeug gibt seine eigene Arbeit frei.
Die späteren DB-Code-PRs brauchen zusätzlich `Claude DB Review: APPROVED` am
exakten Head, `contracts`, Copilot-Review und die erforderlichen Gemini-/Kaan-
Gates für geänderte `SECURITY.md`-Grenzen. CodeRabbit wird im vorhandenen
PR-Review-Ablauf berücksichtigt, ersetzt aber keines dieser Gates.

Technische Referenzen zu den ausdrücklich entworfenen Datenbankzugriffen:
[PostgreSQL Advisory Locks](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS),
[Transaktionssicht](https://www.postgresql.org/docs/18/transaction-iso.html),
[ORDER BY mit Index und LIMIT](https://www.postgresql.org/docs/15/indexes-ordering.html),
[globale Unique-Grenzen partitionierter Tabellen](https://www.postgresql.org/docs/18/ddl-partitioning.html#DDL-PARTITIONING-DECLARATIVE-LIMITATIONS).
Diese Dokumentation ersetzt weder einen Query-Plan noch die Prüfung der
installierten PostgreSQL-Version. Reproduzierbare Evidenz entsteht erst in den
benannten Tests; insbesondere ist keine vollständige Schemaschutzprüfung als
Umgehung der bestehenden Architekturentscheidung still eingeführt.

### 12.12 Offene Fragen an Kaan

1. Wird die in §12.1 beschriebene spätere Erkennung historischer Manipulationen
   akzeptiert und als eigene Security-Grenze freigegeben, oder bleibt deshalb der
   Tip-only-Pfad gesperrt? Vollständige Startprüfung bleibt in beiden Fällen Pflicht.
2. Werden die vorgeschlagenen Delta-/Epochengrenzen (128 Records/1 MiB,
   16384 Records/64 MiB, 16 MiB Ankerjournal mit 64 KiB Reserve) übernommen?
   Welche Kapazitätswarnschwellen sollen vor einem fail-closed-Stopp gelten?
3. Wird das weiterhin vollständig lesbare PostgreSQL-Archiv mit dauerhaft
   vorhandenen Original-Wires, globalen Replay-Indizes und linearer Vollstartprüfung
   als erster Archivierungsweg bestätigt? Es ist keine Lösch-/Aufbewahrungsfreigabe.
4. Soll der auditierte Pending-Sweep aus offener Entscheidung 5 als eigener PR
   nach dem Audit-/Epochenpfad umgesetzt werden, und mit welchem Betriebs-Takt?
5. Wird der kontrollierte Legacy-v2-Cutover im Wartungsfenster statt Live-Mischbetrieb
   bestätigt? Zeitpunkt, Backup-/Restore-Nachweis und tatsächliche Migrationsfreigabe
   bleiben eine gesonderte Entscheidung vor jeder Änderung am laufenden System.
