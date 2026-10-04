# D1: Portal–Kern-Vertrag (Entwurf zur unabhängigen Prüfung)

**Status:** Entwurf auf Basis von [Issue #92](https://github.com/Kaancodm/GeniusNew/issues/92).
Dieser Text ist noch keine Betriebsfreigabe. Vor einem aktiven Code-PR müssen der
Vertrag unabhängig geprüft, die offenen Punkte von Kaan entschieden und ein
dauerhafter Replay-Speicher im freigegebenen Datenbankdesign beschrieben werden.

## Ziel und Vertrauensgrenze

Das Portal-Backend auf Vercel stellt `POST /jobs` an den Kern. Der Browser darf nur
den Auftragstext liefern. `subject`, `tier`, `user_id`, `tools`, `job_id` und ähnliche
Autoritätsfelder sind im HTTP-Body verboten; der Kern lehnt sie ausdrücklich ab.
Der Kern erzeugt die Job-ID selbst. Portal-Login und Portalrollen sind keine
Core-Berechtigung. Der private Portal-Signierschlüssel liegt nur beim Portal-Backend;
der Kern erhält ausschließlich dessen öffentlichen Ed25519-Schlüssel.

Für D1 schlägt dieser Entwurf einen dedizierten, im Kern konfigurierten
**Portal-Service-Principal** vor. Der Kern bestimmt dessen `subject` aus der
serverseitigen Zuordnung zum verifizierten Portal-Schlüssel, nie aus dem Request.
Damit beweist D1 den aufrufenden Dienst, noch keine einzelne Portal-Nutzeridentität.
Die Isolation von Nutzeraufträgen und Approvals in D3 braucht daher eine eigene,
vor der Aktivierung entschiedene Autorisierungsregel. Ein gemeinsamer Principal darf
nicht stillschweigend als Nachweis einer Core-seitigen Nutzertrennung gelten.

## Signierter Request

Der Portal-Server erzeugt pro Versuch einen neuen, kryptografisch zufälligen
32-Byte-Nonce. Ein kanonisch kodiertes ASCII-JSON-Envelope enthält ausschließlich:

| Feld | Wert und Bindung |
| --- | --- |
| `version`, `kind` | feste Protokollversion und `REQUEST` |
| `issuer`, `audience`, `key_id` | fest konfigurierte Portal-/Kern- und Schlüsselidentität |
| `nonce` | 64 lowercase Hex-Zeichen; einmalig je `issuer` und Gültigkeitsbereich |
| `issued_at`, `expires_at` | ganze Unix-Sekunden; höchstens 60 Sekunden Laufzeit |
| `method`, `path` | exakt `POST` und `/jobs`, ohne Query, Fragment oder Umschreibung |
| `body_sha256` | SHA-256 über die tatsächlich gesendeten Body-Bytes |
| `signature` | Ed25519 über Domänentrennungslabel und alle vorigen Felder |

Der Body ist kanonisches JSON mit genau einem nichtleeren String `text`, höchstens
16 KiB. Unbekannte, doppelte oder falsch typisierte Felder, nichtkanonische Bytes,
ungültige Unicode-Skalare, mehrdeutige URL-Kodierung und abweichende Body-Bytes
werden vor der Replay-Reservation verweigert. Das Envelope ist höchstens 4096 Bytes.
Der HTTP-Adapter muss genau eine eindeutig kodierte Envelope-Header-Instanz
akzeptieren; doppelte Header, ein gleichzeitiger Bearer- und Signaturpfad sowie
Proxy-Umschreibungen werden verweigert. Die konkreten Header- und
Fehlerstatus-Bytes werden vor der Implementierung im Protokoll fixiert.

Der Kern wählt den öffentlichen Schlüssel ausschließlich aus seiner eigenen,
begrenzten Konfiguration anhand der erwarteten Bindung aus `issuer`, `audience` und
`key_id`. Werte im Envelope dürfen weder neue Schlüssel laden noch die zugeordnete
Core-Identität bestimmen. Eine Signatur für eine andere Richtung, Version,
Zielinstanz oder Route gilt nicht. Der Transport braucht zusätzlich TLS mit
geprüftem Servernamen; die Signatur ersetzt keinen geschützten Netzwerkkanal.

## Ablauf, Replay und Fehler

Die Kernuhr muss innerhalb des signierten Zeitfensters liegen. Ein abgelaufener
oder erst zukünftig gültiger Request wird verweigert. Nach vollständiger
Signaturprüfung reserviert der Kern `(Protokolldomäne, issuer, nonce)` in einer
dauerhaften Core-PostgreSQL-Tabelle mit Unique Constraint. Derselbe Nonce bleibt
auch bei geändertem Body, Digest, Schlüssel oder Job-ID verbraucht. Zwei
gleichzeitige Instanzen dürfen höchstens eine Reservation gewinnen. Die Prüfung
und Reservation sind transaktional; ein nicht erreichbarer oder beschädigter
Replay-Speicher führt zu einer Ablehnung, nie zu einem In-Memory-Fallback.

Der Nonce wird vor der Ausführung verbraucht. Fällt der Dienst nach der Reservation
und vor einer Antwort aus, ist der ursprüngliche Request nicht erneut ausführbar.
Ein Client darf aus einem Timeout keinen Erfolg ableiten. D1 verspricht damit
Replay-Schutz für den signierten Request, noch keine Ende-zu-Ende-Deduplikation
derselben menschlichen Absicht bei einem neuen Nonce. Der genaue Ablauf zwischen
Replay-Reservation und bestehender Job-Reservation muss vor Code gegen Crashpunkte
festgelegt und getestet werden. Ein Worker läuft nie in einer offenen
Replay-Transaktion.

Die Datenbank-Migration, Runtime-Rechte, Aufbewahrung und Bereinigung der
Nonce-Zeilen gehören in `docs/DATABASE.md` und brauchen dessen vorgesehenen
Entwurfs- und Reviewweg. Bereinigung darf einen noch gültigen Nonce nie wieder
freigeben; Schlüsselrotation darf das ebenfalls nicht. Das Portal braucht einen
eigenen Mechanismus, um unklare Antworten sicher anzuzeigen und keine automatische
Wiederholung desselben Requests auszulösen.

## Abnahme vor Aktivierung

- Unabhängiger Review dieses Vertrags am exakten Commit; offene Architekturfragen
  entscheiden Kaan und die zuständigen Dokumente halten die Entscheidung fest.
- Tests verweigern Browser-Autoritätsfelder, manipulierte Body-/Route-/Signatur-
  Bytes, unbekannte Schlüssel, falsche Zielinstanz, abgelaufene und zukünftige
  Zeiten, Replay nach Neustart, parallelen Replay und DB-Ausfall. Jede neue
  Code-Ablehnung hat einen Refusal-Test und das Modul steht in `GUARDED`.
- Ein echter HTTP-Test zeigt, dass nur ein gültiger, frisch signierter Portal-Request
  den konfigurierten Core-Principal erreicht; die bestehende direkte API-Key-Route
  bleibt getrennt und verweigert Mischformen.
- Ein DB-Code-PR braucht das unabhängige Review am exakten Head und grüne
  `contracts`; Deployment, Schlüsselverteilung und Netzfreigabe brauchen Kaans OK.

## Offene Entscheidungen

1. Wie wird in D3 ein eingeloggter Portal-Nutzer auf einen Core-Principal und
   dessen Approvals abgebildet, ohne Browserwerte zur Core-Autorität zu machen?
2. Welche konkreten Header, Fehlerantworten und Redirect-/Proxy-Regeln bilden die
   HTTP-Grenze? Diese Bytes müssen vor dem Adapter feststehen.
3. Welche Aufbewahrungs- und Bereinigungsregel für Nonces bleibt auch bei
   Uhrfehlern und Schlüsselrotation fail closed?
