# GeniusNew Constitution v1 — Draft

Status: DRAFT

Diese Datei definiert die Sicherheitsinvarianten für den ersten Runtime-Kern von GeniusNew. Sie ist bewusst neu formuliert und kein Quellcode-Import aus einem anderen Repository.

## 1. Identität

Jeder ausführbare Job MUSS mindestens folgende Identitäten eindeutig tragen:

- `job_id`
- `user_id`
- `orchestrator_id`
- `worker_agent_id`

`user_id` ist ein sicherheitsrelevantes Pflichtfeld und darf nicht aus frei wählbaren Client-Metadaten stammen.

## 2. Autorisierung

Client-Eingaben dürfen niemals direkt folgende Werte bestimmen:

- Berechtigungsstufe
- Tool-Allowlist
- Sandbox-Profil
- Ziel-Worker
- Approval-Status

Diese Werte werden serverseitig aus vertrauenswürdiger Identität, Policy und Systemzustand abgeleitet.

## 3. Handoff-Grenze

Ein Handoff zwischen Orchestrator, Policy/Gateway und Worker ist eine explizite Serialisierungsgrenze.

Anforderungen:

- unbekannte Felder ablehnen
- Typen strikt validieren
- Payload-Integrität kryptografisch binden
- Ablaufzeit erzwingen
- keine impliziten Defaults, die Rechte erweitern
- kein bereits validiertes In-Memory-Objekt als Ersatz für eine unabhängige Prüfung verwenden

## 4. Fail-Closed

Unbekannter Zustand bedeutet Ablehnung.

Insbesondere:

- unbekannte Identität → deny
- unbekannte Policy-Version → deny
- unbekanntes Tool → deny
- unbekanntes Sandbox-Profil → deny
- ungültiger oder abgelaufener Handoff → deny
- fehlende sicherheitsrelevante Konfiguration → deny

## 5. Approval

Sicherheitsrelevante Aktionen dürfen einen expliziten Zustand `PENDING_APPROVAL` verwenden.

Ein Job in `PENDING_APPROVAL` darf keinen Worker oder externen Effekt erreichen.

Freigaben müssen:

- an genau den Job gebunden sein
- einen klaren Scope besitzen
- zeitlich begrenzt sein
- nicht wiederverwendbar sein, wenn sie als einmalige Freigabe definiert wurden
- auditierbar sein

## 6. Tools und Capabilities

Tool-Nutzung folgt expliziten Allow-Lists.

Ein Worker darf kein Tool verwenden, das nicht für genau diesen Job freigegeben wurde. Policy kann Rechte reduzieren, aber nie stillschweigend erweitern.

## 7. Audit

Sicherheitsrelevante Entscheidungen müssen nachvollziehbar sein.

Audit-Einträge sollen enthalten:

- Job-/Trace-Bezug
- Entscheidungsart
- Policy-/Constitution-Version
- Ergebnis/Code
- Integritätsinformationen
- Zeitbezug

Audit-Einträge dürfen keine Secrets und standardmäßig keine Roh-Payloads enthalten.

## 8. Trennung von Entscheidung und Ausführung

Keine Instanz soll ihre eigene sicherheitsrelevante Entscheidung allein bestätigen dürfen.

Mindestens folgende Rollen bleiben logisch getrennt:

- Orchestrierung
- Policy-/Gateway-Prüfung
- Ausführung
- Ergebnis-/Verhaltensprüfung
- Audit/Forensik

## 9. Public-Repository-Anforderung

Da GeniusNew öffentlich ist:

- keine produktiven Secrets
- keine echten Zugangsdaten
- keine privaten Infrastrukturdetails, die nicht veröffentlicht werden sollen
- keine personenbezogenen Daten
- Beispielkonfigurationen nur mit klaren Platzhaltern

## 10. Minimaler v1-Kern

Die erste Runtime-Version soll nur so viel implementieren, wie nötig ist, um folgende Kette reproduzierbar zu testen:

1. trusted identity resolution
2. Handoff erzeugen
3. unabhängige Contract-Validierung
4. Policy-Entscheidung
5. optionales Approval-Gate
6. deterministische Test-Worker-Ausführung
7. unabhängige Ergebnisvalidierung
8. Audit-Eintrag und Integritätsprüfung

Alles Weitere bleibt außerhalb von v1, bis diese Kette stabil, testbar und nachvollziehbar ist.
