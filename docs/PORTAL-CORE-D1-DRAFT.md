# D1: Portal–Kern-Vertrag (Entwurf zur unabhängigen Prüfung)

**Status: HOLD.** Grundlage ist [Issue #92](https://github.com/Kaancodm/GeniusNew/issues/92).
Dieser Text aktiviert keine Route. Die offenen Entscheidungen und Security-Gates am
Ende sperren aktiven D1-Code.

## Identität und Autorität

Der Browser liefert allein den Auftragstext. `subject`, `tier`, `user_id`, `tools`,
`job_id`, Approver-Identität und ähnliche Autoritätsfelder im Body werden verweigert.
Der Kern erzeugt die Job-ID. Der private Ed25519-Schlüssel bleibt beim Portal-Backend;
der Kern hält nur den öffentlichen Schlüssel. TLS mit geprüftem Servernamen bleibt
Pflicht.

Der verifizierte D1-Schlüssel authentisiert den **Portal-Dienst**. Der Kern bindet
`(issuer, audience, key_id)` in eigener Konfiguration an einen dedizierten
Service-Principal. Portal-Login, Session und Portalrolle werden dadurch **nicht** zur
Core-Nutzeridentität. Der gemeinsame Service-Principal beweist weder Job-Eigentum einer
Einzelperson noch deren Approval-Berechtigung oder individuelle Quote.

**D3-Zielabbildung zur Entscheidung durch Kaan:** Der Portal-Server müsste eine aus der
geprüften Session ermittelte, stabile Portal-Nutzerreferenz als eigene signierte Aussage
liefern. Der Kern müsste `(verifizierter Portal-Dienst, Portal-Nutzerreferenz)` in einer
**Core-seitig verwalteten** Registry eindeutig auf einen Core-`subject` und eine
kanonische Personen-ID (`user_id` im heutigen Policy-Grant) abbilden. Unbekannte,
mehrdeutige, deaktivierte oder nicht mehr gebundene Referenzen werden verweigert.
Browserwerte und unsignierte Header sind keine Autorität. Dienst- und Nutzeridentität
bleiben getrennt prüfbare Fakten; die Dienstsignatur allein genügt nicht.

Auftraggeber und Approver werden Core-seitig an ihre kanonischen Personen-IDs gebunden.
Ein Approver braucht weiterhin C3s serverseitige Approver-Rolle, Policy-Grant und
Einmal-Token. Stimmen die IDs überein, wird die Freigabe auch bei verschiedenen
Sessions, Schlüsseln, Subjects oder Portal-Konten verweigert. Alle Aliasse einer
natürlichen Person müssen dieselbe Core-Personen-ID haben; ohne verlässliche Verknüpfung
wird die Freigabe verweigert. Job-Eigentum, Verlauf, Nutzer-Token-Buckets und Quoten
beziehen sich auf die gebundene Core-Identität; ein zusätzlicher Dienst-Bucket begrenzt
das Portal insgesamt.

**HOLD D3:** Kaan muss entscheiden, welche Instanz die kanonische Personen-ID ausstellt
und wie Portal-Konten, direkte API-Keys und Approver-Subjects derselben natürlichen
Person verlässlich zusammengeführt und gesperrt werden. Danach brauchen Nutzer-Aussage
und Core-Registry einen eigenen versionierten Vertrag. Das unten geschlossene
D1-Envelope enthält **keine** Nutzer-Aussage und darf nicht still um `user_id` ergänzt
werden. D1 begründet bis dahin keine nutzergetrennten Jobs oder Freigaben. C3 bleibt
unverändert.

## Bytegenaues Signaturprofil

Das Envelope enthält **genau** elf Felder; `signature` gehört nicht dazu. Konfigurierte
Bezeichner sind ASCII-Strings nach `^[A-Za-z0-9._-]{1,64}$`, ohne
Unicode-Normalisierung.

| Feld | JSON-Typ und Wert |
| --- | --- |
| `version` | String `geniusnew-portal-core-d1` |
| `kind` | String `REQUEST` |
| `issuer` | String, konfigurierte Portal-Dienst-ID, 1–64 ASCII-Zeichen |
| `audience` | String, konfigurierte Core-Zielinstanz-ID, 1–64 ASCII-Zeichen |
| `key_id` | String, 1–64 ASCII-Zeichen; Schlüsselwahl nur aus begrenzter Core-Konfiguration |
| `nonce` | String, exakt 64 lowercase Hex-Zeichen = 32 zufällige Bytes |
| `issued_at` | echter JSON-Integer, Unix-Sekunden UTC |
| `expires_at` | echter JSON-Integer, Unix-Sekunden UTC |
| `method` | String `POST` |
| `path` | String `/jobs` |
| `body_sha256` | String, exakt 64 lowercase Hex-Zeichen des SHA-256 der Body-Bytes |

`issuer`, `audience` und `key_id` müssen den exakt konfigurierten Werten entsprechen.
Fremde IDs dürfen weder Schlüssel nachladen noch einen Principal wählen. Die stabile
Replay-Domäne lautet ASCII `geniusnew.portal-core.request.d1`; ein Nonce bleibt über
Schlüsselwechsel hinweg verbraucht.

Signierte Bytes sind exakt:

```text
ASCII("geniusnew/portal-core/request/ed25519/d1") || 0x00 || envelope_bytes
```

Das Label ist `b"geniusnew/portal-core/request/ed25519/d1\x00"`; `||` ist direkte
Verkettung ohne Länge, Zeilenumbruch oder weiteres Trennzeichen. Ed25519
signiert diese Bytes. Die 64 Signatur-Bytes werden als **128 lowercase Hex-Zeichen**
dargestellt. Signatur und Transport-Trennzeichen sind weder Teil von `envelope_bytes`
noch der signierten Bytes.

`envelope_bytes` sind höchstens 4096 Bytes lang. Der Body ist höchstens 16384 Bytes lang
und enthält genau `{"text":<nichtleerer JSON-String>}`. Für Body und Envelope gilt:
ASCII JSON mit `ensure_ascii=True`, lexikografisch sortierten Schlüsseln und `,`/`:`
ohne Whitespace. Zahlen sind ausschließlich dezimale Integer ohne Exponent; Floats,
`NaN`, `Infinity`, `null` und Booleans an Integerstellen sind verboten. Strings
verwenden die Escapes der Python-`json.dumps`-Kanonform, insbesondere lowercase `\u`-Hex
und für Supplementary-Zeichen ein korrektes Surrogate-Paar. **Keine
Unicode-Normalisierung:** NFC und NFD bleiben verschiedene Bytes. Ungültige
Unicode-Skalare, einschließlich isolierter High/Low-Surrogates, sind verboten.

Body- und Envelope-Parser verweigern doppelte Schlüssel beim Parsen, danach unbekannte
Felder und falsche Typen. Vor der Signaturprüfung muss die erneute Kanonisierung des
vollständig geprüften Werts **bytegleich** zum empfangenen JSON sein.
`geniusnew.contracts.canonical()` allein prüft weder D1-Feldform noch doppelte Schlüssel
oder isolierte Surrogates und ist kein vollständiger D1-Validator.

### Cross-Language-Vektoren

Für alle gültigen Vektoren gelten `issuer="portal-test"`, `audience="core-test"`,
`key_id="key-1"`, `nonce="00"` 32-mal (nur Testwert), `issued_at=1700000000`,
`expires_at=1700000060`, `method="POST"`, `path="/jobs"`. `body_sha256` ist der Digest
der exakt angegebenen ASCII-Bytes. `signing_sha256` ist der Digest von Label plus
kanonischem Envelope; er prüft Cross-Language-Bytes und ist keine Ed25519-Signatur.

| Fall | Exakte Body-Bytes als ASCII-Byte-Literal | `body_sha256` | `signing_sha256` |
| --- | --- | --- | --- |
| ASCII | `b'{"text":"hello"}'` | `cbbbdcd27692344de5dbab3abcaba413fb0f45307267de7081401576df1cb176` | `bbb318bbf252e9a208bfd24cfe3e5042fe49454cc6fff18829fdf0dace942451` |
| Grüße🙂 | `b'{"text":"Gr\u00fc\u00dfe\ud83d\ude42"}'` | `c3fc136890181afc0a1556efa7c691fc43647df9681f2d7c6c4a7003704cc87b` | `eba419e4702e84ee69a4eeb1c7d430e696b27808a194c11220d4fffff4ab5692` |
| NFC `é` | `b'{"text":"\u00e9"}'` | `d804508ba860f371e3f4ad571e0b760745ca1347987ba8ad41cb3eef8b4c284f` | `3e243422d6ac0b0fd130eec90a15e4d598c5047c4aabe92bd289bca0aa731fa6` |
| NFD `e` + U+0301 | `b'{"text":"e\u0301"}'` | `2169722aee46a09ad91bae5ad6e5eb68e327438cdf02e2e04fe951f97ba466e8` | `859b78533b08d856a4278a7c4f7511c3703463fa2bec2ffe968dacff5aa0a7db` |
| Kombinierend `a` + U+0308 + U+0323 | `b'{"text":"a\u0308\u0323"}'` | `f2ef9205b9a0f94cc9c4ca4c518c89c48a97b2af2e7d14f773c369b02ed6d1e3` | `15ac67c3d88d3ee34b35fa107fd99bdc8939ce28c1b660d765852f571b451897` |
| Supplementary U+10437 | `b'{"text":"\ud801\udc37"}'` | `a33bf7da501d83aae5429ed29cc176ebc725b200ab5d627ab54ce80dc38e7e28` | `94b5575766dd9e35ff89c372ca0c458295746146842cab02bd75052b681ed029` |

Das Envelope jedes Falls ist die ASCII-Kanonisierung der elf Felder mit seinem
`body_sha256`. Der ASCII-Fall lautet vollständig (352 Bytes ohne
Code-Block-Zeilenumbruch):

```json
{"audience":"core-test","body_sha256":"cbbbdcd27692344de5dbab3abcaba413fb0f45307267de7081401576df1cb176","expires_at":1700000060,"issued_at":1700000000,"issuer":"portal-test","key_id":"key-1","kind":"REQUEST","method":"POST","nonce":"0000000000000000000000000000000000000000000000000000000000000000","path":"/jobs","version":"geniusnew-portal-core-d1"}
```

Vorlage, Feldwerte und Body-Digests bestimmen die übrigen Envelope- und Signierbytes
eindeutig. Negative Vektoren: `b'{"text":"\ud801"}'` (isolierter Surrogate),
`b'{"text":"\u0068ello"}'` (alternativer Escape statt `hello`),
`b'{"text":"hello","tier":"admin"}'` (Zusatzfeld) und `b'{"text":"a","text":"b"}'`
(doppelter Schlüssel) sind **REFUSE**. Dasselbe gilt für alternative Envelope-Escapes,
doppelte oder zusätzliche Envelope-Felder.

## HTTP- und Proxy-Grenze

Der einzige D1-Header heißt exakt `X-GeniusNew-D1-Envelope`. Sein einzelner ASCII-Wert
ist `<base64url-ohne-Padding(envelope_bytes)>.<signature-hex>`: URL-safe Base64 mit
Alphabet `A-Z a-z 0-9 _ -`, ohne `=`, gefolgt von genau einem ASCII-Punkt und 128
lowercase Hex-Zeichen. Der Decoder verwirft andere Zeichen, Padding und nicht identisch
wieder kodierbare Werte. Maximal 5600 ASCII-Oktette für den Headerwert, maximal 8192
Oktette für die gesamte empfangene Headerliste. Genau eine Instanz ist erlaubt.

Headernamen werden auf der **ursprünglichen HTTP-Headerliste vor jeder
Mapping-/Dictionary-Bildung** ASCII-case-insensitiv auf Duplikate geprüft. D1 verweigert
insbesondere doppelte `Host`, `Authorization`, `Content-Type`, `Content-Length`,
`Transfer-Encoding`, `Content-Encoding`, `Trailer` oder `X-GeniusNew-D1-Envelope`, auch
bei gleichen Werten. Kommagetrennt zusammengeführte Werte ersetzen diese Prüfung nicht.
Obs-fold, Whitespace vor dem Doppelpunkt und nicht-ASCII-Headernamen werden vor der
Listenbildung verweigert.

| Eingang | D1-Regel |
| --- | --- |
| Request-Zeile / `:path` | exakt `POST /jobs`; origin-form, kein Query `?`, Fragment `#`, Prozentkodierung, absolute URI, Dot-Segment, Suffix oder Redirect |
| `Host` / `:authority` | genau ein Wert, bytegleich zur konfigurierten Core-Authority; keine Alias-, Port- oder Case-Normalisierung als Autorität |
| `Content-Type` | genau einmal, exakt ASCII `application/json`, keine Parameter oder Mehrfachwerte |
| `Content-Length` | genau einmal, ASCII-Dezimalzahl ohne Vorzeichen oder führende Null, 1–16384; mehrere Werte auch bei Gleichheit REFUSE |
| `Transfer-Encoding` | jede Instanz REFUSE, auch `chunked`; zusammen mit `Content-Length` ebenfalls REFUSE |
| `Content-Encoding` und `Trailer` | jede Instanz REFUSE; keine Dekompression oder Trailer-Verarbeitung |
| Body | exakt `Content-Length` Bytes; verkürzter oder überzählig erkennbarer Body REFUSE und Verbindung schließen |
| `Authorization` | bei D1 jede Instanz REFUSE, unabhängig von Gültigkeit; Bearer und D1 dürfen nie gemeinsam erfolgreich sein oder als Fallback dienen |

Nach jedem D1-Request wird die Core-Verbindung geschlossen; zusätzliche gepufferte Bytes
werden nie als nächster Request geparst. Ein unsicher gerahmter, unvollständiger oder
nicht vollständig konsumierbarer Request schließt die Verbindung ohne Verarbeitung.
HTTP/2-Framing muss ein vorgeschalteter Proxy eindeutig terminieren. **Tatsächlich
empfangene Body-Bytes** sind die HTTP-Inhaltsbytes, die der Core-Adapter als
Request-Body erhält: keine HTTP/1.1-Chunk-Marker und keine HTTP/2-Frames. D1 verbietet
Content-Encoding; der Digest gilt daher den unveränderten Inhaltsbytes.

Ein Proxy darf Body, Methode, Pfad oder Authority nicht umschreiben und mehrdeutige
Framingformen nicht still normalisieren. Umgeschriebene Pfade und Proxy-Override-Header
wie `X-Original-URL`/`X-Rewrite-URL` werden verweigert; `Forwarded`-/`X-Forwarded-*`
liefern keine Autorität. Der Portal-Client folgt keinem Redirect; der Core-D1-Pfad
antwortet nicht per Redirect. Proxy- und TLS-Konfiguration sind gesonderte Betriebsgates
vor Deployment.

Die vorhandene direkte Bearer/API-Key-Route bleibt ein eigener Authentisierungspfad. Ein
D1-Header auf einer direkten Route, ein `Authorization`-Header auf D1 oder eine nicht
eindeutig zuordenbare Mischform ist REFUSE. Der heutige Handler bildet Header mit
`dict(self.headers.items())`; die D1-Implementierung muss deshalb vor dieser Stelle die
rohe Liste prüfen und darf die Mapping-Schnittstelle nicht als Duplikatnachweis
verwenden.

## Zeit, Replay und Commit

`UNIX_2100 = 4102444800`. Beide Zeiten sind echte Integer, keine Booleans:

```text
0 <= issued_at < expires_at < UNIX_2100
expires_at - issued_at <= 60
issued_at - 5 <= now < expires_at
```

Die letzte Zeile ist das **noch von Kaan zu bestätigende** Fünf-Sekunden-Profil. Die
fünf Sekunden gelten nur vor `issued_at`; nach `expires_at` gibt es keine Nachfrist.
Bis Kaan fünf Sekunden **JA oder NEIN** entschieden hat, bleibt das Zeitprofil HOLD.
Bei NEIN muss der Vertrag vor Code mit `issued_at <= now < expires_at` revidiert
werden.

Der Kern liest `now` erstmals nach vollständiger HTTP-/Body-/Envelope-Prüfung und
unmittelbar vor der Signaturprüfung. Nach verifizierter Signatur und vorgelagerten
Last-/Policy-Gates liest er `now` erneut unmittelbar vor dem Replay-INSERT. Nach jeder
DB-/Lock-Wartezeit und vor COMMIT wird neu gelesen; bei Ablauf wird die Transaktion
zurückgerollt. Nach bestätigtem COMMIT und **vor** Erzeugen der Job-ID wird nochmals
gelesen; bei Ablauf bleibt der Nonce verbraucht und es entsteht kein Job. Ein Request
wird so nicht wegen langer DB-Wartezeit nach Ablauf angenommen.

Ein Rücksprung der Core-Uhr unter einen zuvor beobachteten Zeitpunkt stoppt D1-Aufnahmen
fail closed. Die Rollback-Erkennung muss auch über Neustarts und alle Core-Instanzen
hinweg funktionieren. Dafür ist noch kein freigegebener unabhängiger Zeitnachweis
definiert: **Security-Gate vor aktivem Code**. Nonces werden durch Uhr-Rücksprung
niemals freigegeben.

Der bestehende Connection-Limit-Gate greift bereits beim Annehmen der Verbindung;
Header- und Body-Limits greifen vor teurer JSON-/Signaturarbeit. Verbindliche
Reihenfolge eines D1-Requests:

1. HTTP-Framing, Header, Route, Größen, vollständigen Body und kanonische JSON-Form
   prüfen; `method`/`path` mit der tatsächlich empfangenen Route und
   `body_sha256` mit den Body-Bytes vergleichen; keine Job-/Audit-Mutation.
2. Erste Zeitprüfung mit der Core-Uhr.
3. Den konfigurierten Ed25519-Schlüssel wählen und Signatur prüfen.
4. Service-Identität, später zusätzlich Core-Nutzerbindung, Rate Limits, Token-Buckets,
   Quoten, `max_in_flight` und Policy-Grants am bestehenden Eingang prüfen; die
   Kapazitätsgrenze vor der Reservation erwerben. Kein Aufruf von `_dispatch()` unter
   Umgehung dieser Gates.
5. Unmittelbar vor der Replay-Reservation Zeit erneut prüfen, dann den Schlüssel
   `(domain, issuer, nonce)` in der Core-PostgreSQL-Tabelle einzufügen versuchen.
6. Nur wenn **diese** Transaktion exakt einen neuen Datensatz eingefügt hat
   (`INSERT ... RETURNING` oder äquivalenter Zeilennachweis), Zeit nach Wartezeit
   erneut prüfen und dauerhaften COMMIT bestätigen. `ON CONFLICT DO NOTHING` mit
   null eingefügten Zeilen ist REFUSE, auch bei erfolgreichem COMMIT.
7. Nach bestätigtem COMMIT und letzter Zeitprüfung erst die Job-ID erzeugen.
8. Danach erst Handoff, Job-Ledger und Audit verändern, über die bestehenden Approval-,
   Audit- und PostgreSQL-Gates. Kein Worker läuft in der Replay-Transaktion. Die
   erworbene `max_in_flight`-Kapazität wird auf jedem Ausgang wieder freigegeben.

Für die Replay-Transaktion sind `synchronous_commit=on`, `fsync=on`,
`full_page_writes=on` und tatsächlich dauerhafte WAL-Speicherung Pflicht; asynchroner
COMMIT oder Failover auf einen Stand ohne bestätigte Reservation ist unzulässig. Der
konkrete PostgreSQL-/Failover-Nachweis ist ein **Betriebsgate**. Bei DB-Ausfall, vollem
Speicher, Konflikt, unbekanntem COMMIT-Ausgang oder Verbindungsabbruch während COMMIT:
kein Job, kein Erfolg, kein automatischer Retry desselben Requests.

Nach bestätigter Reservation, aber vor Job-Erzeugung, bleibt der Nonce bei Crash
verbraucht; kein Job wird rückwirkend erzeugt. Diese Verfügbarkeitseinbuße schützt die
Replay-Grenze. Ein Worker läuft niemals innerhalb der Replay-Transaktion.

Der dauerhafte Speichervertrag und die Rechte stehen als D1-Entwurfszusatz in
[docs/DATABASE.md](DATABASE.md). Ein älteres Restore darf nicht still als gültiger
Replay-Stand starten. Die vorhandene Audit-/Anchor-Prüfung beweist keine
Replay-Vollständigkeit, solange keine geprüfte Bindung der Replay-Historie an einen
unabhängigen Rollback-Nachweis existiert. Dieser Nachweis ist ein **separates
Security-Gate**; leere Ersatz-Tabelle, automatische Neuinitialisierung, History-Löschung
und Anchor-Reset sind verboten.

## Portal-Retry und Abnahme

Timeout und unbekannter Ausgang sind **unbekannt**, nicht Erfolg. Das Portal darf weder
denselben signierten Request automatisch erneut senden noch mit neuem Nonce dieselbe
menschliche Aktion automatisch erneut ausführen. Eine erneute Handlung braucht eine
ausdrückliche Nutzerentscheidung und einen separaten Status-/Idempotenzvertrag. D1
schützt denselben signierten Request gegen Replay; Ende-zu-Ende-Idempotenz einer
menschlichen Absicht ist nicht Teil von D1.

Vor einem aktiven Code-PR: unabhängiger Security-Review dieses exakten Heads, Kaans
Entscheidungen unten, freigegebener DB-/Rollback-Vertrag und Tests für alle
positiven/negativen Vektoren, Header-Duplikate vor Dictionary-Bildung, Framing,
Misch-Auth, Zeit-/Lock-/Commit-Crashpunkte, parallele Inserts, Restart/Restore und
unveränderte Last-/Policy-/Approval-/Audit-Gates. Jede neue Ablehnung braucht einen
Test, der ihr Fehlen bemerkt, und das Modul gehört in `scripts/refusals.py::GUARDED`.
Ein DB-Code-PR benötigt zusätzlich `Claude DB Review: APPROVED` am exakten Head und
grüne `contracts`.

**Offene Gates (HOLD):** (1) Kaans minimale D3-Entscheidung zu kanonischer Personen-ID
und Alias-/Key-Verknüpfung; (2) Kaans JA/NEIN zur fünfsekündigen Vorlauftoleranz; (3)
unabhängiger, restart- und instanzfester Zeit-Rollback-Nachweis; (4) unabhängiger
Replay-Restore-/Rollback-Nachweis mit freigegebenem DB-Design und Betriebsprofil; (5)
erneuter unabhängiger Review am neuen Head.
