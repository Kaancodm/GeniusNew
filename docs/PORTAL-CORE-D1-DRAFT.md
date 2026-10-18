# D1: Portal–Kern-Vertrag (Entwurf zur unabhängigen Prüfung)

**Status: HOLD.** Entwurf gemäß Entscheidung vom 18.10.2026, Issue #159; D1 bleibt bis zu unabhängigem Review, DB-Abgleich und Betriebsfreigabe deaktiviert. Grundlage ist [Issue #92](https://github.com/Kaancodm/GeniusNew/issues/92).
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

**D3-Grundregel, von Kaan am 05.10.2026 bestätigt:** Core vergibt eine stabile
kanonische Personen-ID und verwaltet die Bindung von Portal-Konten, direkten
API-Keys und Approver-Subjects an diese ID. Unbekannte oder mehrdeutige
Bindungen werden abgelehnt. Für die spätere D3-Umsetzung muss der Portal-Server
eine aus der geprüften Session ermittelte, stabile Portal-Nutzerreferenz als
eigene signierte Aussage liefern. Der Kern müsste
`(verifizierter Portal-Dienst, Portal-Nutzerreferenz)` in einer
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

**HOLD D3:** Der konkrete Nachweis, dass mehrere Aliasse derselben natürlichen
Person gehören, sowie Aufnahme, Änderung, Sperrung und Wiederherstellung ihrer
Core-Bindungen sind noch nicht freigegeben. Nutzer-Aussage und Core-Registry
brauchen dafür einen eigenen versionierten Vertrag. Der Nutzerbezug braucht eine
neue Envelope-Version oder einen gesonderten signierten Nutzerkanal mit eigener
Core-Prüfung; er darf nicht still in das unten geschlossene D1-Envelope eingefügt
werden. Dieses enthält **keine** Nutzer-Aussage. D1 begründet bis dahin keine
nutzergetrennten Jobs oder Freigaben. C3 bleibt
unverändert. Bis zur D3-Freigabe bleibt die oben ausdrücklich begrenzte D1-Ein-Nutzer-Beta zulässig; zusätzliche Portal-Nutzer und jeder freigabepflichtige D1-Job bleiben gesperrt. Der Dienst-Principal wird nicht als individuelle Nutzeridentität ausgegeben. Die C3-Selbstfreigabegrenze wird dadurch nicht für andere Auftragswege verändert.

### D1-Dienstprofil: Start-Ablehnungen (H1)

Vor Aktivierung des HTTP-Listeners muss der Kern für den dedizierten D1-Service-Principal drei unabhängige, fail-closed Prüfungen durchführen:

| Fehlerhafte Konfiguration | Abnahme |
| --- | --- |
| Sein Policy-Grant hat `requires_approval=true` | REFUSE beim Dienststart; eigener Negativtest |
| Sein Subject steht in `[approvers]` | REFUSE beim Dienststart; eigener Negativtest |
| In `[principals]` existiert ein Bearer/API-Key für dieses Subject | REFUSE beim Dienststart; eigener Negativtest |

Zusätzlich sind ausschließlich ausdrücklich freigegebene risikoarme Tools zulässig. Ein Positivtest weist nach, dass ein korrektes Profil nach Bestehen aller übrigen Gates starten kann. Der Service-Principal darf keine Approval-Freigabe erteilen. Jeder Refusal-Test muss das Entfernen der Ablehnung bemerken.

**Ein-Nutzer-Betriebsgrenze, Entscheidung Kaan 18.10.2026:** D1 darf *vor* D3 signierte Jobs über einen dedizierten Dienst-Principal annehmen, **nur solange Kaan der einzige Portal-Nutzer ist**, ausschließlich ohne Freigabepflicht. Dieser Principal ist technisch eine Dienst- und keine verifizierte Personenidentität: Alle so angenommenen Jobs teilen seinen Job-Namensraum und seine Quote. Vor Zulassung eines zweiten Portal-Nutzers sind D3-Personenbindung, Portal-Identitätsvertrag und die Trennung von Aufträgen und Freigaben zwingend. Unbekannter Portal-Nutzerzustand, weitere Nutzer oder Freigabepflicht sind REFUSE, nicht etwa ein Fallback auf D1. Diese Einschränkung ist als Betriebsbedingung durchzusetzen und zu testen, nicht allein durch Text oder einen Browserwert.

