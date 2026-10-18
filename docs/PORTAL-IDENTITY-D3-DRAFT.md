# D3: Personenbindung zwischen Portal und Kern (Entwurf)

**Status: HOLD.** Dieser Vertrag aktiviert weder eine Portalroute noch eine
Core-Route. Er konkretisiert die D3-Personenbindung aus
[`PORTAL-CORE-D1-DRAFT.md`](PORTAL-CORE-D1-DRAFT.md). Vor Code brauchen die
neue signierte Nachricht, der persistente Registerentwurf und die Betriebsgrenzen
einen unabhängigen Security-Review. Die übrigen D1-HOLD-Gates bleiben bestehen.

## Entscheidung und Ziel

Kaan hat am 18.10.2026 in dieser Sitzung zwei Vorgaben für v0.2 bestätigt:

1. **Administrierte Bindung:** Eingeladene Portal-Konten und direkte API-Keys
   werden nach manueller Prüfung derselben natürlichen Person einer von Core
   vergebenen kanonischen Personen-ID zugeordnet. Es gibt keine öffentliche
   Registrierung und keine Selbstverknüpfung von Aliasen.
2. **Neue Envelope-Version:** Die geprüfte Portal-Nutzerreferenz wird in einer
   neuen, vollständig signierten Envelope-Version transportiert. Eine zweite
   Signatur oder ein unsignierter Zusatzheader ist dafür nicht vorgesehen.

Der Kern bleibt Autorität für `subject`, `user_id`, Policy-Grant, Approver-Rolle,
Job-Eigentum und Quoten. Die Portal-Signatur beweist den Portal-Dienst und dessen
Aussage über eine aus der geprüften Session ermittelte Nutzerreferenz. Der
Browser kann diese Referenz nicht setzen oder ändern. Die Referenz allein
authentisiert niemanden.

## Aufnahme und Lebenszyklus

- Das Portal stellt Konten nur auf Einladung durch Kaan aus. Jedes Konto erhält
  einmalig eine zufällige, unveränderliche Referenz aus 16 Bytes, dargestellt
  als 32 lowercase Hex-Zeichen (`portal_user_ref`). Ein Klartextname oder eine
  E-Mail-Adresse gehört nicht in die signierte Nachricht.
- Kaan prüft die Person außerhalb des Portals, bevor ein Core-Administrator
  `(portal_service_principal_id, portal_user_ref)` einer bestehenden oder
  neuen kanonischen Personen-ID zuordnet. Der Core erzeugt diese ID; Portal
  und Browser wählen sie nicht. Ein unbekannter, mehrfacher, gesperrter oder
  widersprüchlicher Eintrag ist REFUSE.
- Direkte API-Keys und Approver-Subjects derselben geprüften Person tragen
  dieselbe Core-Personen-ID. Ein noch nicht verifizierter Alias darf keine
  Freigabe auslösen. Die C3-Selbstfreigabe vergleicht die Personen-ID des
  Auftraggebers mit der des Freigebenden, auch über verschiedene Konten,
  Sessions und Schlüssel hinweg.
- Sperren und Aufheben einer Bindung wirken bei jeder neuen Core-Annahme.
  Bereits signierte, noch gültige Requests werden erneut gegen den aktuellen
  Registerzustand geprüft und nach Sperrung abgelehnt. Eine einmal verwendete
  `(portal_service_principal_id, portal_user_ref)` wird niemals einer anderen
  Person zugewiesen. Nach Sperrung bleiben Konto und Referenz dauerhaft
  gesperrt. Für eine Wiederaufnahme wird nach erneutem Identitätsnachweis
  ein **neues** Portal-Konto mit neuer Referenz eingeladen und durch einen
  auditierten Administratorvorgang derselben Core-Personen-ID zugeordnet.
  Das alte Konto und dessen Bindung bleiben als gesperrte Datensätze erhalten.
  So kann ein vor der Sperrung signierter Request nicht nach einer schnellen
  Umbindung unter einer anderen Personen-ID angenommen werden.
