# D1: Portal–Kern-Vertrag (Entwurf zur unabhängigen Prüfung)

**Status: HOLD.** Grundlage ist [Issue #92](https://github.com/Kaancodm/GeniusNew/issues/92).
Die Überarbeitung für [Issue #159](https://github.com/Kaancodm/GeniusNew/issues/159)
bildet die D1-Entscheidungen vom 18.10.2026 in [DECISIONS.md](DECISIONS.md)
([PR #158](https://github.com/Kaancodm/GeniusNew/pull/158)) ab. Dieser Text aktiviert
keine Route. Die offenen Freigabe-, Prüf- und Betriebsgates am Ende sperren aktiven
D1-Code; ein erledigter Entwurfsvertrag ist kein Implementierungsnachweis.

## Identität und Autorität

Der Browser liefert allein den Auftragstext. `subject`, `tier`, `user_id`, `tools`,
`job_id`, Approver-Identität und ähnliche Autoritätsfelder im Body werden verweigert.
Der Kern erzeugt die Job-ID. Der private Ed25519-Schlüssel bleibt beim Portal-Backend;
der Kern hält nur den öffentlichen Schlüssel. Der D1-Verifier verweigert den Bau mit
einem privaten Schlüssel; dafür ist ein eigener Negativtest erforderlich. Der
Portal-Schlüssel wird unabhängig erzeugt und **nicht aus dem Root-Secret des Kerns
abgeleitet**. Der Kern kann ihn weder rekonstruieren noch selbst D1-Anfragen signieren.
TLS mit geprüftem Servernamen bleibt Pflicht.

Der verifizierte D1-Schlüssel authentisiert den **Portal-Dienst**. Der Kern bindet
`(issuer, audience, key_id)` in eigener Konfiguration an einen dedizierten
Service-Principal. Portal-Login, Session und Portalrolle werden dadurch **nicht** zur
Core-Nutzeridentität. Der gemeinsame Service-Principal beweist weder Job-Eigentum einer
Einzelperson noch deren Approval-Berechtigung oder individuelle Quote.

### D1 ohne Nutzerfreigaben: Start-Ablehnungen (H1)

D1 darf vor D3 ausschließlich in der **geschlossenen Beta mit Kaan als einzigem
Portal-Nutzer** risikoarme Tools über seinen dedizierten Dienst-Grant aufrufen.
Alle Portal-Aufträge teilen sich einen Principal, eine Quote und einen
Job-Namensraum. **Ein zweiter Portal-Nutzer setzt D3 voraus.** Diese Präzisierung
der Entscheidung vom 18.10.2026 ersetzt die frühere vollständige Sperre der
D1-Jobannahme bis D3; sie erlaubt keinen Betrieb mit mehreren Portal-Nutzern.
Die Zugangsbeschränkung auf Kaan muss vor Aktivierung am Portal nachgewiesen sein;
der Kern kann die Zahl der Portal-Nutzer aus der Dienstsignatur nicht feststellen.
Freigabepflichtige Aufträge und Nutzerfreigaben über D1 bleiben gesperrt.
Vor dem Listener prüft der Dienst die wirksame Core-Konfiguration:

| Verbotene Konfiguration | Geforderter Test |
| --- | --- |
| Der D1-Dienst-Grant hat `requires_approval=true` | Dienststart verweigert, kein Listener und kein Job; derselbe Test wird ohne diese Ablehnung rot. |
| Der D1-Dienst-Subject steht in `[approvers]` | Dienststart verweigert, auch mit sonst gültigem D1-Schlüssel und Grant; eigener Test für diese Ablehnung. |
| In `[principals]` ist ein Bearer/API-Key an den D1-Dienst-Subject gebunden | Dienststart verweigert, auch ohne Eintrag in `[approvers]`; eigener Test für diese Ablehnung. |

Ein Positivtest fordert den Start mit gültigem öffentlichem D1-Prüfschlüssel,
risikoarmem Grant ohne Approval-Pflicht, ohne Approver-Rolle und ohne Bearer-Key des
Dienstes, nach Erfüllung der übrigen Startgates. Die drei Ablehnungen gelten jeweils
einzeln und bei Kombination; sie dürfen nicht nur eine bereits aktive Route sperren.
Die Startprüfung ist kein Ersatz für die bestehenden Last-, Policy- und C3-Gates.

### D3 bleibt ein eigener Vertrag

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
werden. Dieses enthält **keine** Nutzer-Aussage und darf nicht still um `user_id`
ergänzt werden. D1 begründet bis dahin keine nutzergetrennten Jobs oder Freigaben. C3 bleibt
unverändert. Der fehlende D3-Aliasnachweis blockiert den oben begrenzten D1-Dienstpfad
nur für die geschlossene Beta mit Kaan als einzigem Portal-Nutzer nicht mehr.
Ein zweiter Portal-Nutzer, nutzergetrennte Jobs, Nutzerfunktionen und Freigaben
setzen weiterhin den freigegebenen D3-Vertrag und dessen Umsetzung voraus.

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
Schlüsselwechsel hinweg reserviert, solange seine Reservation besteht. Eine neue
`key_id` macht sie nicht frei. Das Portal erzeugt für jede neue Handlung einen neuen
zufälligen Nonce; ein bereinigter alter Request bleibt durch sein Zeitfenster ungültig.

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

### Ed25519-Festwert für den ASCII-Fall (L1)

Das folgende **öffentliche Testmaterial ist ausschließlich für Tests** bestimmt; der
Seed ist kein produktiver Schlüssel und wird nicht aus dem Core-Root-Secret abgeleitet.
Es gelten unverändert die 352 Envelope-Bytes des ASCII-Falls oben. Signiert werden
die 393 Bytes aus Label und Envelope, weder der Body noch ein vorab berechneter Digest.

| Wert | Lowercase Hex |
| --- | --- |
| Fester Test-Seed, 32 Bytes (`bytes(range(32))`) | `000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f` |
| Öffentlicher Ed25519-Schlüssel, 32 rohe Bytes | `03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8` |
| Erwartete Signatur, 64 Bytes / 128 Hex-Zeichen | `ae927e464ea6e7b9ed2021c16f4697e66140fc701cd5d3902ab80c0e9df997fdc4b0f088bdf97551bead84c71582667b911fe54df67576e2366e6f9eccbca101` |
| `SHA256(envelope_bytes)` für das Audit-Bindungsprofil v1 unten | `08c36cbf373c676a3d92c0eee5537d50f87fc110a10e06231ba0153e4519871f` |

Reproduktion mit Python und der vorhandenen `cryptography`-Abhängigkeit. Die alten
Zeitstempel sind feste Vektorwerte; sie berechtigen nicht zur Annahme eines heutigen
Requests. Ein verändertes Signaturbyte und ein verändertes Nachrichtenbyte müssen
jeweils `InvalidSignature` auslösen:

```python
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# Public TEST ONLY material; never use in production.
seed = bytes(range(32))
expected_public = bytes.fromhex(
    "03a107bff3ce10be1d70dd18e74bc09967e4"
    "d6309ba50d5f1ddc8664125531b8"
)
expected_signature = bytes.fromhex(
    "ae927e464ea6e7b9ed2021c16f4697e66140fc701cd5d3902ab80c0e9df997fdc"
    "4b0f088bdf97551bead84c71582667b911fe54df67576e2366e6f9eccbca101"
)
body = b'{"text":"hello"}'
envelope = {
    "version": "geniusnew-portal-core-d1",
    "kind": "REQUEST",
    "issuer": "portal-test",
    "audience": "core-test",
    "key_id": "key-1",
    "nonce": "00" * 32,
    "issued_at": 1700000000,
    "expires_at": 1700000060,
    "method": "POST",
    "path": "/jobs",
    "body_sha256": hashlib.sha256(body).hexdigest(),
}
envelope_bytes = json.dumps(
    envelope, ensure_ascii=True, sort_keys=True,
    separators=(",", ":"), allow_nan=False,
).encode("ascii")
label = b"geniusnew/portal-core/request/ed25519/d1\x00"
signing_bytes = label + envelope_bytes
assert (len(body), len(label), len(envelope_bytes), len(signing_bytes)) == (
    16, 41, 352, 393,
)
assert hashlib.sha256(envelope_bytes).hexdigest() == (
    "08c36cbf373c676a3d92c0eee5537d50f87fc110a10e06231ba0153e4519871f"
)
assert hashlib.sha256(signing_bytes).hexdigest() == (
    "bbb318bbf252e9a208bfd24cfe3e5042fe49454cc6fff18829fdf0dace942451"
)

private_key = Ed25519PrivateKey.from_private_bytes(seed)
actual_public = private_key.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw,
)
assert actual_public == expected_public
assert private_key.sign(signing_bytes) == expected_signature
assert len(expected_signature) == 64
assert len(expected_signature.hex()) == 128
public_key = Ed25519PublicKey.from_public_bytes(expected_public)
public_key.verify(expected_signature, signing_bytes)

bad_signature = bytes([expected_signature[0] ^ 1]) + expected_signature[1:]
bad_message = signing_bytes[:-1] + bytes([signing_bytes[-1] ^ 1])
for name, signature, message in (
    ("modified_signature", bad_signature, signing_bytes),
    ("modified_message", expected_signature, bad_message),
):
    try:
        public_key.verify(signature, message)
    except InvalidSignature:
        print(name, "REFUSE")
    else:
        raise AssertionError(name + " unexpectedly accepted")
print("Ed25519-ASCII: PASS")
```

Dieser Festwerttest prüft Ed25519 und die Bytes. Der spätere D1-Verifier braucht
zusätzlich den Start-Ablehnungstest für einen übergebenen privaten Schlüssel (L2).
Die spätere Signatur-Abnahme muss außerdem `S >= L`, nicht kanonische
Kodierungen von `A` oder `R` (insbesondere `y >= 2^255 - 19` und gesetztes
Vorzeichenbit bei `x = 0`), Punkte außerhalb der Primordnungs-Untergruppe
einschließlich kleiner Ordnung sowie eine nur kofaktorisiert
gültige Signatur als **REFUSE** prüfen; `signing_sha256` und der Festwerttest
ersetzen diese Ed25519-Negativvektoren nicht.

## HTTP- und Proxy-Grenze

Der einzige D1-Header heißt exakt `X-GeniusNew-D1-Envelope`. Sein einzelner ASCII-Wert
ist `<base64url-ohne-Padding(envelope_bytes)>.<signature-hex>`: URL-safe Base64 mit
Alphabet `A-Z a-z 0-9 _ -`, ohne `=`, gefolgt von genau einem ASCII-Punkt und 128
lowercase Hex-Zeichen. Der Decoder verwirft andere Zeichen, Padding und nicht identisch
wieder kodierbare Werte. Maximal 5600 ASCII-Oktette für den Headerwert, maximal 8192
Oktette für die gesamte empfangene Headerliste. Genau eine Instanz ist erlaubt.

Der Kern prüft Headernamen auf der **ursprünglichen HTTP-Headerliste vor jeder
Mapping-/Dictionary-Bildung und vor dem Lesen des Bodys** ASCII-case-insensitiv auf
Duplikate. D1 verweigert
insbesondere doppelte `Host`, `Authorization`, `Content-Type`, `Content-Length`,
`Transfer-Encoding`, `Content-Encoding`, `Trailer` oder `X-GeniusNew-D1-Envelope`, auch
bei gleichen Werten. Kommagetrennt zusammengeführte Werte ersetzen diese Prüfung nicht.
Obs-fold, Whitespace vor dem Doppelpunkt und nicht-ASCII-Headernamen werden vor der
Listenbildung verweigert.

**Gate 4, Entscheidung vom 18.10.2026:** Der Proxy muss nicht selbst eine zusätzliche
D1-Duplikatprüfung am ersten Hop implementieren. Er darf einen mehrdeutigen Request
früh verweigern. Leitet er ihn weiter, darf er D1-relevante Header aber **weder
zusammenführen noch entfernen**; Anzahl und Werte bleiben für die rohe Prüfung im
Kern erhalten. Insbesondere darf er `Authorization` und `X-GeniusNew-D1-Envelope`
weder entfernen, hinzufügen, umbenennen noch inhaltlich ändern. Das gilt für jeden
Proxy-Hop. Kann ein Hop diese Erhaltung nicht gewährleisten, verweigert er den Request.

Der Kern ordnet den Request anhand des rohen Pfads und der rohen Headerliste eindeutig
einem Authentisierungspfad zu. Die gleichzeitige Präsenz von `Authorization` und
`X-GeniusNew-D1-Envelope` ist REFUSE, selbst wenn einer der Werte ungültig ist.
Eine vorherige Dictionary-Bildung oder ein zusammengeführter Kommawert ersetzt die
Prüfung der ursprünglichen Instanzen nicht.

Die HTTP/2-Terminierung muss weiterhin eindeutiges Framing gewährleisten: `:method`,
`:path`, `:scheme` und `:authority` jeweils genau einmal, `:scheme` exakt `https`;
gleichzeitiges `Host` und `:authority` ist auch bei gleichen Werten REFUSE. Die
notwendige Übersetzung von `:authority` in ein einzelnes `Host` verändert dessen Wert
nicht. Normale D1-relevante Header müssen dabei mit allen Instanzen erhalten bleiben
oder der gesamte Request wird abgelehnt. Die Grenze von 8192 Oktetten gilt für die
dekomprimierte Headerliste an jedem Hop, einschließlich Pseudo-Headern.

Der Core ist nur über den geprüften Ingress erreichbar. Die nginx-Vorlage braucht
Tests für gleiche, verschiedene und unterschiedlich geschriebene Duplikate sowie
Misch-Auth, jeweils mit HTTP/1.1 und HTTP/2-Übersetzung. Das Ergebnis ist entweder eine
frühe vollständige Ablehnung oder eine unveränderte Weitergabe, die der Kern vor
Body-Lesen und Mapping verweigert; stilles Bereinigen ist kein PASS. Solange dieser
Nachweis fehlt, bleibt Gate 4 als **Betriebsgate** offen.

Zusätzlich darf die D1-Route nur über einen **vor dem Vorab-Budget wirksamen,
geschützten Ingress für das vorgesehene Portal-Backend** erreichbar sein.
Die konkrete Netz-/Dienstzugangskontrolle und ihr Nachweis bleiben Gate 10.
Ein öffentlich erreichbarer anonymer D1-Eingang könnte das gemeinsame
Vorab-Budget erschöpfen und Portal-Aufträge aussperren; ein Quellenlimit
ersetzt diesen geschützten Zugang nicht.

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
Akzeptanzgrenzen nicht verschieben. Die Uhr-Rücksprung-Grenze folgt dem
nachstehend beschriebenen Betriebsprofil aus der Entscheidung vom 18.10.2026.

Kaan hat am 05.10.2026 [im PR #120](https://github.com/Kaancodm/GeniusNew/pull/120#issuecomment-5999610879)
**JA** zu diesem Fünf-Sekunden-Profil entschieden. Die fünf Sekunden gelten
ausschließlich vor `issued_at`; nach `expires_at` gibt es keine Nachfrist.

### Core-Uhr und Start-Quarantäne (Gates 2 und 3)

**Uhr-Rücksprung ist seit der Entscheidung vom 18.10.2026 eine Betriebsgrenze:**
NTP ist Pflicht. Der Prozess verweigert D1-Aufnahmen, sobald seine Core-Uhr unter
eine bereits beobachtete Zeit fällt. Zusätzlich vergleichen alle annehmenden
Core-Instanzen ihre Zeit mit einer gemeinsam in PostgreSQL gespeicherten Höchstzeit,
die atomar nur erhöht werden darf. Fehlende, unlesbare oder widersprüchliche
Zeitstände sperren D1. NTP-Zustand, Prozessprüfung und persistente Höchstzeit brauchen
Betriebs- beziehungsweise Regressionstests; unbekannter Zustand ist kein PASS.

Die erste Zeitprüfung liest `max_seen_at` nur; sie darf keine Datenbank-Schreiblast
verursachen. Eine anfragebedingte Fortschreibung erfolgt erst nach verifizierter
Signatur und erfolgreicher Belastung des Dienst-Buckets, atomar und ausschließlich
steigend über alle Core-Instanzen. Abgewiesene unsignierte Anfragen und Requests
bei leerem Dienst-Bucket schreiben diese Höchstzeit nicht fort. Tabelle, Rechte und
Schreibzeitpunkt sind mit dem DB-Vertrag aus #160 abzugleichen; fehlende Werte führen
zur Startablehnung statt zu automatischer Initialisierung.

Die PostgreSQL-Höchstzeit ist kein unabhängiger Zeitzeuge und kann mit einem
DB-Backup zurückgesetzt werden. NTP und die vertrauenswürdige Betriebsführung bleiben
deshalb Teil der Grenze; ein Schutz gegen gemeinsame Manipulation von Uhr und
gespeichertem Zustand wird nicht behauptet. Der entsprechende Eintrag in
`SECURITY.md` folgt in einem eigenen PR. Dieser Entwurf ändert die Datei nicht.

**Nach jedem Dienststart nimmt D1 mindestens 65 Sekunden lang nichts an**
(60 Sekunden maximale Gültigkeit plus 5 Sekunden Vorlauf). Sie beginnt **erst nach
allen bestandenen Start-, Audit-/Anker- und Uhrprüfungen**; deren Laufzeit zählt nicht.
Eine monotone Prozessuhr misst ab diesem Punkt 65 Sekunden; zusätzlich muss die
geprüfte Core-Uhr mindestens 65 Sekunden fortgeschritten sein. Uhrfehler geben die
Quarantäne nicht frei. Währenddessen kein D1-Job, keine Reservation und kein Worker.
Bei erneutem ungeklärtem Start-, Anker- oder Uhrzustand beginnt nach erfolgreicher
Prüfung eine neue volle Quarantäne. Dies gilt auch bei Crash und Restore.

Die Quarantäne ersetzt den bislang geforderten kryptografischen
Replay-Fortschrittsnachweis. Ein vor dem Start bereits angenommenes Envelope kann
unter dem geprüften Zeitprofil nach diesen 65 Sekunden nicht mehr gültig sein.
Bei einem Wechsel des Replay-Datenstands im laufenden Prozess muss D1 zunächst
gesperrt und dieselbe Quarantäne mit Startabgleich erneut durchlaufen werden.
Alte schreibende Instanzen dürfen während eines Restores oder Failovers keine
Anfragen weiter annehmen. Die bestehenden Ledger-, Audit- und Anker-Startprüfungen
bleiben vollständig bestehen; die Quarantäne erlaubt keinen Anchor-Reset und
keinen Start mit beschädigtem oder ungeklärtem Zustand.

### Lastbudgets und Reihenfolge (Gates 5 und 8)

Bereits vor teurer Verarbeitung greifen Connection-, Header- und Body-Limits.
Hinzu kommen **ein Limit pro Quelle am Proxy und ein globales D1-Vorab-Budget im
Kern**. Eine Quelle wird aus der am Proxy beobachteten Gegenstelle ermittelt;
frei gesetzte `Forwarded`-/Client-IP-Header dürfen diese Zuordnung nicht steuern.
Das globale Budget gilt über alle gleichzeitig annehmenden Core-Instanzen
zusammen, nicht in voller Höhe je Prozess. Ein Quellenlimit verspricht weder
Nutzertrennung hinter gemeinsamem Egress noch Schutz vor jedem verteilten Angriff.

Das Portal begrenzt zusätzlich die Zahl der Signierversuche pro geprüfter
Portal-Nutzerreferenz **über alle Sessions hinweg** und insgesamt für das Portal,
bevor es einen D1-Request signiert. Die Budgets gelten instanzübergreifend und
über Neustarts; auch verweigerte und fehlgeschlagene Signierversuche verbrauchen
sie. Unbekannter oder nicht erreichbarer Budgetzustand ist REFUSE. Vor D3 ist
die einzige zugelassene Portal-Nutzerreferenz die von Kaan. Diese Portal-Prüfung
begründet keine Core-Nutzerautorität; Browserwerte dürfen die Budgetzuordnung
nicht bestimmen. Die Quellen-/Core-Budgets ersetzen diese Portal-Budgets nicht.

Kapazität, Rate, Burst, maximale Parallelität und die Replay-/Audit-Speicherschwelle
werden vor aktivem Code konkret vorgeschlagen, von Kaan freigegeben und geprüft.
Der Zahlenvorschlag für Quellen- und Core-Budget gehört zu
[Issue #160](https://github.com/Kaancodm/GeniusNew/issues/160) und `DATABASE.md`;
die zusätzlichen Portal-Budgets und der geschützte Ingress bleiben gesondert
nachzuweisen (Gate 10). Hier werden keine Werte als bereits entschieden eingeführt.

| Ablehnungszeitpunkt | Belastetes Budget |
| --- | --- |
| Vor erfolgreicher Signaturprüfung, einschließlich der ersten Zeitprüfung | Quellenlimit am Proxy; für bis zum Kern gelangte Versuche zusätzlich globales Vorab-Budget. Kein Dienst-Bucket, keine Nutzerquote. |
| Replay- oder Zeitablehnung nach erfolgreicher Signaturprüfung | Zusätzlich Dienst-Bucket des verifizierten D1-Principals, ohne Rückerstattung. Niemals eine Nutzerquote. |

Der Dienst-Bucket wird erst nach gültiger Signatur dem konfigurierten Principal
zugeordnet und **vor** der nächsten Zeit- oder Replay-Prüfung einmal belastet.
Ungültige Signaturen oder bloß behauptete `issuer`-/`key_id`-Werte dürfen ihn nicht
leeren. **Bei leerem Dienst-Bucket wird ohne Audit abgelehnt**; es entsteht auch
kein requestbezogener Logeintrag. Leer bedeutet, dass die anfängliche
Dienst-Tokenbuchung dieser Anfrage scheitert, auch wenn zu ihrer Identität noch
kein Ablehnungs-Event existiert. Hat die erfolgreiche Buchung das letzte Token
verbraucht, bleibt ein erforderlicher erster Ablehnungsnachweis budgetiert;
eine zweite Tokenbuchung erfolgt nicht. Jeder budgetierte signaturgeprüfte
Replay-/Zeitablehnungsversuch belastet den Bucket genau einmal. Wiederholte
Versuche derselben bereits auditierten Identität `(issuer, nonce)` erzeugen kein
weiteres Audit-Event. Die Lastgates werden nicht für Audit oder Retry umgangen.
Die spätere D3-Nutzerquote wird durch D1-Ablehnungen nie gebucht.

Verbindlicher Annahmepfad:

0. Das Quellenlimit am Proxy und nach billiger Framing-/Größenprüfung im Kern das
   globale Vorab-Budget belasten, **vor** JSON-Parsen, Body-Digest und Ed25519.
   Auch unsignierte und ungültige Versuche verbrauchen die erreichten Vorab-Budgets.
1. HTTP-Framing, rohe Headerliste, Route, Größen, vollständigen Body und kanonische
   JSON-Form prüfen; `method`/`path` mit der empfangenen Route und `body_sha256`
   mit den Body-Bytes vergleichen. Keine Job-/Replay-/Audit-Mutation.
2. Erste Zeitprüfung mit der geprüften Core-Uhr unmittelbar vor der Signaturprüfung.
3. Ausschließlich den konfigurierten öffentlichen Ed25519-Schlüssel wählen und die
   Signatur prüfen. Erst danach den Dienst-Bucket belasten.
4. Service-Identität, Rate Limits, weitere bestehende Last- und Policy-Grants prüfen;
   `max_in_flight` vor der DB-Reservation erwerben. Die Start-Ablehnungen von H1
   schließen D1-Approval-Aufträge aus. Kein Umgehen der bestehenden Eingangsgates
   durch direkten Aufruf von `_dispatch()`.
5. **Eine gemeinsame Annahmetransaktion beginnen.** Zeit unmittelbar vor dem INSERT
   erneut prüfen und `(domain, issuer, nonce)` in der autoritativen Core-Tabelle
   reservieren: `ON CONFLICT (domain, issuer, nonce) DO NOTHING RETURNING domain,
   issuer, nonce`. Nur exakt eine neue Zeile mit exakt den Insert-Werten ist ein
   Gewinnernachweis. Null Zeilen oder abweichende Rückgabe sind REFUSE.
6. Die **zufällige Job-ID vor dem COMMIT** im Kern erzeugen, unabhängig von Nonce,
   Envelope-Digest oder Browserwerten. Handoff und Job-Ledger an diese ID binden.
   Nonce-Reservation, Job-Ledger, versioniert gebundenes Annahme-Audit-Event und
   bereits signierter Audit-Kopf werden **in derselben Transaktion** gespeichert.
   Nach jeder DB-/Lock-Wartezeit und unmittelbar vor COMMIT Zeit erneut prüfen;
   bei Ablauf die gesamte Annahmetransaktion zurückrollen.
7. Den dauerhaften synchronen COMMIT bestätigen und die bestehenden Audit-/Anker-
   Gates erfüllen. Erst danach ist sicherheitsrelevante Fortsetzung zulässig.
   Vor der Fortsetzung nochmals Zeit prüfen: Bei Ablauf wird der schon vorhandene
   Job über einen auditierten dauerhaften Refusal-Übergang beendet; seine
   Reservation bleibt bestehen. Er wird nicht als fehlender Job dargestellt.
8. Erst außerhalb der Transaktion und nach diesen Gates darf der Worker starten.
   `max_in_flight` wird auf jedem Ausgang freigegeben, auch bei Refusal oder
   unbekanntem COMMIT-Ausgang. Kein neuer Job und kein automatischer Retry als
   Reaktion auf einen Fehler.

Alle gleichzeitig annehmenden Core-Instanzen verwenden denselben schreibbaren
PostgreSQL-Primary und dieselbe autoritative Replay-Tabelle. Getrennte schreibbare
Kopien und Split-Brain sperren D1. Ein unbekannter Replikations-/Failover-Stand darf
nicht durch Weiterbetrieb übergangen werden; Wiederaufnahme braucht den geprüften
Betriebsablauf, Startabgleich und die Quarantäne oben.

Jede D1-Schreibtransaktion setzt `SET LOCAL synchronous_commit TO on` nach `BEGIN`
und prüft den wirksamen Wert unmittelbar vor COMMIT ohne dazwischenliegende Änderung.
`fsync=on` und `full_page_writes=on` werden beim Start und vor jeder Reservation
wirksam geprüft. Kann das Betriebsprofil bis zum COMMIT nicht garantiert werden,
sperrt D1. Dauerhafte WAL-Speicherung ist Pflicht; asynchroner COMMIT ist unzulässig.
Der externe Anker bestätigt weiterhin erst den bereits signiert gespeicherten Kopf.

Die Annahmetransaktion begrenzt `lock_timeout`, `statement_timeout` und
`idle_in_transaction_session_timeout` jeweils auf höchstens die unmittelbar
vor dem Replay-INSERT gemessene verbleibende Request-Gültigkeit und zusätzlich
auf einen konfigurierten Maximalwert zwischen 1 ms und 60 Sekunden.
Die PostgreSQL-Millisekundenwerte werden aus der tatsächlichen, nicht auf `now`
abgerundeten Restgültigkeit nach unten gerundet. Ist der kleinste Wert aus
Restgültigkeit und konfiguriertem Maximum kleiner als 1 ms oder fehlt das
Maximum, ist der Request REFUSE; ein Timeoutwert `0` darf nie an PostgreSQL
gehen, weil er dort die Begrenzung deaktiviert.
Zusätzlich gilt ein unabhängiges Gesamtzeitlimit einschließlich mehrerer Statements.
Timeout und SQL-/Serialisierungsfehler führen nicht zu automatischen Retries.
D1 muss vor Erschöpfung des gemeinsamen Core-Speichers sperren; die Schwelle umfasst
auch die zusätzlichen Audit-Events. Bereinigung der Replay-Tabelle ersetzt keine
Kapazitätsgrenze für die dauerhafte Audit-Kette.

### Versionierte Audit-Bindung und Ablehnungen (Gate 9)

Das neue D1-Audit-Bindungsprofil `d1-audit-v1` unterscheidet Annahme, Ablehnung ohne
Reservation und Bereinigung ausdrücklich. Es ist eine **neue Entwurfsanforderung**,
kein bereits unterstützter Typ von `geniusnew.audit.AuditEvent`; bestehende
Eventversionen werden nicht still erweitert oder umgedeutet. Für Annahme und
signaturgeprüfte Ablehnung enthält das Profil mindestens:

- `d1_envelope_sha256 = SHA256(envelope_bytes)`, als 64 lowercase Hex-Zeichen,
  mit Profilversion `d1-audit-v1`. Signaturlabel, Signatur und Transport gehören
  nicht dazu; der Wert ist nicht das oben ausgewiesene `signing_sha256`.
- Die Bindung an den verifizierten Dienst-Principal über dessen bestehenden
  `api_subject_sha256`, sowie die geltenden Policy-/Audit-Metadaten.
- Eine prüfbare Replay-Bindung: `replay_key_sha256` ist SHA-256 der ASCII-Kanonform
  von genau `{"domain":<Domäne>,"issuer":<Issuer>,"nonce":<64 Hex-Zeichen>}`
  nach dem JSON-Profil oben. Dazu kommt das signierte `expires_at` als Integer.
  Ein Envelope-Digest allein ersetzt diese Bindung nicht. Body, Raw-Envelope,
  Signatur, Bearer-Keys und private Schlüssel werden nicht ins Audit übernommen.

Für `D1_REQUEST_REFUSED` kommt ein eigener `d1_refusal_dedupe_sha256` hinzu,
dargestellt als 64 lowercase Hex-Zeichen und zusammen mit seiner Profilversion
`d1-refusal-dedupe-v1` im Event gebunden. Dieses Dokument konkretisiert damit den
versionierten, domänenseparierten Digest über `(issuer, nonce)` aus
[#161, §4.9.2 am Commit `7bbc07ec64618aef342f837680cfd4ea07736b35`](https://github.com/Kaancodm/GeniusNew/blob/7bbc07ec64618aef342f837680cfd4ea07736b35/docs/DATABASE.md#492-eine-annahme-transaktion-gate-9).
Das Profil berechnet SHA-256 über die ASCII-Kanonform von genau:

```text
{"domain":"geniusnew.portal-core.refusal-dedupe.d1.v1","issuer":<Issuer>,"nonce":<64 lowercase Hex-Zeichen>}
```

Es gilt das JSON-Profil oben: `ensure_ascii=True`, lexikografisch sortierte
Schlüssel, `,`/`:` ohne Whitespace und keine abschließenden Bytes. Die Domäne ist
eine feste Profilkonstante, kein Envelope- oder Caller-Wert. Als veränderliche
Werte gehen ausschließlich der verifizierte `issuer` und `nonce` ein.
`expires_at`, `key_id`, `audience`, Envelope-Digest und Ablehnungsgrund sind
ausgeschlossen; geänderte Werte erlauben kein zweites Event. Die Replay-Bindung
`replay_key_sha256` mit dem zusätzlichen Ablaufnachweis ersetzt diesen
Dedupe-Digest nicht.

Bei **Annahme** bindet das Event zusätzlich die zufällige Job-ID und den Handoff wie
im bestehenden Ledger-/Audit-Vertrag. Eine Reservation darf nur gemeinsam mit genau
ihrem Job und diesem Event samt signiertem Kopf sichtbar werden. Ein gesonderter
Nonce-COMMIT vor dem Job ist ausgeschlossen.

Bei **signaturgeprüfter Ablehnung ohne neue Reservation** — verbrauchter Nonce
oder Ablauf nach erfolgreicher Signaturprüfung — gilt folgender eigener Vertrag:

1. Eine bereits begonnene Annahmetransaktion muss zuerst eindeutig vollständig
   zurückgerollt sein. Sie darf weder Reservation noch Job oder Annahmeevent
   hinterlassen. Ein vorhandener Gewinner desselben Nonces bleibt unberührt.
2. Pro `(issuer, nonce)` entsteht **höchstens ein** `D1_REQUEST_REFUSED`-Event.
   Ein Event setzt die bereits erfolgreiche Dienst-Tokenbuchung dieser Anfrage
   voraus; die Regel zum leeren Bucket oben gilt vor diesem Auditpfad.
   Die Eindeutigkeit von `d1_refusal_dedupe_sha256` gilt transaktionsfest über
   Instanzen, Neustart, Schlüsselrotation und Replay-Bereinigung. Nur der erste
   auditierte Versuch erhält eine zufällige `attempt_id` im eigenen
   Versuch-Namensraum (keine Job-ID). Das Ereignis `d1-audit-v1` bindet die ID,
   den Envelope-Digest, Dienst-Principal, Replay-Bindung, Dedupe-Digest samt
   Profilversion, geprüfte Core-Zeit und `NONCE_CONFLICT` oder `REQUEST_EXPIRED`.
3. Nach Freigabe der äußeren Sperren speichert eine **eigene Ablehnungs-Transaktion**
   das erste Event und einen bereits signierten Kopf atomar; keine neue
   Reservation, kein Job, kein Handoff und keine Nutzerquotenbuchung. Der vorhandene
   Anker bestätigt wie bisher. Die verifizierte Audit-Kette ist die autoritative
   Deduplizierungsquelle. Prüfung auf einen vorhandenen Ablehnungsnachweis und
   Append erfolgen unter derselben datenbankgestützten Audit-Serialisierung über
   alle D1-Annehmer; konkurrierende Versuche dürfen kein zweites Event committen.
   Ein abgeleiteter Cache darf keinen ungeprüften oder veralteten Stand verwenden;
   beim Start wird der Nachweis aus der geprüften Kette rekonstruiert.
   Replay-Bereinigung entfernt diesen Nachweis nicht. Der Speicher-/Indexvertrag
   und die Rechte gehören in #160.
4. Weitere Ablehnungen derselben Identität `(issuer, nonce)` werden nur über
   **begrenzte Aggregatzähler mit festen Kategorien** erfasst, ohne neue Versuch-ID,
   Event, requestbezogenen Logeintrag oder eigene dauerhafte Buchung je Versuch.
   Eine zusätzliche unbegrenzte Tabelle oder Queue pro Nonce ist unzulässig.
   Ein unbekannter Deduplizierungszustand sperrt D1.
   Die erste Audit-Transaktion darf nach Request-Ablauf laufen (ohne Jobannahme),
   hat aber ein eigenes Gesamtzeitlimit und begrenzte DB-Kapazität.
5. Bei DB-/Audit-/Ankerfehler bleibt D1 fail closed. Eine erfolgreiche auditierte
   Ablehnung wird nicht behauptet, solange ihr Audit-COMMIT beziehungsweise
   dessen Ankerbestätigung ungeklärt ist; kein Job, kein Worker und kein Retry.

**Unbekannter Annahme-COMMIT ist ein anderer Fall.** Bei Verbindungsabbruch während
COMMIT kann die gesamte Einheit aus Reservation, Job und Audit bereits gespeichert
sein. Es gibt keinen Erfolg und keinen Workerstart; der Zustand bleibt **UNKNOWN**
bis zum bestehenden Recovery-/Ankerabgleich. Er darf nicht als nachgewiesene
Ablehnung ohne Reservation verbucht werden. Nach bestätigtem COMMIT bleiben Job
und Nonce auch bei Crash vorhanden; ein Neustart erfindet weder einen Ersatzjob
noch einen erneuten Workerstart.

Der Startabgleich prüft D1 zusätzlich zu den bestehenden B6-Invarianten:

- Jede vorhandene Reservation stimmt in Replay-Schlüssel-Digest und Ablaufzeit
  mit genau ihrer nicht bereinigten Annahme und deren Job überein; jede
  D1-Annahme hat umgekehrt ihre Reservation oder den passenden auditierten
  Bereinigungsnachweis unten. Widerspruch, Zusatzzeile oder fehlende Bindung
  verweigert den Start.
- Nur der ausdrücklich versionierte Typ `D1_REQUEST_REFUSED` darf mit Versuch-ID
  und ohne Job-/Handoff-Bindung vorkommen. Er erzeugt keine Reservation und keine
  Ausnahme für andere Audit-Events ohne Ledger. Unbekannte Versionen, Job-IDs
  als Versuch-IDs oder unzulässige Gründe verweigern den Start. Mehrere
  Refusal-Events für denselben `d1_refusal_dedupe_sha256`, eine falsche Profilversion
  oder fehlende, zusätzliche bzw. abweichende Bindungen gegenüber der aus der
  geprüften Audit-Kette rekonstruierten Deduplizierung verweigern den Start.
- Ein Ablauf **nach bestätigter Annahme** ist ein Refusal desselben vorhandenen
  Jobs mit eigener atomarer Ledger-/Audit-Änderung, keine Ablehnung ohne Job.

### Bereinigung, Rollen und DB-Abgleich (Gates 3 und 7)

Nach erfüllter Quarantäne dürfen Reservationen bereinigt werden, deren
`expires_at < now - 65` gilt: Sie sind **länger als 65 Sekunden abgelaufen**.
Das zusätzliche Aufbewahrungsfenster ist nicht die Start-Quarantäne und nicht bloß
`now >= expires_at`. Die Bereinigung nutzt die geprüfte Core-Uhr und verweigert
unklaren oder rückläufigen Zeitstand.

Die Bereinigung läuft **im Kernprozess** durch die bereits vorhandene
`AuditAuthority`; es entsteht kein zweiter Halter des privaten Audit-Schlüssels.
Nur eine separate `genius_d1_cleanup`-DSN mit eng begrenzten Rechten darf löschen.
Die normale Runtime-DSN behält nur `SELECT` und `INSERT`, weder `DELETE`,
`TRUNCATE`, DDL noch Rollenwechsel oder indirekte Cleanup-Rechte. Der Cleanup-Pfad
ist über die Runtime-DSN nicht erreichbar. Das Portal erhält keinen Core-DB-Zugriff. Löschung und zugehöriges
versioniertes Bereinigungs-Audit-Event samt bereits signiertem Kopf bilden eine
gemeinsame Transaktion; die Anker-Gates gelten auch hier.

Der Bereinigungsnachweis bindet genau das ursprüngliche Annahmeevent über dessen
Event-Digest, die Job-ID, `replay_key_sha256`, ursprüngliches `expires_at` und
die geprüfte Bereinigungszeit. Der Startabgleich lässt eine fehlende Reservation
nur mit diesem passenden Nachweis und eingehaltenem Löschalter zu; keine pauschale
Ausnahme für abgelaufene oder fehlende Zeilen. Job-Ledger und bisherige Audit-Historie
bleiben erhalten. Ein Fehler rollt die Bereinigung vollständig zurück; ein
unbekannter COMMIT-Ausgang wird vor jeder Wiederholung abgeglichen.

Die D1-spezifische Startprüfung von nicht deferrablem Primary Key, Zeilenform,
tatsächlicher Insert-Zieltabelle und effektiven Rechten ist nach der Entscheidung
vom 18.10.2026 **zulässig**. Sie ist kein allgemeiner Schema-Fingerprint im Sinne
der Entscheidung vom 30.09.2026. Unerwartete Trigger, Rules, RLS-Policies oder
Partitionen dürfen die Gewinnerprüfung nicht umgehen. Fehlende oder widersprüchliche
Katalog-/Rechteinformationen verweigern den Start; ein eigener Test fordert jede
Ablehnung. Es gibt keine automatische Neuinitialisierung oder Migration beim Start.

Der genaue Speicher-, Rollen- und Migrationsvertrag wird separat in
[DATABASE.md](DATABASE.md) über [Issue #160](https://github.com/Kaancodm/GeniusNew/issues/160)
angepasst. Dessen bisherige §4.9 und Start-/Restore-Verweise beschreiben noch den
abgelösten getrennten Nonce-COMMIT und Replay-Fortschrittsnachweis. Der gemeinsame
Annahme-/Ablehnungs-/Bereinigungsvertrag, die Zeit-Höchstmarke und der Startabgleich
müssen vor aktivem DB-Code in beiden Entwürfen zusammenpassen und freigegeben sein.
Die 65-s-Regel hebt keine bestehende Ledger-/Audit-/Anker- oder Rollengrenze auf.

## Portal-Retry und Abnahme

Timeout und unbekannter Ausgang sind **unbekannt**, nicht Erfolg. Das Portal darf weder
denselben signierten Request automatisch erneut senden noch mit neuem Nonce dieselbe
menschliche Aktion automatisch erneut ausführen. Eine erneute Handlung braucht eine
ausdrückliche Nutzerentscheidung und einen separaten Status-/Idempotenzvertrag. D1
schützt denselben signierten Request gegen Replay; Ende-zu-Ende-Idempotenz einer
menschlichen Absicht ist nicht Teil von D1.

Vor einem aktiven Code-PR stehen der erneute unabhängige Security-Review durch
Claude **am exakten neuen Head-SHA** (Gate 6), das freigegebene Lastprofil und der
mit diesem Text abgeglichene DB-Entwurf. Die Design-Vorprüfung für Kryptografie
durch Gemini und die übrigen Vorgaben aus [COLLABORATION.md](COLLABORATION.md)
gelten weiterhin.

Für die spätere Implementierung sind insbesondere folgende nachweislich wirksamen
Tests erforderlich; diese Doku-Überarbeitung führt sie nicht als bereits bestanden:

| Bereich | Geforderter Nachweis |
| --- | --- |
| H1 und L2 | Jede der drei unzulässigen Dienst-Konfigurationen verweigert für sich den Start; gültiges Profil startet erst nach allen Gates. Der D1-Verifier verweigert einen privaten Schlüssel. |
| Geschlossene Beta vor D3 | Der Portal-Zugang lässt ausschließlich Kaan als einzigen Portal-Nutzer zu. Ein unbekannter oder zweiter Nutzer erhält keinen Zugang und löst keinen D1-Auftrag aus. Der Nachweis erfolgt am Portal; die Dienstsignatur im Kern ersetzt ihn nicht. |
| Byte- und Signaturprofil | Alle bestehenden positiven/negativen Vektoren sowie fester Seed, öffentlicher Schlüssel und erwartete Ed25519-Signatur; manipulierte Signatur beziehungsweise Nachricht REFUSE. |
| Strikter Verifier und Bezeichner | Bezeichner mit abschließendem Zeilenumbruch, `S >= L`, nicht kanonische Punkte, Identitätspunkte, Torsionsanteile und nur kofaktorisiert gültige Signaturen werden verweigert; der Festwerttest allein genügt nicht. |
| HTTP/Proxy | Gleiche, verschiedene und case-insensitive Header-Duplikate vor Dictionary-Bildung und Body-Lesen; Framing, Misch-Auth und nachgewiesene nginx-Erhaltung oder frühe Ablehnung bei HTTP/1.1 und HTTP/2. |
| Uhr/Quarantäne | Normalstart, Crash und Restore bleiben volle 65 Sekunden nach den Start-, Anker- und Uhrprüfungen gesperrt, einschließlich Randwerten unmittelbar davor und danach; langsame/rückläufige Core-Uhr oder fehlende Höchstzeit verkürzen die Quarantäne nicht. |
| Budgets | Quellen- und globales Limit greifen vor teurer Arbeit und gelten instanzübergreifend; ungültige Signatur und erste Zeitablehnung belasten nie den Dienst-Bucket. Signaturgeprüfte Replay-/Zeitablehnung belastet ihn soweit verfügbar genau einmal, ohne Nutzerquotenbuchung oder Rückerstattung. Gescheiterte anfängliche Buchung erzeugt kein Audit oder requestbezogenes Logging; ein erfolgreich verbrauchtes letztes Token genügt für den erforderlichen ersten Nachweis, ohne zweite Buchung. |
| Portal/Ingress | Geschützter Zugang für das vorgesehene Portal-Backend greift vor dem Vorab-Budget. Portal-Signierbudgets pro geprüfter Nutzerreferenz über Sessions hinweg und insgesamt gelten instanz- und neustartfest; auch fehlgeschlagene/verweigerte Signierversuche zählen. Unbekannter Budgetzustand verweigert das Signieren. |
| DB-Timeouts | Millisekundenlimits werden aus der tatsächlichen Restgültigkeit abgerundet; weniger als 1 ms oder fehlendes Maximum verweigern. Kein Timeoutwert `0` deaktiviert eine Grenze; das Gesamtzeitlimit begrenzt mehrere Statements. |
| Atomare Annahme | Parallele Inserts liefern genau einen Gewinner; Crashpunkte vor und nach INSERT, Job-/Audit-Schreiben, COMMIT und Ankerbestätigung erlauben keine Teilzustände oder doppelten Jobs. Unbekannter COMMIT bleibt UNKNOWN. |
| Auditierte Ablehnung | Verbrauchter Nonce und Ablauf nach Signaturprüfung erzeugen nach Rollback höchstens einen getrennten Ablehnungsnachweis je `(issuer, nonce)` ohne neuen Job, auch parallel, nach Neustart, Schlüsselrotation und Replay-Bereinigung. `d1_refusal_dedupe_sha256` samt Profil wird bytegenau geprüft; geänderte `expires_at`, `key_id`, `audience`, Envelope-Digest oder Grund erlauben kein zweites Event. Wiederholungen erzeugen nur begrenzte Aggregatzähler, kein requestbezogenes Logging. Crash oder Auditfehler kann keinen Worker starten. Ablauf nach bestätigter Annahme bleibt ein Refusal desselben Jobs. |
| Bereinigung/Abgleich | Löschung erst bei `expires_at < now - 65`, nicht bei Gleichheit, nur im Kernprozess über die getrennte Cleanup-DSN und mit atomarem Audit der vorhandenen `AuditAuthority`. Entfernte oder zusätzliche Reservationen, falsche Bindungen und unzulässige Refusal-Eventformen verweigern den Start; ein passender Bereinigungsnachweis erlaubt den definierten Fall. |

Die bestehenden Last-, Policy-, Approval-, Audit- und Anker-Gates müssen unverändert
wirksam bleiben. Jede neue Ablehnung braucht einen Test, der ihr Fehlen bemerkt;
die betroffenen Module gehören in `scripts/refusals.py::GUARDED`. Ein DB-Code-PR
benötigt zusätzlich `Claude DB Review: APPROVED` am exakten Head und grüne
`contracts`.

### Gate-Stand dieses Entwurfs

**ERLEDIGT bezeichnet den ausgearbeiteten Entwurfsvertrag, keine Code-, Betriebs-
oder Merge-Freigabe. D1 bleibt HOLD.** Insbesondere wurde der Rest von Gate 9 nicht
allein durch die Entscheidung zum Annahmepfad geschlossen: Der getrennte
Ablehnungsvertrag und sein gezielter Startabgleich sind jetzt oben ausdrücklich
beschrieben und müssen den unabhängigen Review bestehen.

| Gate | Stand und verbleibende Prüfung |
| --- | --- |
| 1 – D1/D3-Trennung | Für D1 entschieden und beschrieben: geschlossene Beta mit Kaan als einzigem Portal-Nutzer, gemeinsamem Principal, gemeinsamer Quote und gemeinsamem Job-Namensraum; nur risikoarme Tools, keine Freigaben, drei H1-Start-Ablehnungen. Portal-Zugangsbeschränkung ist vor Aktivierung nachzuweisen. Ein zweiter Portal-Nutzer setzt D3 voraus; Aliasnachweis und Lifecycle bleiben ein eigener D3-Vertrag. |
| 2 – Core-Uhr | Betriebsgrenze: NTP, Prozess-Monotonieprüfung und PostgreSQL-Höchstzeit. Nachweise und eigener Eintrag in `SECURITY.md` bleiben offen; kein unabhängiger kryptografischer Zeitnachweis gefordert. |
| 3 – Replay-Restore | **ERLEDIGT im Entwurf:** 65-s-Start-Quarantäne ersetzt den Replay-Fortschrittsnachweis; auditierte Bereinigung nach zusätzlichem Ablaufabstand. Tests, DB-Abgleich und Betriebsnachweis bleiben vor Aktivierung erforderlich. |
| 4 – Proxy | Proxy erhält Header; Kern verweigert Duplikate auf der rohen Liste. Nachweis der nginx-Vorlage einschließlich HTTP/2 bleibt offen. |
| 5 – Lastbudget | Pro Quelle am Proxy plus global im Kern; zusätzliche Portal-Signierbudgets bleiben erforderlich. Konkrete Zahlen, Speichergrenze, DB-Kapazität für Ablehnungs-Audit, Kaans Freigabe und Lasttests bleiben offen (#160, Portal-Nachweis unter Gate 10). |
| 6 – Unabhängiger Review | **OFFEN:** erneuter Claude-Security-Review am exakten neuen Head; Selbstprüfung schließt dieses Gate nicht. |
| 7 – Startprüfung | D1-Prüfung von PK, Zeilenform und effektiven Rechten ist zulässig; kein offener Grundsatzentscheid über einen allgemeinen Schema-Fingerprint. Umsetzung und Negativtests bleiben erforderlich. |
| 8 – Ablehnungsbudgets | Buchungs- und Testvertrag beschrieben: vor Signatur nur Vorab-Budgets; signaturgeprüfte Replay-/Zeitablehnungen zusätzlich Dienst-Bucket, niemals Nutzerquote. |
| 9 – Audit/Recovery | **ERLEDIGT im Entwurf:** gemeinsame Annahmetransaktion, zufällige Job-ID vor COMMIT, versionierte Envelope-/Replay-Bindung; eigener Ablehnungs-Auditpfad gemäß #161 mit höchstens einem Event je `(issuer, nonce)` über `d1_refusal_dedupe_sha256` und gezieltem Abgleich. Wiederholungen nur begrenzt aggregiert; bei leerem Dienst-Bucket kein Audit. UNKNOWN und Ablauf nach COMMIT getrennt. Freigabe erst nach DB-Abgleich (#160) und Gate 6. |
| 10 – Geschützter Ingress und Portal-Budgets | **OFFEN:** Nachweis des vor dem Vorab-Budget wirksamen geschützten Ingress, der anonyme öffentliche Last auf die D1-Route ausschließt; konkrete und geprüfte instanz- und neustartfeste Portal-Budgets pro Nutzerreferenz und insgesamt vor dem Signieren. |