**Schlüsseltrennung (L2):** Das Portal erzeugt seinen privaten Ed25519-Schlüssel unabhängig vom Core-Root-Secret. Der Kern besitzt nur die öffentliche Hälfte. Ein D1-Verifier muss die Übergabe eines privaten Schlüssels ablehnen; dazu gehört ein eigener Negativtest.

## Bytegenaues Signaturprofil

Das Envelope enthält **genau** elf Felder; `signature` gehört nicht dazu. Konfigurierte
Bezeichner sind ASCII-Strings mit 1–64 Zeichen ausschließlich aus
`[A-Za-z0-9._-]`, ohne Unicode-Normalisierung. Validatoren müssen den **gesamten**
String prüfen (`fullmatch`, nicht ein Match mit `$`, das vor einem abschließenden
Zeilenumbruch enden kann); ein Bezeichner mit `\n` ist REFUSE.

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
noch der signierten Bytes. Die Ed25519-Prüfung ist strikt: öffentlicher Schlüssel
und Signaturpunkt `R` sind jeweils kanonisch kodierte 32-Byte-Kurvenpunkte
der Ed25519-Primordnungs-Untergruppe und dürfen nicht der Identitätspunkt sein.
Nicht kanonische Kodierungen, Punkte mit Torsionsanteil und Punkte kleiner
Ordnung werden verweigert. Der Signaturskalar `S` muss kleiner
als die Ed25519-Gruppenordnung
`L = 2^252 + 27742317777372353535851937790883648493` sein. Die Verifikation
prüft die nicht kofaktormultiplizierte Gleichung
`[S]B = R + [H(R || A || M)]A` für die oben definierten Signierbytes `M`.
Dabei sind `A` die 32 öffentlichen Schlüsselbytes, `R` die ersten 32
Signaturbytes, `S` die letzten 32 Bytes als Little-Endian-Integer, `B` der
Ed25519-Basispunkt und `H` SHA-512 mit Reduktion modulo `L`;
eine nur nach Multiplikation mit dem Kofaktor gültige Signatur ist REFUSE.
Auch konfigurierte öffentliche Schlüssel werden vor Verwendung so geprüft.

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
Die spätere Signatur-Abnahme muss außerdem `S >= L`, nicht kanonische
Kodierungen von `A` oder `R` (insbesondere `y >= 2^255 - 19` und gesetztes
Vorzeichenbit bei `x = 0`), Punkte außerhalb der Primordnungs-Untergruppe
einschließlich kleiner Ordnung sowie eine nur kofaktorisiert
gültige Signatur als **REFUSE** prüfen; `signing_sha256` ersetzt diese
Ed25519-Negativvektoren nicht.

### Ed25519-Festwertvektor (L1)

Öffentliches **Testmaterial**, niemals für den Betrieb. Der Seed ist unabhängig vom Core-Root-Secret. Der ASCII-Body ist `b'{"text":"hello"}'` und das unveränderte 352-Byte-Envelope aus dem Vektor oben; die Signatur gilt genau `b"geniusnew/portal-core/request/ed25519/d1\\x00" + envelope_bytes` (393 Bytes).

| Wert | Erwartet (lowercase Hex) |
| --- | --- |
| Test-Seed: `bytes(range(32))` | `000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f` |
| Public Key (32 Bytes, Raw) | `03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8` |
| Ed25519-Signatur (64 Bytes / 128 Hex-Zeichen) | `ae927e464ea6e7b9ed2021c16f4697e66140fc701cd5d3902ab80c0e9df997fdc4b0f088bdf97551bead84c71582667b911fe54df67576e2366e6f9eccbca101` |
| SHA-256 des kanonischen Envelope für `d1-audit-v1` | `08c36cbf373c676a3d92c0eee5537d50f87fc110a10e06231ba0153e4519871f` |

