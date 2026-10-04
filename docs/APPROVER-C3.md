# Freigebendenrolle im Kern (Gate C3)

Dies ist die konkrete Implementierung für die technische Prüfung von C3. Sie aktiviert
keine Rolle in einer laufenden Installation. Der Produktionsdienst nutzt die
persistenten B4–B6-Speicher und bindet Grants atomar an die signierte Audit-Kette.

## Serverseitige Identität und Rolle

Der HTTP-Eingang löst den API-Key wie bisher ausschließlich über `[principals]`
zu einem `subject` auf. Eine optionale Tabelle `[approvers]` bindet diesen
Subject serverseitig an die Nutzeridentität der freigebenden Person:

```toml
[principals]
REPLACE_WITH_SHA256_OF_THE_USER_KEY = "subject-user"
REPLACE_WITH_SHA256_OF_THE_APPROVER_KEY = "subject-approver"

[approvers]
subject-approver = "user-approver"
```

Die Platzhalter sind keine gültigen Key-Digests; das Beispiel kann nicht starten.
Ein Freigebenden-Subject muss einen Principal besitzen. Er benötigt keinen
Worker-Grant. Hat er auch einen Grant, muss dessen `user_id` genau zur
Freigebendenidentität passen; eine widersprüchliche Konfiguration wird abgelehnt.
Nutzeridentitäten der Auftraggebenden stammen aus `Policy.Grant.user_id`, der
signierte Pending-Handoff bindet dieselbe Identität.

Eine Freigabe vergleicht diese serverseitigen Nutzeridentitäten. Deshalb kann
ein zweiter Subject derselben Person deren Auftrag ebenfalls nicht freigeben.
Die korrekte Zuordnung mehrerer Zugangsschlüssel beziehungsweise Subjects zu einer
Person bleibt eine Eigenschaft der vertrauenswürdigen Konfiguration. Eine
fälschlich als zweite Person provisionierte Identität lässt sich im Kern nicht
als dieselbe natürliche Person erkennen.

Fehlt `[approvers]` oder ist die Tabelle leer, ist niemand freigebend autorisiert
und die neue Route existiert nicht. Bestehende Konfigurationen ohne die Tabelle
bleiben gültig. Für einen Auftrag ist weiterhin ein Policy-Grant erforderlich.

Alle konfigurierten Freigebenden dürfen fremde wartende Jobs freigeben. C3 führt
keine Mandanten- oder gruppenspezifische Zuordnung ein. Portalrollen allein
erteilen keine Core-Rolle. Das Portal muss diesen Vertrag über seine später
geprüfte Server-zu-Server-Anbindung verwenden; Browseridentitäten sind hier keine
Autorität.

## HTTP-Ablauf

1. Der Auftraggebende sendet `POST /jobs` mit einem Body `{"text":"…"}`.
   Bei einem approval-pflichtigen Grant lautet die Antwort `202` mit
   `job_id` und `status: "PENDING_APPROVAL"`.
2. Ein anderer, konfigurierter Freigebender sendet
   `POST /approvals/<job_id>/grant`, authentisiert mit seinem eigenen
   `Authorization: Bearer <key>`, und mit dem Body genau `{}`.
3. Erst nach verankerter `APPROVAL_GRANTED`-Entscheidung kommt die Antwort
   `202` mit `job_id`, `status: "APPROVAL_GRANTED"` und dem einmal
   ausgegebenen `approval_token` als 64 Hex-Zeichen.
4. Der Auftraggebende präsentiert den Token wie bisher über
   `POST /jobs/<job_id>/approve`, Body `{}`, Header
   `X-Approval-Token: <token>`. Nur dessen authentisierter Subject kann
   diesen Job ausführen. Gateway und Ergebnisprüfung bleiben unabhängig.

Die Job-Kennung im Pfad ist nur eine Referenz auf einen bereits serverseitig
ausgestellten Job. Sie erteilt weder Identität noch Rechte. Alle Felder im
Freigabe-Body werden abgelehnt, auch `subject`, `user_id`, `role`,
`tier`, `tools`, `ttl_seconds` oder ein Token.

Fremde Rolle, eigener Auftrag, unbekannter oder abgelaufener Job und eine zweite
**aktive** Entscheidung ergeben einheitlich `409 {"error":"REJECTED"}`. Fehlende oder
unbekannte Zugangsschlüssel ergeben `401`; ein fehlerhafter Body ergibt `400`.
Rate-, Body- und Gleichzeitigkeitsgrenzen gelten auch für diese Route.

## Einmalentscheidung und Fehler

Im Produktionspfad sperrt der Grant die Job-Zeile in PostgreSQL. Solange ein
`GRANTED`-Token für denselben Scope noch gültig ist, verweigert der Dienst jeden
weiteren Grant, auch nach Neustart oder bei einer zweiten Instanz. Der Default-TTL
des Tokens ist 30 Sekunden, höchstens bis zum Handoff-Ablauf. Geht die Antwort nach
dem dauerhaften Grant verloren, darf nach Token-Ablauf ein neuer Grant für den noch
wartenden Job entstehen; der alte Token ist dann ungültig. Nach Handoff-Ablauf wird
der Job wie bisher atomar und auditiert abgelehnt. Die Demo ohne PostgreSQL nutzt
denselben Aktivitätsgrundsatz unter einem prozesslokalen Lock.

`Service.approve(job_id, approver_subject=...)` prüft dieselbe Rolle und
Selbstfreigabe auch für direkte serverseitige Aufrufe. Es gibt keine implizite
Operatorrolle. Der Token bleibt zufällig, scope-gebunden, zeitlich begrenzt und
einmal verbrauchbar; gespeichert wird weiterhin nur sein Digest.

Der bestehende Audit-Vertrag bindet Grant und Verbrauch an ihre Record-Hashes und
den Job-Scope. Das `APPROVAL_GRANTED`-Event der Version 2 bindet zusätzlich den
SHA-256-Digest des authentifizierten Freigebenden-Subjects als API-Principal.
Die Zuordnung dieses Subjects zur natürlichen Person stammt weiterhin aus der
serverseitigen Konfiguration.

TLS und vertrauliche Token-Zustellung bleiben Betriebsanforderungen; echte
Zugangsdaten gehören nicht ins Repository.