- Portal-Sessions werden bei Kontosperrung beendet. Die Core-Sperre ist davon
  unabhängig nötig, weil ein bereits signierter Request sonst noch eintreffen
  könnte. Fällt Portal-DB oder Core-Register aus, wird die Aktion abgelehnt.

## Signierte Nachricht v2

Die neue Envelope-Version heißt `geniusnew-portal-core-v2`. Sie enthält genau
die elf Felder des D1-Envelope und zusätzlich `portal_user_ref`. D1 und v2 sind
verschiedene Formate; der D1-Codec aus PR #147 bleibt auf D1 beschränkt und
akzeptiert v2 nicht. Die neue Version übernimmt die D1-Regeln für kanonisches
ASCII-JSON, Body, Feldtypen, Größen, Nonce, Zeitfenster und konfigurierte
Bezeichner. `portal_user_ref` ist exakt 32 lowercase Hex-Zeichen. Die Werte
`version`, `kind`, `method` und `path` sind geschlossen; für v2 gelten
`geniusnew-portal-core-v2`, `REQUEST`, `POST` und `/jobs`.

Die signierten Bytes sind exakt
`b"geniusnew/portal-core/request/ed25519/v2\x00" + envelope_bytes`.
Die strenge Ed25519-Prüfung aus dem noch offenen D1-Nachreview (PR #132) gilt
auch hier und muss vor Code in den gemeinsamen Vertrag übernommen werden.
Eine v2-Signatur darf
niemals als D1-Signatur gelten und umgekehrt. Die Replay-Domäne ist
`geniusnew.portal-core.request.v2`; sie bleibt über Schlüsselwechsel stabil.
Ein Nonce wird für diese Domäne und denselben verifizierten
`portal_service_principal_id` genau einmal verbraucht.
Die Speichergestalt und ihre Rollback-Sicherung gehören in den von ChatGPT in
Kaans Auftrag geführten Entwurf [`DATABASE.md`](DATABASE.md), nicht in diesen
Vertrag.

Der neue Header heißt `X-GeniusNew-Portal-V2-Envelope` und trägt wie D1
`base64url-ohne-Padding(envelope_bytes) + "." + signature-hex`. D1-Header,
v2-Header und `Authorization` dürfen nicht kombiniert werden. Der erste
HTTP-Hop und jeder weitere Hop verweigern doppelte oder normalisierte
Authentisierungsheader **vor** einer Dictionary-Bildung. Die D1-Regeln zu
Framing, unverändertem Body, Authority, TLS, Proxy und Last gelten auch für v2;
ein Proxy darf v2 nicht als D1 oder direkte API-Key-Route weiterleiten.

## Annahmereihenfolge

1. Das Portal prüft D2-Session und CSRF, leitet `portal_user_ref` ausschließlich
   aus dem eigenen Kontodatensatz ab, belastet das persistente Budget pro
   Referenz und das Dienstbudget und signiert erst danach. Unbekannter
   Budgetzustand ist REFUSE.
2. Der Core prüft am geschützten Ingress rohe Route, Header, Framing und Limits,
   dann kanonische Body- und Envelope-Bytes, Digest, Zeit und Signatur. Ein
   D1-Envelope oder ein Mischheader auf dem v2-Pfad ist REFUSE.
3. Der Core ordnet das erfolgreich verifizierte Tupel
   `(issuer, audience, key_id)` einem konfigurierten Portal-Dienst zu. Die
   Konfiguration ordnet jedem zugelassenen Tupel genau eine stabile
   `portal_service_principal_id` zu; mehrere Tupel dürfen auf denselben
   Dienst zeigen, etwa bei Schlüsselrotation. Erst dann liest der Core seine
   aktuelle eindeutige Bindung für
   `(portal_service_principal_id, portal_user_ref)` und löst sie zu
   Core-`subject` und kanonischer Personen-ID auf. Ohne aktive Bindung
   entsteht weder Job noch Freigabe. Der Service-Principal wird nie als
   Job-`user_id` eingesetzt.
4. Der Core prüft Policy, Rolle, Personen-ID, Job-Grenzen, Rate und Quote mit
   der aufgelösten Identität. Eine Freigabe durch dieselbe Person wird vor
   jeder Zustandsmutation abgelehnt. Erst danach darf die Replay-Reservation
   nach dem D1-Zeit-/Commit-Vertrag stattfinden; ein Worker läuft nie in
   dieser Transaktion.
5. Nach bestätigter Reservation und letzter Zeitprüfung erzeugt der Core die
   Job-ID und nutzt den bestehenden Handoff-, Approval-, Audit- und
   Ergebnisweg. Unbekannter COMMIT-Ausgang, Ablauf oder Crash erzeugt keinen
   stillen Retry. Ein verbrauchter Nonce bleibt verbraucht.

Die Zuordnung muss bis zur Job- und Audit-Mutation konsistent bleiben. Eine
gleichzeitige Sperrung darf keinen Job unter einer nach der Sperrung ungültigen
Bindung annehmen. Das Transaktions- und Sperrprotokoll dafür ist vor Runtime-Code
im DB-Entwurf festzulegen und gegen parallele Sperrung zu testen.

Diese v2-Nachricht deckt ausschließlich `POST /jobs` ab. Verlauf, Freigaben und
API-Schlüssel brauchen eigene versionierte, signierte Operationen, die dieselbe
Core-Personenbindung und die jeweiligen Rollen prüfen. Die Job-Signatur darf
nicht durch einen ausgetauschten Pfad für eine dieser Aktionen gelten. Ohne
diese Operationen und ihre Ende-zu-Ende-Tests ist das Portal nicht vollständig
funktionsfähig.

## Abnahme vor einer aktiven Route

- Positiver Ende-zu-Ende-Test mit zwei eingeladenen Personen: Auftrag,
  eigener Verlauf und Freigabe durch eine andere berechtigte Person.
- Negative Tests für unbekannte, doppelte, gesperrte und umgebundene
  Referenzen; Browser-Injektion von `portal_user_ref`, `subject`, `user_id`,
  `tier`, `tools` oder `job_id`; Alias derselben Person als Approver;
  fremder Verlauf und fremder Job ergeben 404.
- Vektor- und Refusal-Tests für v2, strikte Signatur, Header-Mischung,
  Replay, Zeit-Rücksprung, parallele Bindungsänderung und Neustart/Restore.
  Jede neue Ablehnung muss vom Refusal-Guard erkannt werden.
- Separat freizugebender Datenbankentwurf für Core-Register, Audit-Bindung,
  Replay und Restore. DB-Code braucht `Claude DB Review: APPROVED` am exakten
  Head; `contracts` und unabhängiger Security-Review müssen grün sein.
- Nachweis am tatsächlichen ersten Proxy-Hop, dass v2 ausschließlich über den
  geschützten Portal-Ingress erreichbar ist, einschließlich der D1-Last-Gates.
  Kein öffentlicher Demo-Endpunkt und keine produktive Aktivierung ohne Kaans
  ausdrückliches Deployment-OK.

**Offen:** Die konkrete administrative Oberfläche und der überprüfbare
Identitätsnachweis für Einladung und Wiederaufnahme; der
DB-Transaktionsvertrag gegen gleichzeitige Sperrung; der unabhängige
Rollback-Nachweis für Zeit und Replay; Audit-/Recovery-Ereignisse bei
Replay-Ablehnung und Crash; genaue Lastbudgets und Proxy-Abnahme. Diese Punkte
sind Voraussetzungen für Code und Betrieb, keine stillen Defaults.
