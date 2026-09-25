# GeniusNew Handoff v2

Dieses Dokument ergänzt `schemas/handoff-v2.schema.json`. Das Schema beschreibt
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
Hex-Darstellung (128 Zeichen) der Ed25519-Signatur über den kanonischen
Wire-Body ohne das Feld `signature`.

Version 1 war mit HMAC-SHA-256 signiert. Ein v1-Handoff wird abgelehnt; es gibt
keinen Fallback.

## Schlüsselgrenze

Der Signierschlüssel (`HandoffSigner`) wird aus dem Handoff-Integritätsschlüssel
unter eigenem Label abgeleitet; das Root-Secret bleibt das einzige Geheimnis. Er
wird ausschließlich serverseitig zur Laufzeit bereitgestellt und darf weder in
Handoff-Daten, Logs, Tests mit Produktivwerten noch im Repository erscheinen.

Nur der Orchestrator hält den `HandoffSigner`. Gateway, Ergebnisprüfung,
Approval-Grenze und Audit-Rolle halten den `HandoffVerifier`: einen 32-Byte-
Ed25519-Public-Key ohne Signiermethode. Gateway und Ergebnisprüfung lehnen es
ab, mit dem Signer konstruiert zu werden. Wer einen Handoff prüfen kann, kann
damit keinen mehr ausstellen; mit HMAC in v1 konnte er das.

Bei einer Schlüsselrotation werden noch nicht abgelaufene Handoffs neu
ausgestellt; Version 2 enthält wie Version 1 absichtlich keine Key-ID und
akzeptiert daher keinen Fallback auf alte Schlüssel.

Serverseitige `subject`-Werte werden als exakte UTF-8-Bytefolge verglichen.
Sie dürfen Unicode enthalten, werden aber nicht normalisiert: Visuell ähnliche,
unterschiedlich kodierte Werte sind verschiedene Identitäten. Nicht als UTF-8
kodierbare Python-Strings werden als ungültige Konfiguration abgelehnt.

## Prüfung am Gateway

`validate()` akzeptiert nur Bytes, die exakt dem kanonischen Wire-Format
entsprechen. Es lehnt ab, bevor ein Worker erreicht wird, wenn mindestens eine
der folgenden Bedingungen zutrifft:

- Feldmenge, JSON-Typen oder JSON-Serialisierung sind ungültig oder mehrdeutig.
- Payload-Hash oder Ed25519-Signatur stimmen nicht.
- `job_id`, `user_id`, Worker, Tier, Tools, Sandbox-Profil oder Policy-Version
  stimmen nicht mit dem serverseitigen Grant und der Policy überein.
- Der Aussteller ist unbekannt, die Policy enthält unbekannte Tools oder Profile,
  oder der Handoff liegt vor seiner Ausgabezeit beziehungsweise nach Ablaufzeit.
- Für den direkten `validate()`-Pfad lautet der Zustand `PENDING_APPROVAL`.

Approval-pflichtige Handoffs werden nicht umsigniert oder in-place auf
`APPROVED` geändert. Das Gateway validiert denselben unveränderten Wire mit
`validate_pending()`, konsumiert einen einmaligen Approval-Token gegen den daraus
abgeleiteten Scope und mintet anschließend einen separaten `DispatchPermit`.
`WorkerRunner` akzeptiert nur diesen Permit. Ein nackter `Handoff` mit
`PENDING_APPROVAL` bleibt daher nicht dispatchfähig; die Approval-Entscheidung lebt
außerhalb des signierten Handoff-Bodys und ist über den Permit/Receipt gebunden.

Eine Signatur ersetzt die serverseitige Policy-Bindung nicht: Auch ein mit
dem korrekten Schlüssel signierter Handoff wird abgelehnt, sobald er von der
aktuellen Policy abweicht.

## Weiterführende Grenzen

Approval-Token mit Scope und Einmalverbrauch, Gateway-Permits, Worker-Ausführung,
Prozessisolation und Ergebnisvalidierung sind inzwischen als getrennte Grenzen im
Repository implementiert. Die Ergebnissignatur ist noch HMAC und folgt als eigener
Schritt; persistente Audit-/Deployment-Infrastruktur bleibt eine spätere Phase.
