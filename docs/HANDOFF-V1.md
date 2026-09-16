# GeniusNew Handoff v1

Dieses Dokument ergänzt `schemas/handoff-v1.schema.json`. Das Schema beschreibt
die Struktur; dieses Dokument definiert die Bindung der Struktur an einen
vertrauenswürdigen Aussteller.

## Erzeugung

`issue()` akzeptiert nur die untrusted Nutzlast `{ "text": "..." }`. Identität,
Tier, Tools, Sandbox-Profil, Worker, Policy-Version und Approval-Zustand werden
allein aus einem serverseitigen `Policy`-/`Grant`-Objekt abgeleitet.

Der Wire-Body ist das kanonische JSON aller Felder. Kanonisches JSON bedeutet:

- Objekt-Schlüssel lexikographisch sortieren
- keine Leerzeichen außerhalb von Stringwerten
- ASCII-Escapes für Nicht-ASCII-Zeichen
- keine nicht-endlichen Zahlen

`payload_sha256` ist die kleingeschriebene SHA-256-Hashdarstellung des
kanonischen `payload`-Objekts. `signature` ist die kleingeschriebene
HMAC-SHA-256-Hashdarstellung des kanonischen Wire-Body ohne das Feld
`signature`.

## Schlüsselgrenze

Der HMAC-Schlüssel wird ausschließlich serverseitig zur Laufzeit bereitgestellt.
Er ist mindestens 32 Byte lang und darf weder in Handoff-Daten, Logs, Tests mit
Produktivwerten noch im Repository erscheinen. Aussteller und prüfendes Gateway
benötigen denselben aktiven Schlüssel. Bei einer Schlüsselrotation werden noch
nicht abgelaufene Handoffs neu ausgestellt; Version 1 enthält absichtlich keine
Key-ID und akzeptiert daher keinen Fallback auf alte Schlüssel.

Serverseitige `subject`-Werte werden als exakte UTF-8-Bytefolge verglichen.
Sie dürfen Unicode enthalten, werden aber nicht normalisiert: Visuell ähnliche,
unterschiedlich kodierte Werte sind verschiedene Identitäten. Nicht als UTF-8
kodierbare Python-Strings werden als ungültige Konfiguration abgelehnt.

## Prüfung am Gateway

`validate()` akzeptiert nur Bytes, die exakt dem kanonischen Wire-Format
entsprechen. Es lehnt ab, bevor ein Worker erreicht wird, wenn mindestens eine
der folgenden Bedingungen zutrifft:

- Feldmenge, JSON-Typen oder JSON-Serialisierung sind ungültig oder mehrdeutig.
- Payload-Hash oder HMAC-Signatur stimmen nicht.
- `job_id`, `user_id`, Worker, Tier, Tools, Sandbox-Profil oder Policy-Version
  stimmen nicht mit dem serverseitigen Grant und der Policy überein.
- Der Aussteller ist unbekannt, die Policy enthält unbekannte Tools oder Profile,
  oder der Handoff liegt vor seiner Ausgabezeit beziehungsweise nach Ablaufzeit.
- Der Zustand lautet `PENDING_APPROVAL`.

Eine HMAC-Signatur ersetzt die serverseitige Policy-Bindung nicht: Auch ein mit
dem korrekten Schlüssel signierter Handoff wird abgelehnt, sobald er von der
aktuellen Policy abweicht.

## Nicht Teil von v1

V1 stellt nur die Contract-Grenze bereit. Ein Approval-Token mit Scope und
Einmalverbrauch, Audit-Persistenz, Worker-Ausführung und Ergebnisvalidierung
werden in späteren, getrennt geprüften Phasen ergänzt.
