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