Reproduktion: Ed25519-Private-Key aus dem Seed bauen, rohen öffentlichen Schlüssel vergleichen, die exakt vorhandenen Envelope-Bytes kanonisch erzeugen und mit `private_key.sign(signing_bytes)` signieren. Der daraus berechnete Wert muss bytegleich sein; `public_key.verify(signature, signing_bytes)` muss erfolgreich sein. Nach Änderung je eines Bytes der Signatur und der signierten Nachricht muss `InvalidSignature` folgen. Alle bereits dokumentierten `body_sha256`- und `signing_sha256`-Vektoren bleiben gültig. Zusätzlich sind Negativtests für `S >= L`, nichtkanonische Punkte und Signaturen nur mit Kofaktor-Gleichung erforderlich (strenges Profil oben).

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

**Gate 4, Entscheidung 18.10.2026:** Die zwingende Duplikatprüfung liegt am Kern auf seiner rohen Headerliste **vor** Dictionary-Bildung und Body-Lesen. Der Proxy darf problematische Requests bereits vorher vollständig ablehnen, muss aber alle D1-relevanten Header-Instanzen unverändert weitergeben und darf sie weder zusammenführen noch entfernen. Der Kern verweigert
die gleichzeitige Präsenz von `Authorization` und `X-GeniusNew-D1-Envelope`, auch
wenn einer der Werte ungültig ist. Jeder weitere Hop wiederholt diese Prüfung. Ein
Proxy darf diese beiden Header weder entfernen, hinzufügen, umbenennen noch ihre Werte
ändern; D1-relevante Duplikate und andere sicherheitsrelevante Header darf er nicht
entfernen oder zu einem Wert zusammenführen. Kann ein Hop die Authentisierungsheader
nicht unverändert weitergeben, verweigert er den Request. Bei HTTP/2 sind `:method`, `:path`,
`:scheme` und `:authority` jeweils genau einmal erforderlich; `:scheme` ist exakt
`https`, und gleichzeitige `Host`- und `:authority`-Felder sind auch bei gleichen
Werten REFUSE. Die Grenze von 8192 Oktetten gilt für die dekomprimierte Headerliste
an jedem Hop, einschließlich Pseudo-Headern. Der Core ist nur über diesen geprüften
Ingress erreichbar und prüft die bei ihm empfangene rohe HTTP/1.1-Liste nochmals.
Die nginx-Vorlage benötigt reproduzierbare Tests für unveränderte Weitergabe oder frühe Ablehnung von Duplikaten und Misch-Auth unter HTTP/1.1 und HTTP/2. Solange dies nicht belegt ist, bleibt das Proxy-Betriebsgate offen.

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
Der Core-Adapter nimmt nur eindeutig gerahmtes HTTP/1.1 an. HTTP/2-Framing muss ein
vorgeschalteter Proxy eindeutig terminieren; dieser verweigert auch tatsächliche
Request-Trailer, selbst wenn kein `Trailer`-Header angekündigt wurde. **Tatsächlich
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

`now` ist bei jeder Prüfung die ganzzahlige Unix-Sekunde der Core-UTC-Uhr
(`floor` des tatsächlichen Sekundenwerts); Gleitkomma-Rundung darf die
Akzeptanzgrenzen nicht verschieben. Uhr-Rücksprung ist gemäß Entscheidung vom 18.10.2026 eine dokumentierte **Betriebsgrenze** statt eines unabhängigen kryptografischen Zeitnachweises: NTP ist Pflicht, Rücksprünge gegen die im Prozess beobachtete Maximalzeit sind REFUSE, und eine in PostgreSQL gemeinsam atomar fortgeschriebene Höchstzeit ist beim Start sowie bei D1-Prüfungen zu kontrollieren. Fehlende, nicht lesbare oder widersprüchliche Werte sperren D1. Die DB-Höchstzeit ist selbst kein unabhängiger Zeitzeuge und kann bei gemeinsamem Restore zurückfallen; dieser Rest wird in einem eigenen `SECURITY.md`-PR dokumentiert.

