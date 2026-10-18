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

### 4.9 D1-Replay-Reservation (überarbeiteter Entwurf, D1 bleibt HOLD)

Grundlage sind die Entscheidungen vom 18.10.2026 in
[`DECISIONS.md`](DECISIONS.md) und [Issue #160](https://github.com/Kaancodm/GeniusNew/issues/160).
Sie entscheiden den Annahmepfad von Gate 9, die 65-s-Start-Quarantäne (Gate 3)
und die Zulässigkeit der D1-Startprüfung (Gate 7). Die nachstehenden Speicher-
und Audit-Erweiterungen sowie die Lastzahlen sind **Vorschläge zur Prüfung und
Freigabe**, kein implementierter Schutz. Der zugehörige Schnittstellenentwurf
[`PORTAL-CORE-D1-DRAFT.md`](PORTAL-CORE-D1-DRAFT.md) wird getrennt in
[Issue #159](https://github.com/Kaancodm/GeniusNew/issues/159) angepasst.
Widersprüche zwischen beiden Entwürfen sperren D1 bis zum Abgleich und Review.

Vor D3 gilt ausschließlich die geschlossene Beta mit Kaan als einzigem
Portal-Nutzer. Der Dienst-Grant verlangt keine Freigabe und erlaubt nur
risikoarme Tools; Dienst-Subject als Approver, als Bearer-Principal oder mit
`requires_approval=true` verweigert den Start. Ein zweiter Portal-Nutzer braucht
D3. Die folgenden Core-Tabellen geben Portal oder Browser keine Core-Rechte.

#### 4.9.1 Replay-Zeile und Rechte

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
    job_id text not null unique references job_ledger(job_id),
    envelope_sha256 char(64) not null
        check (length(envelope_sha256) = 64
               and envelope_sha256 !~ '[^0-9a-f]'),
    primary key (domain, issuer, nonce)
);
```

`nonce` ist die exakte 32-Byte-Dekodierung der 64 lowercase Hex-Zeichen aus dem
signierten Envelope. `domain` und `issuer` bleiben über Schlüsselrotation und
Deployments stabil; `key_id` und `audience` sind absichtlich nicht Teil des
Primary Keys. Rotation oder Wechsel des Core-Ziels hebt eine noch benötigte
Reservation nicht auf. Alle annehmenden Core-Instanzen benutzen dieselbe
Tabelle auf demselben schreibbaren Primary. Split-Brain, getrennte schreibbare
Kopien oder unbekannter Failover-Stand sperren D1.

Die Core-Runtime behält ausschließlich `SELECT` und `INSERT` auf dieser
Tabelle: kein `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`, `MAINTAIN`
oder DDL. Sie ist weder Owner noch Superuser und hat kein `BYPASSRLS`,
`CREATEROLE`, `CREATEDB` oder `TEMP`. Das gilt auch indirekt über `PUBLIC`,
Default-Privileges, Mitgliedschaften, Vererbung, `SET ROLE`, Schemaprivilegien,
`pg_write_all_data`, `pg_maintain` und ausführbare `SECURITY DEFINER`-Funktionen.
Die Portal-Runtime hat keinerlei effektive Core-Rechte. Nur die getrennte
Migrationsrolle ändert das Schema; eine eigene Bereinigungsrolle erhält den
unten begrenzten Löschpfad, niemals die Runtime. Alle Zugriffe qualifizieren
Schema und Objekt; der sichere `search_path` darf nicht umgangen werden.

`expires_at` ist **kein automatischer Löschauftrag**. Nur der auditierte Pfad
in 4.9.5 darf eine hinreichend alte Zeile entfernen. Das ursprüngliche
Envelope bleibt danach wegen seiner abgelaufenen Gültigkeit unzulässig.
D1 verspricht damit keine ewige Nonce-Historie und keine Idempotenz einer mit
neuem Envelope erneut signierten menschlichen Handlung. Job-IDs und die
zugehörige Audit-Historie bleiben dauerhaft erhalten.

#### 4.9.2 Gemeinsame Annahmetransaktion und versionierte Audit-Bindung

Nach geschütztem Ingress, Vorab-Budget, vollständiger Eingabeprüfung,
Zeitprüfung, erfolgreicher Ed25519-Prüfung, Dienst-Budget, Policy- und
Kapazitätsprüfung erzeugt der Kern eine zufällige `job_id` **vor COMMIT**.
Handoff und Job-ID stammen allein vom Kern. In **einer** expliziten
PostgreSQL-Transaktion entstehen die Job-/Handoff-Zustände des bestehenden
Annahmepfads, die Replay-Reservation und ihre Audit-Records einschließlich der
bereits signierten Köpfe. Die durch B6 vorgeschriebenen Ereignisse
`HANDOFF_ISSUED` und `HANDOFF_ADMITTED` entfallen dabei nicht.

Der nicht deferrable Fremdschlüssel erlaubt folgenden Ablauf: zunächst die
noch uncommittete `job_ledger`-Zeile anlegen, dann
`INSERT ... ON CONFLICT (domain, issuer, nonce) DO NOTHING RETURNING domain,
issuer, nonce` ausführen. Nur genau eine zurückgegebene Zeile mit exakt den
angefragten drei Werten ist der Gewinnernachweis. Null Zeilen oder abweichende
Werte rollen **auch** die neue Job-Zeile zurück. Vor dem gemeinsamen COMMIT
sind die Audit-Bindungen vollständig. Es gibt keinen separat bestätigten
Nonce-COMMIT vor Job-Erzeugung und keine sichtbare Job-Zeile ohne Reservation.

**Vorgeschlagene Audit-Erweiterung:** `event_version=3` erweitert für D1 die
bestehenden Job-Ereignisse um eine streng geformte D1-Bindung. Sie enthält
`digest_version="geniusnew.d1.envelope.v1"`, `envelope_sha256`,
`replay_key_sha256` und `expires_at`; `api_subject_sha256`, Handoff-/Payload-
Bindung und Job-ID bleiben nach dem bestehenden B6-Vertrag gebunden.
`envelope_sha256` ist SHA-256 von exakt
`b"geniusnew/portal-core/request/ed25519/d1\x00" + envelope_bytes`, also der
Digest der Signierbytes, ohne Signatur oder Transportkodierung.
`replay_key_sha256` ist SHA-256 von
`b"geniusnew/d1/replay-key/v1\x00" + canonical({"domain": domain,
"issuer": issuer, "nonce": nonce.hex()})`. Die Kanonisierung folgt dem
D1-Byteprofil. Keine Nutztexte, Raw-Nonces, Signaturen oder Secrets im Audit.

Mit D1-Annahmeereignis ist im folgenden Abgleich ausschließlich das
versionierte `HANDOFF_ADMITTED` gemeint; `HANDOFF_ISSUED` bleibt ein
zusätzlicher B6-Nachweis, keine zweite Reservation.

Die neue Eventversion ist **noch nicht im Code vorhanden**. `audit.py` lässt
an der Baseline nur Version 1 und 2 zu; `audit_store.py` prüft B6-Bindungen
explizit als Version 2. Codex muss Parser und Abgleich gezielt um die geschlossenen
D1-Formen erweitern, ohne unbekannte Versionen/Felder oder fehlende bestehende
Bindungen zu akzeptieren. Historische v1/v2-Regeln bleiben unverändert; kein
Umetikettieren alter Daten. Der konkrete Wire-Vertrag muss mit #159 identisch
sein, bevor Gate 9 insgesamt als geschlossen gilt.

Nach `BEGIN` wird `SET LOCAL synchronous_commit TO on` gesetzt und unmittelbar
vor COMMIT wirksam geprüft, ohne dazwischenliegende Änderung oder Savepoint-
Rücknahme. `fsync=on` und `full_page_writes=on` werden beim Start und vor der
Reservation wirksam geprüft; ist das Betriebsprofil bis COMMIT nicht
sicherzustellen, wird abgelehnt. Nach DB-/Lock-Wartezeiten und unmittelbar vor
COMMIT wird die Zeit neu geprüft. Ablauf bedeutet ROLLBACK aller Annahmedaten.

`lock_timeout`, `statement_timeout`, `idle_in_transaction_session_timeout`
und ein unabhängiges Transaktions-Gesamtlimit sind durch Restgültigkeit und
ein konfiguriertes Maximum begrenzt. Vorgeschlagenes Maximum: **2 s** pro
Annahmetransaktion, damit ein lokaler DB-Stau die vier D1-Plätze nicht bis zur
60-s-Gültigkeitsgrenze hält. Tatsächliche Restgültigkeit in Millisekunden
abrunden; Werte unter 1 ms bedeuten REFUSE, niemals PostgreSQL-Timeout `0`.
Der Wert ist mit Last-Gate 5 zu messen und von Kaan freizugeben.

Erst bestätigter synchroner DB-COMMIT **und** Anchor-Bestätigung nach §6
erlauben die Fortsetzung. Der Worker läuft außerhalb der Transaktion und
hinter den unveränderten Ausführungs-Gates. Crash vor COMMIT: keine
Annahmemutation; Crash danach: Reservation, Job und Audit bleiben gemeinsam
vorhanden, kein automatischer Wiederanlauf des Jobs. Ein unbekannter COMMIT-
Ausgang ist `UNKNOWN`, nicht nachweislich ROLLBACK: kein Erfolg, kein Retry,
keine zweite Job-ID und kein nachträglich erfundener Ablehnungsbeleg. Erst die
bestehende Recovery darf den tatsächlichen Zustand feststellen.

#### 4.9.3 Signaturgeprüfte Ablehnungen ohne neue Reservation

Für einen sicher feststehenden Nichtannahmefall nach erfolgreicher Signatur-
prüfung, etwa Nonce-Konflikt oder Ablauf nach der zweiten Zeitprüfung, wird
eine begonnene Annahmetransaktion vollständig zurückgerollt. Danach schreibt
eine **eigene kurze Transaktion** ausschließlich ein vorgeschlagenes
`event_version=3`, `action="D1_REQUEST_REJECTED"`, `decision="DENIED"` und den
zugehörigen bereits signierten Audit-Kopf in `audit_chain`/`audit_heads`.
Sie erzeugt weder eine neue Replay-Zeile noch einen künstlichen Job-/Handoff-
Datensatz. Bei unbekanntem COMMIT-Ausgang gilt stattdessen 4.9.2.

Die geschlossene Ablehnungsform bindet eine serverseitig zufällig erzeugte
`request_id`, Actor, Policy-/Verfassungsversion, `api_subject_sha256`, geprüfte
Core-Zeit, die beiden versionierten Digests aus 4.9.2 und `expires_at`.
`reason_code` stammt ausschließlich aus der festgelegten Allowlist
`D1_REPLAY`, `D1_EXPIRED_AFTER_VERIFY`, `D1_SERVICE_RATE_LIMIT`,
`D1_POLICY_DENIED`, `D1_CAPACITY_EXHAUSTED`, `D1_STORAGE_UNAVAILABLE`.
Job-ID, Handoff-/Ergebnis-Digest, Approval-Referenz und Nutzeridentität sind
hier **nicht anwendbar und nicht als Platzhalter zu erfinden**. Diese neue
Form ist nicht durch Lockerung der vorhandenen Job-Eventform umzusetzen.

Das Ablehnungs-Event ist selbst der dauerhafte Datensatz; eine zusätzliche
unabhängige Ablehnungstabelle ist nicht nötig. Beim Start werden seine
Kanonform, zulässige Feldkombination, Signaturkette/Köpfe und eindeutige
`request_id` geprüft. Es beansprucht keinen Job-Ledger-Eintrag und ist keine
Replay-Reservation. Bei `D1_REPLAY` bindet es zusätzlich den Record-Hash des
vorherigen, passenden D1-Annahmeereignisses. Auch nach bereinigter Replay-
Zeile bleibt dieser Beleg in der Audit-Kette erhalten. Referenz auf eine
fremde oder nicht vorhandene Annahme ist Startfehler.

Auch diese Transaktion nutzt dauerhaften COMMIT, die Timeout-Grenzen und §6.
Scheitern DB, Kapazität oder Anker, wird kein Job angenommen und kein
vollständig auditiertes Ergebnis behauptet; D1 wird bis zur Klärung gesperrt.
Ein Prozessabsturz **vor** dem Ablehnungs-COMMIT kann den Versuch unbelegt
lassen. Die Kette beweist nur committed Events, nicht jeden empfangenen
Netzwerkversuch; aus einer fehlenden Zeile darf kein Erfolg abgeleitet werden.

Verbrauchte Vorab- und Dienst-Budgeteinheiten werden durch ROLLBACK der
Annahme nicht zurückgegeben. Vor erfolgreicher Signaturprüfung gibt es keine
Dienst-Buchung und keine autoritative D1-Auditmutation; nur Proxy-Limit und
Vorab-Budget werden belastet. Nach erfolgreicher Signaturprüfung zählen auch
Replay-/Zeitablehnungen zum Dienst-Bucket, **nie zu einer Core-Nutzerquote**.
Ein bereits erschöpfter Bucket wird nicht negativ, eröffnet aber auch keinen
unbegrenzten Prüf-/Auditpfad: das globale Vorab-Budget bleibt wirksam.

#### 4.9.4 Höchstzeit, Start-Quarantäne und Restore

```sql
create table d1_clock_state (
    singleton boolean primary key check (singleton),
    max_seen_at bigint not null
        check (max_seen_at > 0 and max_seen_at < 4102444800)
);
```

Die Migrationsrolle legt genau eine Zeile mit geprüfter Core-UTC-Zeit an.
Die Core-Runtime erhält `SELECT` und nur `UPDATE(max_seen_at)`, kein
`INSERT`, `DELETE`, `TRUNCATE` oder DDL. Ein von der Migrationsrolle
kontrollierter Update-Trigger erlaubt nur gleiche oder höhere Werte; die
Runtime kann ihn weder deaktivieren noch umgehen. Portal und Bereinigungsrolle
haben kein Schreibrecht; die Bereinigungsrolle erhält nur `SELECT`.
Fehlende/mehrere Zeilen, falsche Form oder unzulässige Rechte sperren D1.

Bei jeder D1-Zeitprüfung wird ausschließlich die eigene Core-Uhr gelesen:
`floor(UTC-Sekunden)`, niemals `issued_at` eines Clients als Höchstzeit.
Der Prozess vergleicht mit seiner höchsten beobachteten Zeit und prüft die
Fortschreitung gegen seine monotone Uhr. Ein Rücksprung sperrt D1; ein
Vorwärtssprung ist kein Nachweis für abgelaufene Quarantäne. Zusätzlich wird
die Singleton-Zeile in einer eigenen kurzen, synchron committed Transaktion
mit `SELECT ... FOR UPDATE` gelesen. **Nach** dem Lock-Warten wird die Core-
Zeit erneut gelesen. Liegt sie unter `max_seen_at`, wird abgelehnt, nicht
still auf die gespeicherte Zeit geklemmt; sonst wird die Höchstzeit erhöht.

Diese Zeittransaktion läuft auf einer eigenen geprüften Verbindung und wird
vor Fortsetzung der jeweiligen Prüfung bestätigt. Sie enthält keine Job-,
Replay- oder Audit-Annahmeänderungen und wird nicht mit einer abgelehnten
Annahme zurückgerollt. Insbesondere bleiben vor einem späteren Refusal
persistierte Höchstzeiten erhalten. Sie hält keine Sperre der anschließend
laufenden Annahmetransaktion; ein neuer Zeitcheck nach deren Wartezeit darf
nicht an einer selbst gehaltenen Uhr-Sperre hängen. Unbekannter Zeit-COMMIT
sperrt D1. Die einmalige Zeitpflege beim Start braucht keinen Portalrequest.

**Gate 2 ist eine Betriebsgrenze:** NTP-Synchronisierung, verlässliche Core-
Uhr, Monotonieprüfung und persistierte Höchstzeit sind Pflicht. Ein gemeinsames
Zurücksetzen von DB und Hostzeit wird dadurch nicht unabhängig kryptografisch
bewiesen; PostgreSQL ist kein Zeitanker. Einträge in `SECURITY.md` und Tests
folgen im eigenen PR. Die bestehenden Grenzen werden hier nicht geschlossen.

**Gate 3 ist architektonisch entschieden:** Nach jedem Start nimmt D1
mindestens **65 vollständig verstrichene monotone Sekunden** nichts an
(60 s Envelope-Gültigkeit + 5 s erlaubter Vorlauf). Die Frist beginnt erst,
wenn der gültige, eindeutig autoritative DB-Stand und die Start-/Zeitprüfungen
feststehen. Währenddessen keine D1-Annahme und keine nachträgliche Ausführung
gepufferter Requests. Neustart oder Restore beginnt die Frist neu; laufende
Überwachung der Zeit bleibt Pflicht. Damit ist jedes zuvor noch gültige
Envelope nach der Quarantäne abgelaufen, unter der Betriebsannahme aus Gate 2.

Bei Restore/Failover werden alle bisherigen D1-Schreiber zuerst gestoppt bzw.
eindeutig vom Schreiben ausgeschlossen. Eine bloße 65-s-Wartezeit auf einer
neuen Instanz neben einem weiter schreibenden alten Primary reicht nicht.
Der geprüfte DB-Stand wird hergestellt, die vollständige Audit-/Ledger-
Prüfung und der Abgleich mit dem **getrennten** Anker nach §6 erfolgen, dann
die Quarantäne. Ein dem Restore vorausliegender Anker bleibt ein Startfehler;
65 s erlauben weder Audit-Rückschnitt noch Anchor-Reset. Die Quarantäne ersetzt
nur den zuvor geforderten unabhängigen Replay-Fortschrittsnachweis.

Keine automatische Ersatz-/Leertabelle und keine automatische Neuinitialisierung
bei ungewissem Vorzustand. Eine legitimerweise leer bereinigte Tabelle ist nur
mit passender Audit-Historie zulässig. Erstinstallation und Migration bleiben
getrennt freizugebende Schritte, niemals Bestandteil der automatischen
Startmigration. Verlorene oder beschädigte Job-/Auditdaten werden nicht durch
Warten repariert. Mehrere Instanzen oder Wiederöffnung nach Failover brauchen
den gemeinsamen Betriebsnachweis, nicht jeweils unabhängige Freigaben.

#### 4.9.5 Auditierte Bereinigung und Startabgleich

Vorgeschlagen wird eine eigene Rolle `genius_d1_cleanup` unter eigenem
OS-Nutzer, ohne Mitgliedschafts-/`SET ROLE`-Pfad aus `genius_core` oder
`genius_portal`. Sie besitzt weder Tabellen noch DDL-/Trigger-/Truncate-Rechte.
Sie erhält `SELECT`, `DELETE` auf `d1_replay_reservations`, `SELECT` auf
`d1_clock_state` sowie `SELECT`, `INSERT` auf `audit_chain` und `audit_heads`
für den geprüften Append-Pfad. Auf Job-, Acceptance- und Approval-Speicher
bestehen keine Mutationsrechte; auf Ankerspeicher und dessen Zugangsdaten
bestehen keinerlei Rechte. Die Bereinigung erhält keinen privaten Audit-
oder Portal-Schlüssel: die vorhandene `AuditAuthority` liefert den geprüften
signierten Kopf über einen gesondert zu prüfenden Wartungsaufruf.

Nur nach abgeschlossener Quarantäne und bestandenem Zeitcheck sind Zeilen mit
`expires_at < checked_now - 65` Kandidaten. **Genau 65 s** abgelaufen genügt
nicht. `checked_now` stammt aus der Core-Zeitprüfung, nicht aus einem frei
wählbaren SQL-/CLI-Argument. Ein DB-seitiger Delete-Guard verweigert jüngere
Zeilen auch bei direktem DELETE unter der Bereinigungsrolle; er prüft gegen
die persistierte Höchstzeit. Diese liefert nur unter Gate 2 eine gültige
Zeitbasis. Nur die Migrationsrolle kontrolliert diesen Guard. Der D1-Start
prüft ihn ausdrücklich; das Verbot insert-umschreibender Trigger bleibt davon
unberührt.

Ein Batch enthält vorgeschlagen höchstens **20** Zeilen. Zunächst wird der
Uhrcheck separat bestätigt, dann werden unter einheitlicher, dokumentierter
Sperrreihenfolge der Replay-Kapazitätsschutz und der bestehende Audit-Append-
Lock erworben. Kandidaten werden gesperrt und mit ihren Annahmebelegen
abgeglichen. In **derselben** DB-Transaktion werden ausschließlich diese
Zeilen gelöscht und ein `event_version=3`, `action="D1_REPLAY_PURGED"` samt
bereits signiertem Kopf gespeichert. Nach Wartezeiten wird die Zeit erneut
geprüft. Zeitfehler, abweichende DELETE-Rückgabe, Auditfehler oder Timeout:
ROLLBACK des ganzen Batches. Nach DB-COMMIT gilt weiterhin §6; vor bestätigtem
Anker kein nächster Batch und keine Wiederöffnung von D1.

Das geschlossene Bereinigungs-Event bindet zufällige `batch_id`, Actor,
Core-Zeit, Cutoff, Anzahl und eine nach Record-Hash sortierte, duplikatfreie
Liste der **konkreten D1-Annahme-Record-Hashes**. Jeder referenzierte Record
bindet Job, Envelope, Replay-Schlüssel und Ablaufzeit. So bleibt der Beleg
auch bei einer späteren neuen Reservation desselben Nonce eindeutig.
Das Event bleibt einschließlich Metadaten innerhalb der bestehenden
**8192-Byte-Eventgrenze**; die Batchgröße 20 begrenzt auch Locks und WAL-
Spitzen. Der Wartungsaufruf, die neue Eventform und der DB-Guard brauchen vor
Code Claudes Review und Kaans Freigabe; sie existieren noch nicht.

Beim Start gilt bidirektional: Jede verbleibende Replay-Zeile besitzt genau
ein passendes versioniertes D1-Annahmeereignis und den zugehörigen Job. Jede
D1-Annahme besitzt umgekehrt entweder genau diese Replay-Zeile **oder** genau
einen zeitlich zulässigen Bereinigungsbeleg, nie beides. Ein Purge darf kein
noch benötigtes oder schon bereinigtes Ereignis referenzieren. Job-Ledger und
ursprüngliche Audit-Events werden niemals gelöscht; alle übrigen B6-Abgleiche
bleiben bestehen. Fehlende, zusätzliche oder widersprüchliche Bindung sperrt
D1. Ein direktes DELETE ohne Audit kann durch Rollenrechte allein nicht
bewiesen werden, wird aber vom Startabgleich nicht als zulässige Bereinigung
akzeptiert.

#### 4.9.6 Lastbudget — Zahlenvorschlag für Gates 5 und 8

Alle Werte sind **unvermessene Beta-Startwerte zur Freigabe durch Kaan**.
Sie sind weder Lasttest noch garantierter Durchsatz. Bei Rate/Burst bedeutet
`B` eine gesamte sofort verfügbare Kapazität, nicht `B` zusätzliche Requests;
für ein Intervall von `t` Sekunden gilt höchstens `B + r*t/60`. Keine
Warteschlange zur Umgehung von Zeit-/Lastgrenzen. Budgetprüfung und Verbrauch
sind atomar; bereits verbrauchte Einheiten werden bei späterem Fehler nicht
erstattet. Rate-Erschöpfung ist `429`, belegte Kapazität bzw. unbekannter
Speicher-/Budgetzustand `503`; unsicheres Framing schließt die Verbindung.

| Ebene | Vorgeschlagene Grenze | Begründung und genaue Zählung |
| --- | --- | --- |
| Proxy, nur D1, je tatsächlicher Quelladresse | 30/min, Burst 10 | Eine Quelle erreicht dauerhaft höchstens die Hälfte des globalen 60/min-Budgets. Gegenüber dem Dienstlimit 20/min bleiben 50 % Transport-/Fehlversuchsspielraum; Burst 10 fängt kurze Ankunftsspitzen ab, ohne eine Queue aufzubauen. Gemeint ist die TCP-Gegenstelle, bei Vercel gewöhnlich eine Ausgangsadresse, nicht der Browser-Nutzer; keine Autorität aus `X-Forwarded-*`. |
| Proxy, je Quelle | höchstens 8 gleichzeitige Verbindungen/Requests | Das Doppelte der vier D1-Jobplätze erlaubt begrenzte Transport-/Prüfparallelität. Bei HTTP/2 zählen parallele Streams separat. Die Begrenzung muss auch durch Verbindungs-/Header-Timeouts und eine Gesamtverbindungsgrenze ergänzt werden. |
| Kern, D1-Vorab-Budget global | 60/min, Burst 20 | Durchschnittlich ein teurer Prüfversuch pro Sekunde, kurzfristig höchstens 20; drei Mal die Dienst-Dauerrate, vier Mal dessen Burst. Erfasst auch ungültige/unsignierte Requests **nach** bestandenem Ingress, vor JSON-/Digest-/Signaturarbeit. Kein prozessweise vervielfachter Bucket. |
| Kern, verifizierter Portal-Dienst | 20/min, Burst 5 | Höchstens ein neu zugelassener Versuch je drei Sekunden im Mittel. Fünf Sofortversuche reichen für die vier Jobplätze plus einen gegebenenfalls abgelehnten Versuch; erfolgreiche und signaturgeprüft abgelehnte Requests zählen gemeinsam. Kein reservierter Anteil am globalen Budget. |
| D1-Ausführung | höchstens 4 innerhalb `max_in_flight` | Grenzen Prozess-/Speicherparallelität unabhängig von der Rate; vor Reservation beide Kapazitäten nicht blockierend erwerben und auf jedem Ausgang freigeben. Effektiv höchstens `min(4, freie Gesamtplätze)`. Nur wenn die verifizierte Gesamtgrenze mindestens 8 ist, kann D1 höchstens deren Hälfte belegen; vier alleine garantieren keine Reserve. Kein automatisches Erhöhen des bestehenden Limits. |
| Portal, je geprüfter Nutzerreferenz, alle Sessions | höchstens 10 Signierversuche in jedem rollierenden 60-s-Fenster | Höchstens die Hälfte der Dienst-Dauerrate; erlaubt interaktive Nutzung, begrenzt Doppelklick-/Fehlerschleifen. Kein zusätzlicher Nutzer-Burst, keine Buchung anhand einer Browser-ID. |
| Portal, je geprüfter Nutzerreferenz | höchstens 200 Signierversuche in jedem rollierenden 24-h-Fenster | Bewusster Nutzungsdeckel für Kaans Einpersonen-Beta; verhindert eine Dauerlast von 14 400 Versuchen/Tag bei ausgeschöpften 10/min. Kein Zahlungs-/Tokenkostenlimit und kein Nachweis der Auditkapazität. |
| Portal, global vor jedem Signieren | 20/min, Burst 5 | Gleiche Hülle wie der Core-Dienst-Bucket, damit horizontale Vercel-Instanzen nicht unbegrenzt signieren. Der Kern prüft trotzdem unabhängig; unterschiedliche Ankunftszeiten können zusätzliche Ablehnungen verursachen. |
| Replay-Tabelle | Annahmestopp bei mindestens 100 000 Zeilen; Warnung ab 80 000 | Enthält auch noch nicht bereinigte abgelaufene Zeilen. Bei 20/min wären 100 000 ohne Bereinigung rechnerisch nach knapp 3,5 Tagen erreicht; 20 000 Warnabstand entsprechen etwa 16,7 h bei dieser Dauerrate. Sicherheits-Notstopp bei ausgefallener Bereinigung, kein RAM-/DB-Kapazitätsnachweis. |
| Nutzbarer freier DB-Speicher | Annahmestopp unter 2 GiB; Warnung unter 4 GiB | Absolute Mindestreserve für WAL, Audit und verbleibende Jobübergänge; die Warnung bei 4 GiB lässt nochmals eine solche Reserve als Reaktionspuffer. 2 GiB = 2 147 483 648 Bytes; genau 2 GiB löst diese eine Grenze noch nicht aus. Der sichere Bedarf kann größer sein und muss gemessen werden. |

**Persistenz und Fehlversuche:** Core-Vorab-/Dienst-Bucket und Portal-Budgets
brauchen gemeinsamen, neustartfesten Zustand in ihrer jeweiligen Datenbank;
keine neue DB-Technologie und kein alleiniger Vercel-Prozessspeicher. Core-
Buckets haben feste konfigurierte Schlüssel, atomaren Verbrauch und eine
unabhängig von der Jobtransaktion bestätigte Buchung. Fehlender/alter oder
nicht erreichbarer Zustand bedeutet REFUSE, kein voller Default-Bucket.
Ein Restore dieser Zustände braucht einen eigenen geprüften Wiederanlauf;
65 s Replay-Quarantäne stellen insbesondere kein verlorenes 24-h-Portalbudget
wieder her. Die Tabellen aus §5.4 mit HOUR/MONTH bilden die vorgeschlagenen
rollierenden Signierlimits noch nicht ab und werden hier nicht umdefiniert.

Das Portal bucht einen nach Sessionprüfung identifizierten Signierversuch
vor dem Signieren atomar über alle Instanzen und Sessions gegen Nutzer- und
Gesamtbudget. Scheitern Signieren oder Transport danach, bleibt der Verbrauch
bestehen. Eine durch bereits ausgeschöpfte Quote abgelehnte Anfrage erzeugt
keine Signatur und setzt den Zähler nicht zurück. Ein Core-Replay-/Zeitfehler
löst keine zusätzliche Nutzerbuchung aus und erstattet auch nicht den bereits
im Portal gezählten Signierversuch. D1 liefert dem Kern keine Nutzerquote.

**Keine Verfügbarkeitsgarantie:** wechselnde IPs umgehen ein einzelnes
Adressbudget; deshalb muss Gate 10 **vor** dem gemeinsamen Core-Budget greifen.
Ein kompromittiertes zugelassenes Portal kann den Dienst weiterhin aushungern.
Auch vollständig verifizierte, aber vom Dienst-Bucket abgelehnte Versuche
können Ablehnungs-Audit schreiben: dessen Spitzenlast ist daher bis zur
60/min-/Burst-20-Hülle des Vorab-Budgets zu messen, nicht nur mit 20/min.

**Speicherprüfung:** Vor jedem neuen Annahmeversuch werden physische Größe
von Replay-Tabelle einschließlich Indizes/TOAST, Zeilenzahl, Audit-/Anker-
Kapazität und nutzbarer freier Platz auf allen beteiligten Daten-, Tablespace-
und WAL-Dateisystemen geprüft. `pg_total_relation_size` liefert belegte
Relationbytes, **keinen freien Dateisystemplatz**; letzterer kommt aus einem
vertrauenswürdigen, nur lesenden Betriebssensor, vorgeschlagen höchstens 1 s
alt. Fehlender/unlesbarer/veralteter Sensor sperrt D1, nicht die Auditpflicht.
Die 1-s-Probe ist keine Reservierung: die Reserve muss zusätzlich gemessene
WAL-/DB-Spitzen bis zur nächsten Probe und alle bereits laufenden Jobs decken.
Reicht 2 GiB dafür nicht, muss die Stoppschwelle höher liegen.

Die Zeilenzahl wird unter einer gemeinsamen transaktionsgebundenen
Kapazitätssperre auf dem Primary geprüft; alle D1-Inserts und die Bereinigung
benutzen dieselbe Sperre. Bei 99 999 Zeilen kann noch genau ein neuer Insert
gewinnen; bei 100 000 keiner. Statistikschätzungen sind kein harter Nachweis.
DB-Owner-Manipulation bleibt außerhalb dieser Konkurrenzgarantie und wird
nicht durch das Limit legitimiert.

Audit-Eventgrenze (8192 Bytes), Ankernachricht (64 MiB) und Ankerzustand
(16 MiB) bleiben unverändert und können **vor** 100 000 Replay-Zeilen binden.
D1 muss vor Annahme auch Platz für die maximal noch nötigen Folgeereignisse
bereits laufender Jobs und den eigenen Annahmepfad nachweisen; die Umsetzung
darf nicht erst beim finalen Audit-Append vollen Speicher entdecken. Kein
Löschen von Audit/Job-Ledger und kein Anker-Reset zum Weiterarbeiten.
PostgreSQL-DELETE gibt zudem nicht sofort Dateisystemplatz zurück; reguläres
VACUUM dient überwiegend der Wiederverwendung. `VACUUM FULL`, Rechteänderungen
oder manuelle Bereinigung sind kein automatischer Notfall-Fallback.

#### 4.9.7 Geschützter Eingang — Vorschlag für Gate 10

**Empfehlung für die Beta:** TLS mit geprüftem Servernamen plus ein separates,
zufällig erzeugtes **256-Bit-Ingress-Secret** im Header `X-GeniusNew-Ingress`.
Es ist unabhängig von D1-Ed25519-Schlüssel, Core-Root-Secret und Bearer-Keys.
Die 256 Bit vermeiden menschliche Passwörter und geben genügend Entropie gegen
Raten; sie sind kein Ersatz für Zugangskontrolle und sichere Speicherung.
Vercel verwendet standardmäßig dynamische Ausgangsadressen. Optionale Static
IPs existieren, sind aber keine vorausgesetzte Projektfunktion und ersetzen
keine Authentisierung. Insbesondere werden nicht pauschal Cloud-IP-Bereiche
als Portal-Identität freigegeben.

Ablauf: Portal-Session und persistente Signierbudgets prüfen → D1 signieren →
TLS-Verbindung zum festen D1-Ingress → billige Größen-/Quellgrenzen und
Ingress-Prüfung am Proxy → erst dann Weiterleitung zum Core-Vorab-Budget →
unabhängige Ed25519-, Policy-, Kapazitäts-, Replay- und Auditprüfungen im Kern.
Ein richtiges Ingress-Secret allein darf niemals einen Job autorisieren.

Der Proxy verlangt genau einen Ingress-Header, ASCII-case-insensitiv gezählt,
mit festem Format und konstanter Vergleichszeit. Fehlend, falsch, doppelt
(auch gleichwertig), zusammengeführt oder unklar bedeutet Ablehnung **ohne
Upstream-Aufruf**. Ein einfacher Zugriff auf einen bereits zusammengeführten
nginx-Headerwert ist kein Singleton-Nachweis. Die tatsächlich gewählte
Proxy-Implementierung muss das Verhalten belegen, sonst bleibt Gate 10 offen.

Das Secret liegt nur in serverseitiger Vercel-Laufzeitkonfiguration und im
geschützten Proxy-Zugangspfad, nie im Browser, Repository, Build-Ausgabepaket,
in URLs, Logs oder Traces. Keine Weitergabe an frei wählbare Ziele und kein
Redirect-Following. Preview-/Testumgebungen erhalten kein Produktions-Secret.
Rotation braucht Kaans gesonderte Freigabe, begrenzte Übergangszeit und einen
Test, dass das alte Secret danach abgewiesen wird. Dieser Entwurf erzeugt
oder ändert kein echtes Secret.

Um Gate 4 nicht durch eine neue Header-Ausnahme zu umgehen, bleibt auch der
Ingress-Header bei Weiterleitung unverändert und wird nicht als Core-
Autorität benutzt; seine Bytes zählen zur 8192-Byte-Headergrenze. Der Kern
prüft die rohe Liste einschließlich dieses neuen Headers auf Duplikate,
ohne den Secretwert zu speichern oder zu protokollieren. `Authorization` und
`X-GeniusNew-D1-Envelope` werden weder entfernt noch zusammengeführt; Misch-
Auth bleibt REFUSE. Diese zusätzliche Transportheader-Regel muss #159
explizit übernehmen. Keine Header-/Body-/Pfadnormalisierung als Abkürzung.

Der Core bleibt nur über den geprüften lokalen Proxy erreichbar. Alle
anderen virtuellen Hosts, Routen und direkten Bearer-Zugänge müssen D1-
Requests verweigern; Loopback allein beweist keine Isolation von anderen
lokalen Prozessen. Der lokale Zugang und die unveränderte Headerweitergabe
sind gesondert nachzuweisen. Ein anonymer Internetrequest darf den Core-
Bucket nicht belasten. TLS-/Leitungsüberlastung vor der Ingress-Prüfung und
der Verlust des Ingress-Secrets sind dadurch nicht ausgeschlossen.

**Alternative mTLS:** Der Proxy verlangt verpflichtend ein gültiges Client-
Zertifikat, beschränkt auf den vorgesehenen Portal-Dienst, mit geprüfter
Kette, Gültigkeit, Identität und Widerrufs-/Rotationsverfahren. Kein optionales
Clientzertifikat und kein Secret-/Bearer-Fallback. Der Vercel-Python-Client
muss Zertifikat und privaten Schlüssel ausschließlich serverseitig laden und
das Core-Serverzertifikat weiter prüfen. In dieser Variante entfällt der
Ingress-Secret-Header, nicht die D1-Signatur. Kompatibilität und Betrieb sind
noch nicht geprüft; keine Umstellung durch dieses Dokument.

**nginx-Grenzen:** `limit_req` verwendet Leaky-Bucket-Semantik; der konkrete
`burst`-Parameter muss auf die oben definierte Gesamtkapazität abgebildet und
an der Grenze geprüft werden (keine stillschweigende zusätzliche Anfrage).
`nodelay` vermeidet verzögerte Warteschlangen. `limit_conn` zählt erst nach
vollständig gelesenen Headern; acht in `limit_conn` belegen deshalb nicht
acht insgesamt offene TLS-/Slow-Header-Verbindungen. Der Betriebsnachweis
muss diese frühe Phase separat mit harten Verbindungsgrenzen und Zeitlimits
abdecken. Bestehende Proxy-Vorlagen sind nicht automatisch D1-fähig.

#### 4.9.8 Abnahme und Quellen

Gate 7 ist gemäß Entscheidung zulässig, **kein allgemeiner Schema-Fingerprint**:
Vor D1-Freigabe werden Migration, tatsächliche Zieltabelle, nicht deferrable
Primary Key, Fremdschlüssel/Zeilenform, effektive Rechte und Default-Privileges
geprüft; ebenso die kontrollierten Clock-/Delete-Guards sowie das Fehlen
insert-umschreibender Trigger, Rules, RLS-Policies und nicht freigegebener
Partitionen/Kindtabellen. Unklare Katalogwerte sind REFUSE. Die bestehenden
Core-Startprüfungen und die Anker-Trennung bleiben vollständig erhalten.

Für die spätere Umsetzung verlangt der Entwurf reproduzierbare Tests:

- Annahme-Race mit gleicher Nonce, verschiedene Schlüssel derselben Domäne,
  Fehler/Crash vor und nach jedem COMMIT/Anchor-Schritt: niemals eine zweite
  Ausführung oder getrennt sichtbare Replay-/Job-/Audit-Annahme.
- Ablehnung nach Signatur, Nonce-Konflikt, Ablauf und unbekannter COMMIT-Ausgang;
  fehlendes/fremdes Ablehnungs- oder Purge-Event; v1/v2 unverändert lesbar nur
  nach ihren bisherigen Regeln, fremde v3-Feldkombinationen abgelehnt.
- Quarantäne bei 64,999 s und ab vollständig 65 s, wiederholter Neustart,
  alter Restore, parallel aktiver alter Primary, Uhr-Rück-/Vorwärtssprung;
  fehlende Höchstzeit, verbotener Rückwärts-UPDATE und kein Zurückrollen einer
  bereits bestätigten Höchstzeit mit der Jobtransaktion.
- Bereinigung genau 65 s und länger abgelaufener Zeilen, direkter DELETE einer
  jungen Zeile, fehlender Batch-Audit, Crash-Fenster, spätere neue Reservation
  desselben Nonce; Runtime-/Portalrechte auch über indirekte Grant-Pfade.
- Alle Rate-/Burst-/Fenstergrenzen und parallele Vercel-Sessions, mehrere
  Core-Instanzen, Neustarts und Budget-Restore, voller/fehlender Budgetzustand,
  vier/fünf D1-Jobs und unverändertes Gesamtlimit; keine Budget-Rückerstattung
  durch Fehler. Lasttest inklusive 60/min signaturgeprüfter Ablehnungen.
- 99 999/100 000 Replay-Zeilen unter Konkurrenz, 2 GiB und ein Byte darunter,
  ausgefallene Bereinigung, alte/unlesbare Messung, Audit-/Ankergrenze vor
  Replay-Grenze sowie verbleibende Auditfähigkeit laufender Jobs.
- Ingress-Secret fehlend/falsch/doppelt: **null** Upstream-Aufrufe und null
  Core-Budgetverbrauch; korrektes Secret plus falsche D1-Signatur: kein Job.
  Direkter Core-/Alternativrouten-Bypass, Header-Duplikate, HTTP/2-Streams,
  Slow-Header-Verbindungen, Secretfreiheit von Logs und getestete Rotation.

Gate 3/7 sind Entscheidungen, keine bestandenen Laufzeittests. Gates 5/10
bleiben bis Freigabe der Zahlen und tatsächlich ausgeführtem Last-/Ingress-
Nachweis offen. Gate 9 braucht den Abgleich mit #159 und Claudes unabhängigen
Review der neuen Eventformen; Gate 6 verlangt den Review am exakten Head.
Kaan gibt den Entwurf frei und mergt. Hier werden weder D1 aktiviert noch
Code, Migrationen, Produktivrollen, Proxy-Konfiguration oder Secrets geändert.

Primärquellen für die Produkt-/Betriebsangaben (geprüft 18.10.2026; keine
Quelle belegt die Eignung unserer vorgeschlagenen Lastzahlen):

- [Vercel: dynamische Ausgangsadressen und optionale Static IPs](https://vercel.com/kb/guide/can-i-get-a-fixed-ip-address).
- [nginx: Request-Limits und Burst](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html),
  [gezählte Verbindungen/HTTP-Streams](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html),
  [Clientzertifikat-Prüfung](https://nginx.org/en/docs/http/ngx_http_ssl_module.html#ssl_verify_client).
- [PostgreSQL 17: Größenfunktionen](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-ADMIN-DBSIZE),
  [VACUUM und Speicherwiederverwendung](https://www.postgresql.org/docs/17/routine-vacuuming.html).

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

Für einen späteren aktiven D1-Pfad kommen vor Schritt 10 die Prüfungen aus
4.9 hinzu: D1-Schema und Rechte, persistierte Höchstzeit, bidirektionale
Replay-/Job-/Audit-Bindung einschließlich zulässiger Bereinigung sowie
65 s Start-Quarantäne nach jedem Start/Restore. Sie ersetzen für Replay den
bisher geforderten unabhängigen Fortschrittsnachweis, nicht den bestehenden
Core-/Anker-Abgleich. Ohne erfüllte Betriebs- und Freigabegates bleibt D1
gesperrt; kein stilles Zurücksetzen oder Abschwächen der Core-Startprüfung.

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

Eine spätere, separat freizugebende D1-Migration legt die Tabellen und
kontrollierten Clock-/Delete-Guards aus 4.9 an. Rollen und Grants gehören zum
eigenständig freizugebenden Installationsablauf.
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
- **`genius_portal`:** nur Portal-Tabellen, ohne Zugriff auf Core-Tabellen;
- **`genius_d1_cleanup` (Vorschlag):** eigene Wartungsrolle für den auditierten
  Bereinigungspfad aus 4.9.5, ohne erreichbaren Rollenwechsel aus einer Runtime,
  ohne Job-/Approval-Mutationsrechte und ohne Ankerzugriff.

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
| `d1_clock_state` (erst nach freigegebener D1-Migration) | SELECT, UPDATE(max_seen_at), nur nicht abnehmend; kein INSERT/DELETE/TRUNCATE/DDL |

Für `genius_d1_cleanup` gelten separat ausschließlich die Rechte aus 4.9.5:
Replay SELECT/DELETE hinter dem Delete-Guard, Clock SELECT, Audit-/Kopf-
SELECT/INSERT im kontrollierten Append-Pfad. Keine vererbten Zusatzrechte,
kein Eigentum, kein TRUNCATE/DDL und kein privater Signierschlüssel. Diese
Rolle erweitert nicht die Rechte von `genius_core` oder `genius_portal`.

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