Kaan hat am 05.10.2026 [im PR #120](https://github.com/Kaancodm/GeniusNew/pull/120#issuecomment-5999610879)
**JA** zu diesem Fünf-Sekunden-Profil entschieden. Die fünf
Sekunden gelten ausschließlich vor `issued_at`; nach `expires_at` gibt es keine
Nachfrist. Die Betriebsprüfungen für Uhr-Rücksprung bleiben vor aktivem D1-Code erforderlich.

### Gate 3: Start-Quarantäne

Nach **jedem** Dienststart, Crash-Restart, Restore oder Wechsel des autoritativen Replay-Primary nimmt D1 **65 Sekunden lang** keine Requests an und erzeugt weder Job noch Reservation. Die Schranke nutzt eine monotone Laufzeituhr und darf bei Rücksprung der Core-Uhr nicht vorzeitig freigeben. 60 Sekunden maximale Signaturgültigkeit plus 5 Sekunden Vorlauf gewährleisten, dass vor einem Restore angenommene Signaturen nach der Quarantäne abgelaufen sind. Der Zustand wird erst bei gültigem Startabgleich von Job-Ledger, Audit-Kette und getrenntem Anker wieder freigegeben; Ankerreset und unbekannter Restore-Stand bleiben verboten.

Abgelaufene Reservationen dürfen über eine **eigene, getrennt berechtigte Bereinigungsrolle** erst bei `expires_at < now - 65` entfernt werden. Löschung und versioniertes Bereinigungs-Audit werden atomar gespeichert, mit Bezug auf ursprünglichen Replay-Schlüssel, Job, Annahmeevent, Ablauf und Löschzeit; die Runtime selbst hat auf der Replay-Tabelle nur `SELECT`/`INSERT` und keine Löschrechte. Der Startabgleich unterscheidet passende auditierte Bereinigung von fehlenden/unerwarteten Reservationen. Eine neue D1-Startprüfung für tatsächlichen Primary Key, Zeilenform und effektive Rechte ist ausdrücklich zulässig (Gate 7); sie ist kein allgemeiner Schema-Fingerprint. Ein beschädigter, fehlender oder widersprüchlicher DB-/Audit-Zustand bleibt fail closed.

Der bestehende Connection-Limit-Gate greift bereits beim Annehmen der Verbindung;
Header- und Body-Limits greifen vor teurer JSON-/Signaturarbeit. Verbindliche
Reihenfolge eines D1-Requests:

0. Nach billiger Framing- und Größenprüfung, **vor** JSON-Parsen, Body-Digest und
   Ed25519-Prüfung, ein begrenztes, authentisierungsunabhängiges D1-Eingangsbudget
   belasten. Auch ungültige und nicht signierte Versuche verbrauchen dieses Budget;
   Erschöpfung ist REFUSE. Kapazität und Rate müssen für alle aktiven Core-Instanzen
   vor Code als Last-Gate festgelegt und geprüft sein. Das vorangeschaltete geschützte Ingress-Gate 10 ist hierfür zwingend. Dieses Vorab-Budget ersetzt
   weder den Dienst- noch den Nutzer-Token-Bucket. Das Portal begrenzt zusätzlich
   die Zahl der Signierversuche pro geprüfter Portal-Nutzerreferenz **über alle
   Sessions hinweg** und insgesamt für das Portal, bevor es einen D1-Request
   signiert. Die Budgets gelten instanzübergreifend und über Neustarts; auch
   verweigerte und fehlgeschlagene Signierversuche verbrauchen sie. Unbekannter
   oder nicht erreichbarer Budgetzustand ist REFUSE. Konkrete Kapazitäten,
   Raten und der Nachweis sind Teil des Last-Gates. Browserwerte begründen
   dabei keine Core-Autorität.
   Die D1-Route darf nur
   über einen vor dem Budget wirksamen, geschützten Ingress für das vorgesehene
   Portal-Backend erreichbar sein. Die konkrete Netz-/Dienstzugangskontrolle und
   ihr Nachweis sind ein Betriebsgate; ein öffentlich erreichbarer anonymer
   D1-Eingang könnte das gemeinsame Vorab-Budget erschöpfen und Portal-Aufträge
   aussperren.
1. HTTP-Framing, Header, Route, Größen, vollständigen Body und kanonische JSON-Form
   prüfen; `method`/`path` mit der tatsächlich empfangenen Route und
   `body_sha256` mit den Body-Bytes vergleichen; keine Job-/Audit-Mutation.
2. Erste Zeitprüfung mit der Core-Uhr.
3. Den konfigurierten Ed25519-Schlüssel wählen und Signatur prüfen.
4. Die verifizierte Service-Identität, **Ein-Nutzer-Betriebsbedingung**, H1-Startprofil, Rate Limits, Dienst-Bucket, `max_in_flight` und Policy-Grants prüfen. Vor D3 kein individuelles Nutzerrecht und keine Nutzerquote aus dem gemeinsamen Principal ableiten. Weitere Nutzer oder Approval-Pflicht sind REFUSE. Die Kapazitätsgrenze vor der Reservation erwerben. Kein Aufruf von `_dispatch()` unter Umgehung der Gates.
5. Zeit unmittelbar vor dem Replay-INSERT prüfen und **eine gemeinsame PostgreSQL-Annahmetransaktion** beginnen. Den Schlüssel `(domain, issuer, nonce)` mit `ON CONFLICT (domain, issuer, nonce) DO NOTHING RETURNING domain, issuer, nonce` reservieren; nur exakt eine passende Zeile gilt als Gewinner. Null Zeilen oder eine abweichende Rückgabe sind REFUSE ohne Job.
6. Die **zufällige Job-ID vor COMMIT** erzeugen. Handoff, Job-Ledger, Replay-Reservation sowie das neue versionierte Annahme-Audit-Event mit `SHA256(envelope_bytes)`, Principal-Digest und Replay-Bindung **in derselben Transaktion** speichern. Bereits signierter Audit-Kopf und die bestehenden Anker-/B6-Gates bleiben Pflicht. Nach jeder DB-/Lock-Wartezeit und unmittelbar vor COMMIT Zeit neu lesen; bei Ablauf alles zurückrollen.
7. Den synchronen dauerhaften COMMIT bestätigen und Audit-/Anker-Abgleich erfüllen. Unbekannter COMMIT-Ausgang bleibt **UNKNOWN**: Ein vollständiger Job samt Reservation und Audit kann bereits bestehen; weder neuer Job noch automatischer Retry noch Workerstart sind erlaubt. Wird ein bereits verbindlich angenommener Job vor Ausführung abgelaufen, erfolgt ein separater atomarer Ledger-/Audit-Refusal **für dieselbe Job-ID**, nicht die Behauptung, es habe nie einen Job gegeben.
8. Erst danach den Worker **außerhalb** der Annahmetransaktion starten. `max_in_flight` ist auf allen Ausgängen freizugeben. Kein Worker läuft in einer Replay-/Ablehnungstransaktion.

**Signaturgeprüfte Ablehnungen ohne Reservation (Gate 9):** Für einen verbrauchten Nonce oder Ablauf nach gültiger Signatur wird eine begonnene Annahmetransaktion vollständig zurückgerollt; es darf kein neuer Job entstehen. Eine **separate dauerhafte Audit-Transaktion** schreibt ein versioniertes `D1_REQUEST_REFUSED`-Event mit unabhängiger zufälliger `attempt_id` (kein Job-Identifier), `SHA256(envelope_bytes)`, Digest des verifizierten Service-Principals, Digest des kanonischen Replay-Schlüssels `(domain,issuer,nonce)`, `expires_at`, geprüfter Core-Zeit und festem Grund `NONCE_CONFLICT` oder `REQUEST_EXPIRED`, zusammen mit signiertem Audit-Kopf; Ankerprüfung bleibt bestehen. Body, Signatur und Secrets erscheinen nicht im Audit. Abbruch, Crash, unbekannter Commit oder Ankerfehler verhindern eine als bestätigt ausgegebene Audit-Ablehnung und jeden Workerstart. Der B6-Startabgleich prüft die Bindungen beidseitig: jede nicht bereinigte Reservation genau zu Annahme und Job, jede Annahme zu Reservation oder exakt passendem auditiertem Bereinigungsnachweis; nur versionierte Ablehnungs-Events dürfen ohne Job/Reservation existieren. Unbekannte Event-Typen, Bindungen oder fehlende Zeilen sind REFUSE. Damit ist Gate 9 als **Entwurfsvertrag erledigt**, nicht als implementierte Freigabe.

**Gate 8, Buchung:** Ablehnungen vor erfolgreicher Ed25519-Prüfung einschließlich erster Zeitprüfung belasten ausschließlich das Quellbudget am Proxy und das globale Core-Vorab-Budget, **nicht** den gemeinsamen Dienst-Bucket und niemals eine Nutzerquote. Bei gültiger Signatur wird der Dienst-Bucket vor weiterer Zeit-/Replay-Prüfung genau einmal belastet; signaturgeprüfte Replay-/Zeitablehnungen erstatten ihn nicht zurück. Unsignierte Requests dürfen über frei behauptete `issuer`-Werte nicht den Dienst-Bucket erschöpfen.

**Gate 5, Lastschutz:** Ein Proxy-Limit **pro Quelle** und ein globales authentisierungsunabhängiges D1-Eingangslimit über alle Core-Instanzen greifen vor JSON und Ed25519; zusätzlich begrenzt das Portal seine Signierversuche instanzübergreifend pro verifizierter Portal-Referenz und insgesamt (Gate 10). Zahlen, Bursts, Parallelität und Replay-/Audit-Speicherschwellen sind vor Aktivierung durch Kaan zu genehmigen und unter Last zu testen. Der D1-Eingang muss vor dem globalen Vorab-Budget durch einen geschützten Ingress (etwa mTLS oder streng geschütztes Proxy-Secret) auf das Portal-Backend beschränkt sein, auch bei Vercel ohne feste Egress-IP. Eine anonyme öffentlich erreichbare D1-Route ist verboten; fehlende Sicherung ist HOLD.

Alle gleichzeitig annehmenden Core-Instanzen für dasselbe `(issuer, audience)`
verwenden denselben schreibbaren PostgreSQL-Primary und dieselbe Replay-Tabelle.
Getrennte schreibbare Kopien, Split-Brain und ein Failover ohne bestätigte
Reservationen sperren D1; unbekannter Replikationsstand ist REFUSE.

Jede D1-Schreibtransaktion setzt `SET LOCAL synchronous_commit TO on` nach `BEGIN`
und prüft den wirksamen Wert unmittelbar vor COMMIT, ohne dazwischenliegende
Änderung. `fsync=on` und `full_page_writes=on` werden beim Start und vor jeder
Reservation wirksam geprüft. Kann das Betriebsprofil nicht bis zum COMMIT
garantiert werden, sperrt D1. Tatsächlich dauerhafte WAL-Speicherung ist Pflicht;
asynchroner COMMIT ist unzulässig. Der konkrete PostgreSQL-/Failover-Nachweis
ist ein **Betriebsgate**. Bei DB-Ausfall, vollem Speicher, Konflikt,
unbekanntem COMMIT-Ausgang oder Verbindungsabbruch während COMMIT:
kein Job, kein Erfolg, kein automatischer Retry desselben Requests.

Die gemeinsame Annahmetransaktion begrenzt `lock_timeout`, `statement_timeout` und
`idle_in_transaction_session_timeout` jeweils auf höchstens die unmittelbar
vor dem Replay-INSERT gemessene verbleibende Request-Gültigkeit und zusätzlich
auf einen konfigurierten Maximalwert zwischen 1 ms und 60 Sekunden.
Ein unabhängiges Gesamtzeitlimit
begrenzt die Transaktion einschließlich mehrerer Statements; Timeout oder
unklarer Wartezustand sind REFUSE ohne automatischen Retry. Die für PostgreSQL
gesetzten Millisekundenwerte werden aus der tatsächlichen, nicht auf `now`
abgerundeten Restgültigkeit nach unten gerundet. Ist der kleinste Wert aus
Restgültigkeit und konfiguriertem Maximum kleiner als 1 ms oder fehlt das
Maximum, ist der Request REFUSE; ein Timeoutwert `0` darf nie an PostgreSQL gehen, weil
er dort die Begrenzung deaktiviert. Damit darf ein
hängender DB-/Lock-Versuch `max_in_flight` nicht unbegrenzt halten. Der D1-Last-
und Betriebsnachweis muss außerdem D1 vor Erschöpfung des gemeinsamen Core-
Speichers sperren, damit Job-Ledger und Audit nicht durch Replay-Wachstum
ausfallen; Schwelle und Überwachung sind vor Code festzulegen.

Nach bestätigtem Annahme-COMMIT überstehen **Reservation, Job und Audit gemeinsam** jeden Crash. Eine isoliert verbrauchte Reservation ohne Job ist kein erlaubter erfolgreicher Annahmezustand. Ein Worker läuft niemals innerhalb der Transaktion.

Der dauerhafte Speichervertrag und die Rechte stehen als D1-Entwurfszusatz in
[docs/DATABASE.md](DATABASE.md). Ein Restore sperrt D1 bis zum erfolgreichen vollständigen Audit-/Ledger-/Anker-Startabgleich und der erneuten **65-s-Quarantäne**. Ein unabhängiger kryptografischer Replay-Fortschrittsnachweis ist nicht mehr gefordert. Leere Ersatz-Tabelle, automatische Neuinitialisierung, unautorisierte History-Löschung und Anchor-Reset bleiben verboten.

## Portal-Retry und Abnahme

Timeout und unbekannter Ausgang sind **unbekannt**, nicht Erfolg. Das Portal darf weder
denselben signierten Request automatisch erneut senden noch mit neuem Nonce dieselbe
menschliche Aktion automatisch erneut ausführen. Eine erneute Handlung braucht eine
ausdrückliche Nutzerentscheidung und einen separaten Status-/Idempotenzvertrag. D1
schützt denselben signierten Request gegen Replay; Ende-zu-Ende-Idempotenz einer
menschlichen Absicht ist nicht Teil von D1.

Vor einem aktiven Code-PR stehen der unabhängige Security-Review dieses exakten Heads,
Kaans Entscheidungen unten und der freigegebene DB-/Rollback-Vertrag. Für dessen
spätere Abnahme sind Tests nötig für alle
positiven/negativen Vektoren, Header-Duplikate vor Dictionary-Bildung, Framing,
HTTP/2-Proxy-Übersetzung, Misch-Auth, erschöpftes Vorab-Lastbudget,
Zeit-/Lock-/Commit-Crashpunkte, parallele Inserts, Restart/Restore und unveränderte
Last-/Policy-/Approval-/Audit-Gates. Jede neue Ablehnung braucht einen
Test, der ihr Fehlen bemerkt, und das Modul gehört in `scripts/refusals.py::GUARDED`.
Ein DB-Code-PR benötigt zusätzlich `Claude DB Review: APPROVED` am exakten Head und
grüne `contracts`.

**Gate-Status – D1 bleibt HOLD:** (1) D1 vor D3 **ausschließlich für Kaan als einzigen Portalnutzer**, nur risikoarme Jobs ohne Approval; H1-Start-Ablehnungen als Entwurfsvertrag entschieden, D3-Personenbindung vor einem zweiten Nutzer erforderlich. (2) NTP, Prozess-Monotonie und PostgreSQL-Höchstzeit als Betriebsgrenze, eigener SECURITY.md-PR ausstehend. (3) **ERLEDIGT im Entwurf:** 65-s-Quarantäne statt Replay-Fortschrittsnachweis; Nachweis im Betrieb offen. (4) Proxy erhält Header, Core prüft Rohduplikate; nginx-Tests offen. (5) Lastbudgets, konkrete Zahlen, Replay-/Audit-Speicherschwelle und Kaans Freigabe offen. (6) **OFFEN:** erneuter unabhängiger Claude-Review am exakten Head. (7) Spezifische D1-Startprüfung von PK, Zeilenform und Rechten ist freigegeben; Umsetzung offen. (8) Signierte Ablehnungen belasten den Dienst-Bucket, unsignierte ausschließlich Vorabbudgets; Nutzerquote nie. (9) **ERLEDIGT im Entwurf:** atomare Annahme plus separate auditierte Ablehnung und B6-Abgleich; Review und DB-Vertrag stehen aus. (10) Geschützter Ingress und vor dem Signieren wirksame instanzfeste Portal-Limits als Betriebsgate.

(10) Nachweis eines vor dem Vorab-Budget wirksamen geschützten Ingress, der
anonyme öffentliche Last auf die D1-Route ausschließt, und der instanz- und
neustartfesten Portal-Budgets pro Nutzerreferenz und insgesamt vor dem Signieren.
