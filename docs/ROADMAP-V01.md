# Roadmap zu v0.1

Dieses Dokument ist ein **Plan, kein Zustandsbericht.** Was tatsächlich gilt, steht in
`main`, in den Tests und in der CI. Wenn dieses Dokument und das Repository sich
widersprechen, hat das Repository recht.

Maßgeblich bleiben `SECURITY.md`, `docs/CONSTITUTION-V1-DRAFT.md` und als Import-Gate
`docs/MIGRATION-MATRIX.md`. Diese Roadmap ordnet Arbeit, sie erzeugt keine eigene
Autorität und darf keiner dieser Quellen widersprechen.

## Ziel von v0.1

> Ein Fremder klont das Repository, führt **einen** Befehl aus, und sieht: ein Job geht
> über HTTP hinein, die Identität wird serverseitig bestimmt, eine vom Orchestrator
> unabhängige Instanz prüft die Policy, der Job läuft isoliert, das Ergebnis kommt
> **signiert** zurück und wird von einer weiteren unabhängigen Instanz angenommen, und die
> Audit-Chain lässt sich gegen einen extern festgehaltenen Kopf verifizieren. Die CI
> beweist denselben Pfad bei jedem Push.

Erst wenn das gilt, ist v0.1 erreicht. Nicht vorher.

v0.1 ist ausdrücklich **keine** Produktionsfreigabe und kein Sicherheitsnachweis für einen
echten Betrieb. Es ist der erste durchgehende, nachvollziehbare Pfad durch alle Schichten.

## Bewusst nicht in v0.1

Kanonisches Import-Gate ist `docs/MIGRATION-MATRIX.md`, gebunden an den exakten
Quell-SHA. Die folgenden Ausschlüsse sind Scope-Entscheidungen dieser Roadmap; ihre
ursprüngliche Einstufung als `REJECT_FOR_BOOTSTRAP` stammt aus dem inzwischen
historischen `docs/IMPORT-MANIFEST.md` und wird hier bewusst fortgeschrieben:

- **Firecracker / microVM** — erhöht Komplexität und Angriffsfläche, ohne die v0.1 nicht
  nachweisbar wäre. Isolation beginnt auf Prozessebene.
- **Datenbank / Persistenz** — der Kern bleibt prozesslokal. Persistenz ist eine eigene,
  bewusst zu entwerfende Phase.
- **Portal / Web-UI** — externe Angriffsfläche, kommt nach den Kernverträgen.
- **Semantisches Gedächtnis** — nicht Teil des minimalen Trust-Kerns.

Etwas hiervon vorzuziehen ist kein Fortschritt, sondern eine Verbreiterung des Scopes. Wer
es tut, nimmt die Entscheidung bewusst zurück und begründet sie.

## Die Schritte

### Teil 1 — landen, was in Arbeit ist

Offene Arbeit, die nicht integriert wird, verfällt. Das ist die Hauptursache der beiden
vorangegangenen Neuaufbauten. Deshalb steht dieser Teil vorn.

1. **#9** — Validator lehnt tief verschachtelte Eingaben ab, statt abzustürzen.
2. **#5** — Contract-CI auf GitHub Actions. Wird nach #9 ohne weitere Änderung grün.
3. **#8** — Migrationsmatrix wird kanonisches Import-Gate; `IMPORT-MANIFEST.md` wird als
   historisch markiert.
4. **#6** — serverseitige Einmal-Approval-Tokens, scope-gebunden, mit Herkunftsprüfung
   beim Grant.
5. **#3** — schließen. Eine Datenbank-Grundlage widerspricht dem Bootstrap-Scope oben.
   Kommt als eigene Phase zurück, wenn sie gebraucht wird.

### Teil 2 — die vertikale Scheibe

Ein Pfad durch alle Schichten, nicht alle Schichten halb fertig. Die Reihenfolge folgt der
Abhängigkeit: erst die Verträge, dann die Instanzen, die sie durchsetzen.

6. **Schema-Kennungen auf URN umstellen** — `schemas/handoff-v1.schema.json` trägt eine
   `$id` unter einer Domain, die nicht gehalten wird und damit von Dritten registrierbar
   ist. Die Entscheidung steht in der Migrationsmatrix: `urn:geniusnew:schema:<name>:v1`.
   Das geschieht **vor** Schritt 7, damit v0.1 nicht auf einer fremdregistrierbaren
   Vertragskennung aufbaut.

7. **Audit-Ereignisvertrag** — was ein Eintrag enthalten darf: Akteur, Aktion,
   Entscheidung, Integritätsbezug, Zeitbezug. **Keine Roh-Payloads, keine Secrets**
   (`CONSTITUTION-V1-DRAFT.md` §7, `SECURITY.md`). Nutzdaten erscheinen ausschließlich als
   Hash oder Referenz. Ein Test muss belegen, dass Payload-Inhalt keinen Eintrag erreichen
   kann.

8. **Audit-Chain** — append-only, hash-verkettet, mit `verify()`. Eine für sich stehende
   Kette erkennt Änderung und Einfügung, **nicht** aber das Abschneiden des Endes: eine um
   die letzten Einträge gekürzte Kette bleibt in sich gültig. Deshalb gehört ein extern
   festgehaltener, signierter Kettenkopf dazu. Tests gegen Änderung, Einfügung **und
   Löschung des letzten Eintrags**.

   **Status bis zum eigenen Anker-Prozess** (der aktuelle Stand folgt unten).
   Mechanismus, Autoritätstrennung und Tests stehen
   (`geniusnew/audit_chain.py`): Position, signierter Kopf mit eigenem Audit-Schlüssel,
   und ein Anker, gegen den eine gekürzte und neu signierte Kette scheitert. Der Anker
   bindet sich an eine *Kette*, nicht an eine Länge: ein Vorrücken muss belegen, dass der
   Eintrag an der bereits festgehaltenen Position weiterhin auf den festgehaltenen Hash
   führt. Ein bloß monotoner Zähler war hier nachweislich zu wenig — er akzeptiert jede
   längere Kette, auch eine ohne gemeinsame Geschichte.
   Offen war die *Externalität* des Ankers. Er lag im selben Prozess wie die Kette, und
   damit im Vertrauensbereich dessen, der schreibt — das modelliert die Grenze, es ist sie
   nicht. Wo der Anker tatsächlich liegt, ist eine Deployment-Entscheidung, und
   sie hängt an Schritten 12 bis 14: erst wenn Gateway und Ergebnisprüfung als getrennte
   Instanzen existieren, gibt es überhaupt einen Ort außerhalb des Schreibers. Der Schritt
   gilt als erledigt, wenn der Anker dort liegt.

   Zweite offene Abhängigkeit: die Kopfsignatur ist HMAC und damit symmetrisch — wer
   prüfen kann, kann auch signieren. Eine Trennung in privaten Signatur- und öffentlichen
   Prüfschlüssel braucht ein Primitiv außerhalb der Standardbibliothek und ist deshalb
   eine Abhängigkeitsentscheidung, keine Codeänderung.

   **Status: Anker in eigenem Prozess** (`geniusnew/anchor_process.py`). Die
   Verdrahtung legt den Kopf standardmäßig bei einem Kindprozess fest. Der Schreiber
   hält nur zwei Pipes und kann über sie genau zwei Dinge fragen: „lege diesen Kopf über
   diese Records fest“ und „was ist festgelegt“. Eine Nachricht zum Zurücksetzen gibt es
   nicht. Der Kindprozess führt den unveränderten `AuditAnchor` aus und baut die Records
   mit einer eigenen `AuditAuthority` neu auf; neue Kettenlogik kommt nicht hinzu. Die
   Demo belegt die Grenze mit einem dreizehnten Angriff: Der Schreiber setzt den Anker in
   seinem eigenen Speicher zurück und reicht die gekürzte, neu signierte Kette ein.
   Gegen den bisherigen In-Prozess-Anker gelingt das, gegen den Prozess nicht; beide
   Richtungen hält ein Test fest.

   Offen war danach der **Lebenszyklus**: Der Dienst startete den Anker und konnte ihn
   damit auch beenden, und ein Neustart des Dienstes war ein Zurücksetzen.

   **Status: Lebenszyklus beim eigenen Pfad.** Start und Stopp sind aus dem Dienst
   herausgezogen: `anchor_process.start` startet den Anker in einer eigenen Session und
   gibt ein `AnchorHandle` zurück, das als Einziges ihn beenden kann. Der Dienst bekommt
   in `wiring.build` nur einen `AnchorClient` mit dem Socket-Pfad — ohne Voreinstellung,
   denn eine Voreinstellung wäre wieder der Dienst, der seinen Anker selbst startet; und
   `Service` hat keine Methode mehr, die etwas beendet. Statt zweier Pipes hält der
   Schreiber jetzt einen Unix-Socket-Pfad; fragen kann er darüber weiterhin genau die
   zwei Dinge von oben. Den öffentlichen Prüfschlüssel bekommt der Anker von dem, der
   ihn startet, nicht vom Schreiber. Ein Neustart des Dienstes setzt den
   Anker nicht mehr zurück; die Demo belegt das mit einem vierzehnten Angriff, und
   `tests/test_end_to_end.py` hält es fest.

   Offen bleiben zwei Dinge, bewusst getrennt: Der Anker läuft unter demselben
   Betriebssystem-Nutzer (Deployment), und er hält nur Speicher — ein Neustart des
   *Ankers* ist weiterhin ein Zurücksetzen. Ob und wie er persistiert wird, ist eine
   eigene Entscheidung in `docs/ADR-002-anchor-persistence.md`, Status offen. Ein Test
   hält die Grenze offen.

   **Entscheidung zu HMAC:** blieb für v0.1. Nach v0.1 sind **Audit-Köpfe Ed25519**:
   `AuditAuthority` signiert, `AuditVerifier` hält nur den öffentlichen Schlüssel, und der
   Anker-Prozess bekommt nur diesen. Damit ist die Schlüsselfrage dieses Schritts für die
   Kette gelöst; der Anker kann einen gefälschten Kopf ablehnen, aber keinen erzeugen.
   Handoff- und Ergebnissignaturen folgen in eigenen Schritten. Erste Abhängigkeit:
   `cryptography`, in `requirements.txt` exakt gepinnt und mit Hashes.

9. **Ergebnisvertrag** — das Ergebnis wird vom Worker signiert und bei der Annahme
   geprüft, symmetrisch zum eingehenden Handoff. Ohne diesen Schritt bleibt „das Ergebnis
   kommt signiert zurück" im Ziel oben unerfüllt.

   **Status: Vertrag steht** (`geniusnew/results.py`). `produce` verhält sich zu `issue`
   wie `accept` zu `validate`; beide Wire-Formate gehen durch denselben Decoder. Der
   Worker signiert mit einem eigenen Schlüssel — nicht dem Handoff-Integritätsschlüssel,
   denn der prägt Autorisierungen, und ein Worker damit könnte sich selbst beauftragen.
   Die Signatur ist an den Digest des exakten Handoff-Artefakts gebunden, sonst ließe
   sich ein Ergebnis von Job A als Antwort auf Job B ausgeben. Ein `FAILED`-Ergebnis
   trägt keinerlei Output, damit „Fehler" kein Kanal wird, der an den Output-Prüfungen
   vorbeiführt.

   Offen bleibt, was kein Vertrag lösen kann: die Annahme ist **nicht einmalig**. Ein
   Vertrag hält keinen Zustand; dasselbe Ergebnis zweimal anzunehmen verhindert erst die
   annehmende Instanz aus Schritt 14, so wie `approvals.py` es für Approvals tut. Ein
   Test hält diese Grenze offen fest. Ebenso bleibt HMAC symmetrisch: die von §8
   geforderte Unabhängigkeit ist hier organisatorisch, nicht kryptografisch.

10. **Worker-Schnittstelle** plus ein deterministischer Trivial-Worker als Referenz.

    **Status: Schnittstelle steht** (`geniusnew/workers.py`). Die interessante Hälfte ist
    die Grenze, nicht der Worker: `WorkerRunner` hält die Signaturautorität, prüft den
    Grant, ruft die Arbeitsfunktion, validiert was zurückkommt und signiert. Ein `Worker`
    sieht weder Schlüssel noch Handoff noch Identität — nur eine Kopie der Payload. Was
    er erreichen kann, erreicht auch ein Angreifer, der ihn übernimmt.

    Default-Deny gilt auch hier: ein Worker, dessen Tool nicht im Grant steht, läuft
    nicht, und die Ablehnung wird als signiertes `FAILED` festgehalten statt verschwiegen.
    Das Gateway aus Schritt 12 sollte vorher greifen — eine Grenze, die sich darauf
    verlässt, ist keine.

    Ein Fehlschlag ist ein Ergebnis, kein Absturz: wirft die Arbeitsfunktion oder liefert
    sie die falsche Form, entsteht ein signiertes `FAILED`. Der Ausnahmetext erreicht das
    Ergebnis **nie** — `reason_code` ist ein geschlossener Code, damit der Fehlerpfad kein
    Textkanal aus der Ausführungsdomäne wird. `KeyboardInterrupt` und `SystemExit` werden
    dagegen durchgereicht: ein Shutdown ist kein Werkzeugfehler.

    Ein abgelaufener Handoff hat keine signierbare Antwort — `produce` verweigert die
    Signatur nach `expires_at`, also gibt es auch kein `FAILED` als Rückfallebene. Der
    Runner lehnt vorher ab, **bevor** die Arbeit läuft. Das ist der dritte der vier
    TTL-Kontrollpunkte aus Schritt 15.

    `geniusnew/workers.py` gehört außerdem zum Refusal-Mutation-Guard der CI; jede
    sicherheitsrelevante Ablehnung muss durch einen Test bemerkt werden, wenn sie entfällt.

11. **Isolationsgrenze auf Prozessebene** — kein Netz, kein Schreibzugriff außerhalb eines
    temporären Verzeichnisses, Zeitlimit, Ressourcenlimit. Nachweisbar durch Tests, die
    den Ausbruch versuchen.

    **Status: v0.1-Prozessgrenze steht** (`geniusnew/isolation.py`). Nur
    `Worker.run(payload)` läuft nach `exec` in einem **frischen Interpreter**;
    Signaturautorität und Ergebnisannahme bleiben im Elternprozess und werden nicht durch
    einen Fork in den Worker-Adressraum kopiert. Das Kind startet in einem pro Job erzeugten temporären
    Verzeichnis, erhält CPU-, Adressraum-, Dateigrößen- und FD-Limits sowie ein extern
    durchgesetztes Wall-Clock-Limit. Python-Audit-Hooks verweigern Socket-Erzeugung,
    Prozess-Spawn, native `ctypes`-Ladevorgänge und Dateischreibzugriffe außerhalb des
    Sandbox-Verzeichnisses. Tests versuchen Netzwerkzugriff, Schreiben nach außen,
    Prozess-Spawn und einen Wall-Clock-Ausbruch; jeder Versuch muss fail closed enden.

    Diese Grenze ist bewusst **keine microVM und kein Nachweis gegen bereits geladenen
    nativen Code oder rohe Syscalls**. Das wäre eine stärkere Isolationsebene und bleibt
    gemäß v0.1-Scope außerhalb dieses Schritts. Die hier behaupteten Eigenschaften gelten
    für den Python-Worker-Pfad und sind in `tests/test_isolation.py` festgehalten.

12. **Gateway** — unabhängige Default-Deny-Durchsetzung **vor** dem Dispatch. Eigene
    Instanz, nicht Teil des Orchestrators.

    **Status: Gateway-Grenze steht** (`geniusnew/gateway.py`). Das Gateway akzeptiert
    ausschließlich den rohen Handoff-Wire und führt die Contract-/Policy-Prüfung selbst
    erneut aus; ein bereits validiertes `Handoff`-Objekt ist kein Ersatz. Bei
    approval-pflichtigen Grants muss das Gateway den einmaligen Approval-Token gegen den
    exakt aus diesem Wire abgeleiteten Scope konsumieren. Erst danach mintet es einen
    `DispatchPermit`.

    `WorkerRunner.execute` akzeptiert nur noch diesen Gateway-Permit, keinen rohen oder
    bereits validierten Handoff. Damit ist Default-Deny vor Dispatch technisch
    verpflichtend. Der Permit bindet den Digest des zugelassenen Handoffs, wird vor
    Ausführung erneut geprüft und **atomar einmalig verbraucht**; Mutation nach Gateway-
    Zulassung und Replay über denselben oder einen anderen Runner scheitern. Der Approval-
    Token selbst verlässt den Gateway-Pfad nicht, im Permit steht nur der Receipt-Hash.

    Handoff-HMAC bleibt symmetrisch: ein Gateway mit dem Integritätsschlüssel könnte
    technisch auch signieren. Die Unabhängigkeit ist in v0.1 deshalb eine getrennte
    Runtime-Rolle/Instanz mit eigener API-Grenze, nicht eine asymmetrische
    Verifikationsautorität. Eine solche Schlüsseltrennung wäre eine spätere
    Kryptographie-/Deployment-Entscheidung.

13. **Orchestrator** — Admission, Zuordnung, Dispatch. Deterministisch, fail closed, keine
    geteilte veränderliche Autorität. Er trifft Entscheidungen, er bestätigt sie nicht
    selbst.

    **Status: steht** (`geniusnew/orchestrator.py`). Der Schritt wurde zweimal unabhängig
    gebaut; dieser Stand führt beide zusammen. Das Routing-Modell stammt aus
    `feat/orchestrator`, der Zustand, die Entscheidungssätze und die Permit-Bindung aus
    `feat/orchestrator-decisions`.

    Er entscheidet dreierlei und hält jede Entscheidung fest: ob ein Auftrag zulässig ist,
    welchen konfigurierten Worker die vertrauenswürdige Policy dafür benennt, und dass ein
    Dispatch stattgefunden hat. Das signierte Ergebnis gibt er **unbewertet** zurück — er
    hält keinen Ergebnisschlüssel und ruft `accept` nie auf, kann seinen eigenen Job also
    nicht für gelungen erklären. Das ist Schritt 14, und §8 verlangt, dass es eine andere
    Rolle bleibt.

    Umgekehrt kann er seinen Handoff nicht selbst zulassen: einen `DispatchPermit` mintet
    nur `gateway.py`, und `WorkerRunner.execute` nimmt nichts anderes an. Prüfbar ist für
    ihn nur, dass der zurückgegebene Permit **genau den eingereichten Wire** bindet — sonst
    liefe der Worker einen Vertrag, den dieser Orchestrator nie abgeschickt hat, während
    der zurückgegebene Wire weiter den anderen beschriebe.

    Die Zuordnung ist eine Tatsache des Grants: der Grant benennt die `worker_agent_id`,
    also ist Routing ein Nachschlagen auf diese Kennung und nie eine Wahl zwischen
    Kandidaten. Das Tool des konfigurierten Endpunkts muss trotzdem im Grant stehen — ein
    Worker unter der richtigen Agent-Kennung, der ein anderes Tool implementiert, erreichte
    sonst eine Fähigkeit, die die Policy nie erteilt hat. Beides wird **vor** dem Gateway
    geprüft, damit ein fehlgeleiteter Auftrag kein einmaliges Approval verbrennt.

    Er signiert mit einem **eigenen** Integritätsschlüssel statt in das Gateway zu greifen.
    Handoff v1 ist HMAC, die Bytes sind heute dieselben; den Schlüssel als eigene Eingabe
    zu nehmen ist das, was getrennte Schlüssel überhaupt möglich hält und Rotation nicht zu
    einer gemeinsamen Entscheidung zweier Rollen macht.

    Deterministisch: keine Uhr, keine Entropie — `now` ist ein Argument. Die Registry wird
    bei der Konstruktion in eine unveränderliche Abbildung kopiert, ein Aufrufer kann
    danach weder Worker hinzufügen noch das Routing ändern noch einen anderen Runner in den
    Dispatch-Pfad schmuggeln. **Kein Failover**, denn ein zweiter Versuch nach einer
    Ablehnung ist genau der Rückfall, der aus Default-Deny ein Default-Retry macht.

    Der einzige Zustand ist das Job-Ledger. Eine `job_id` wird verbraucht, wenn ein Permit
    vorliegt und die Arbeit gleich läuft — nicht vorher: ein vom Gateway abgelehnter
    Auftrag verliert seine Kennung sonst endgültig, und gerade beim fehlenden
    Approval-Token ist das Approval an einen Wire gebunden, der genau diese Kennung trägt.
    Was gelaufen ist, bleibt verbraucht, auch wenn der Dispatch scheitert; sie wieder
    freizugeben machte das Ledger zum Replay-Fenster statt zum Nachweis. Es ist beschränkt;
    eine unbegrenzte Menge, die ein Aufrufer wachsen lassen kann, ist ein Speicher-DoS mit
    Beleg.

    Aus dem Review dazugekommen: `admit` gibt die Ausstellungsentscheidung mit zurück,
    sonst hätte der zwingend zweistufige Approval-Pfad ein signiertes Artefakt ohne
    protokollierbare Entscheidung. Die Verfügbarkeit der `job_id` wird **vor** dem Gateway
    erfragt, damit eine Wiederholung kein einmaliges Approval verbrennt, das dann keine
    Arbeit bezahlt; die atomare Reservierung danach bleibt die eigentliche Autorität.
    Scheitert die Ausführung, trägt die Ablehnung die Dispatch-Entscheidung mit sich
    (`DispatchAttempted`) — die Kennung ist verbraucht und der Permit konsumiert, der
    Versuch hat also stattgefunden. Zeitstempel sind auf das Fenster begrenzt, das
    `audit.py` annimmt, und zwar dort, wo `now` hereinkommt: eine Ablehnung, die wegen
    ihrer eigenen Uhr nicht aufzeichenbar wäre, ist keine auditierbare Ablehnung. Und die
    bereinigte Ablehnung wird **außerhalb** des `except`-Blocks erhoben: `from None` setzt
    nur `__suppress_context__`, der Originaltext bleibt ein Attribut entfernt liegen — eine
    Nachricht zu säubern ist nicht dasselbe wie zu säubern.

    Ablehnungen tragen geschlossene Reason-Codes mit je einem festen Satz, damit der
    Ablehnungspfad kein Textkanal wird; jede verwendete Aktion liegt im geschlossenen
    Audit-Vokabular von `audit.py` (ein Test prüft das gegen dessen Menge). Fremde
    Ablehnungen — die des Gateways, die der Worker-Grenze — werden **unverändert**
    durchgereicht: die Entscheidung einer anderen Instanz als eigene zu protokollieren wäre
    derselbe Fehler in die andere Richtung. `geniusnew/orchestrator.py` gehört zum
    Refusal-Mutation-Guard der CI, der dafür `_deny` gelernt hat; ohne das wären alle
    Admission-Ablehnungen für ihn unsichtbar gewesen, während das Modul in der Liste stand.

    **Offen bleibt die Herkunft der Policy.** Das Gateway prüft den Wire unabhängig,
    bekommt die Policy aber als Parameter — in dieser Verdrahtung vom Orchestrator.
    Unabhängigkeit verlangt, dass beide Rollen dieselbe Policy aus derselben
    vertrauenswürdigen serverseitigen Quelle beziehen, nicht die eine von der anderen. Das
    ist eine Verdrahtungs- und Deployment-Frage (Schritte 16 und 17), kein Vertragsdefekt,
    und bleibt bis dahin ausdrücklich offen.

    **Entscheidung für v0.1: logische Trennung reicht.** Seit Schritt 17 kommt die Policy
    für beide Rollen aus der Kompositionswurzel, nicht die eine von der anderen. Gateway
    und Orchestrator bleiben aber Objekte in einem Prozess. Für die Zielbeschreibung von
    v0.1 gilt „eine vom Orchestrator unabhängige Instanz prüft die Policy“ damit als
    erfüllt im Sinne von §8: getrennte Rollen mit eigener API-Grenze, eigener
    Revalidierung des Wires und eigenem Audit-Akteur. Eine Prozesstrennung wie beim
    Worker und beim Audit-Anker kommt nach v0.1.

    **Offen bleibt die Reichweite des Ledgers.** Es ist eine Menge in einem Prozess. Ein
    Neustart oder eine zweite Instanz mit derselben Kennung führt denselben unverfallenen
    Handoff erneut aus; das Gateway hält kein eigenes Handoff-Ledger und mintet jedes Mal
    einen frischen Permit. Dauerhafter gemeinsamer Zustand ist eine Persistenzentscheidung,
    die unter *Bewusst nicht in v0.1* ausdrücklich draußen steht — der Anspruch lautet
    deshalb „ein Dispatch pro Job-Kennung **pro Instanz**", und ein Test hält genau diese
    Grenze offen fest, so wie `test_results.py` es für die Einmaligkeit der Annahme tut.

    **Offen bleibt außerdem die Prozessgrenze.** In einem Prozess gibt es keine
    Speichergrenze; der Orchestrator hält einen Endpunkt in die Ausführung hinein.
    Nachweisbar ist, was an der API gilt: er hält keine fremde Autorität in seinem eigenen
    Zustand, kann kein Permit minten und kein Ergebnis annehmen. Die Trennung nach §8 ist
    in v0.1 eine logische Rollentrennung, keine Speichertrennung.

14. **Ergebnisprüfung** — unabhängige Annahme: Signatur, Integrität, TTL. Weder Worker
    noch Orchestrator validieren ihr eigenes Ergebnis.

    **Status: steht** (`geniusnew/verifier.py`). Die Instanz nimmt **Wires entgegen, keine
    Objekte**: ein übergebenes `Handoff` ist die Schlussfolgerung, die jemand anders über
    diese Bytes gezogen hat, also parst und revalidiert sie den Handoff-Wire selbst gegen
    vertrauenswürdige Policy und serverseitige Identität — dieselbe Grenze wie beim
    Gateway, nur am anderen Ende des Pfads. Was sie danach prüft, ist `results.accept`,
    aufgerufen mit einem Handoff, den sie selbst abgeleitet hat.

    Neu ist, was kein Vertrag leisten kann: **Einmaligkeit**. `results.py` hält diese Lücke
    ausdrücklich offen — ein Vertrag hält keinen Zustand, also nimmt `accept` dasselbe
    Ergebnis so oft an, wie es gefragt wird (ein Test belegt genau das). Hier hat ein
    zugelassener Handoff genau ein angenommenes Ergebnis, so wie `approvals.py` es für
    Tokens tut.

    Das Ledger verbraucht die Kennung **erst nach vollständiger Prüfung**. Beim Eintritt zu
    verbrauchen hieße: wer diese Instanz erreicht, verbrennt mit einem gefälschten Ergebnis
    die eine Annahme des Jobs und sperrt das echte dauerhaft aus — ein Denial of Service,
    gebaut aus der Anti-Replay-Regel. Ein Test führt das vor.

    Ein signiertes `FAILED` ist ein echtes Ergebnis und wird als solches angenommen.
    Annahme betrifft das Artefakt, nicht den Ausgang; Fehlschläge als ungültig abzulehnen
    hieße, ein Worker könnte seine eigenen Fehler unsichtbar machen.

    Der vierte TTL-Kontrollpunkt aus Schritt 15 greift hier eine Schicht früher als
    erwartet: ein abgelaufener Handoff scheitert schon an der Revalidierung, bevor das
    Ergebnis überhaupt geparst wird. Die entsprechende Prüfung in `accept` bleibt als
    Verteidigung für Aufrufer, die ein `Handoff` anders in die Hand bekommen.

    Die Trennlinie ist bewusst scharf: alles, was den Job oder seine Artefakte betrifft,
    ist ein `Rejected` mit geschlossenem Code und damit als Audit-Eintrag festhaltbar;
    alles, was den **Aufruf** betrifft — eine Uhr, die kein Integer ist, eine Policy, die
    keine ist —, bleibt ein einfacher `ContractError`. Wer seine eigenen Argumente falsch
    setzt, fällt kein Urteil über ein Ergebnis und darf auch nicht so protokolliert werden.

    **Offen bleibt die Reichweite des Ledgers** — dieselbe Grenze wie beim Job-Ledger des
    Orchestrators. Es ist eine Menge in einem Prozess; ein Neustart oder eine zweite
    Instanz mit derselben Kennung nimmt dasselbe Ergebnis erneut an. Der Anspruch lautet
    deshalb „eine Annahme pro Handoff **pro Instanz**", und ein Test hält das fest.

    **Offen bleibt die Symmetrie.** Das Ergebnis-HMAC ist symmetrisch — wer prüfen kann,
    kann signieren. Die Unabhängigkeit ist hier eine getrennte Instanz mit eigener
    API-Grenze, keine kryptografische; ein Test hält diese Grenze offen fest, statt sie
    wegzubehaupten. Eine asymmetrische Ergebnissignatur ist die Abhängigkeitsentscheidung
    aus Schritt 8 und änderte nur den Konstruktor dieser Datei.

    **Offen bleibt die Approval-Evidenz.** Ein approval-pflichtiger Wire trägt dauerhaft
    `PENDING_APPROVAL` — das Konsumieren schreibt ihn nicht um —, also kann diese Instanz
    einen genehmigten Job nicht von einem ungenehmigten unterscheiden. Sie **verlangt**
    deshalb den Receipt-Hash des Gateways und protokolliert ihn, ohne ihn verifizieren zu
    können: der Store, der das könnte, gehört dem Gateway. Ohne Receipt wird abgelehnt —
    das lässt die Evidenz mitreisen. Das Verifizieren zu nennen wäre, UNKNOWN als PASS zu
    lesen. Die Auflösung gehört zu Schritt 17.

    `geniusnew/verifier.py` gehört zum Refusal-Mutation-Guard; `scripts/refusals.py` kennt
    dafür jetzt auch den modul-eigenen Ablehnungstyp `Rejected`. Ohne das wäre ausgerechnet
    die Einmaligkeitsregel für die Prüfung unsichtbar gewesen, während das Modul in der
    Liste stand.

    Die Schritte 12 bis 14 sind bewusst getrennt. `CONSTITUTION-V1-DRAFT.md` §8 verlangt,
    dass Orchestrierung, Policy-/Gateway-Prüfung, Ausführung, Ergebnisprüfung und
    Audit/Forensik logisch getrennt bleiben und keine Instanz ihre eigene
    sicherheitsrelevante Entscheidung allein bestätigt. Eine Zusammenlegung wäre eine
    Verletzung dieser Regel, keine Vereinfachung.

15. **TTL an allen vier Kontrollpunkten** — Admission, vor dem Dispatch, Revalidierung im
    Worker, Annahme des Ergebnisses. Ein Handoff, der bei der Admission gültig war und
    während der Ausführung abläuft, muss abgelehnt werden. Ein Ressourcen-Zeitlimit aus
    Schritt 11 ersetzt das nicht. Eigener End-to-End-Test für Ablauf **während** der
    Ausführung.

    **Status: steht** (`tests/test_ttl.py`, plus eine Ergänzung in `geniusnew/workers.py`).
    Die vier Punkte sind: Admission (das Gateway revalidiert den Wire), vor dem Dispatch
    (die Worker-Grenze lehnt ab, **bevor** die Arbeitsfunktion läuft), Revalidierung im
    Worker (nach der Arbeit, gegen die tatsächlich verstrichene Zeit) und Annahme. Jeder
    Test prüft die **Meldung**, nicht nur dass irgendetwas abgelehnt wurde — mehrere dieser
    Eingaben würden von einem späteren Punkt ohnehin abgelehnt, „ein ContractError kam"
    überlebte also das Löschen des früheren.

    **Der dritte Punkt fehlte.** `now` ist die Dispatch-Uhr und rückt nie vor, also war
    eine Ausführung, die die Deadline überschritt, von einer prompten nicht zu
    unterscheiden. Gemessen, bevor etwas geändert wurde: ein Worker, der 1,5 Sekunden
    schläft, bekam unter einer TTL von **einer** Sekunde sein Ergebnis signiert *und*
    angenommen. Jetzt wird die verstrichene Zeit über die Arbeitsfunktion gemessen und in
    echten Sekunden gegen die Deadline gehalten — ohne Rundung, denn Aufrunden lehnt
    gültige kurze Jobs ab und Abrunden lässt einen Job überziehen.

    Das signierte Artefakt bleibt davon unberührt: `produced_at` ist weiterhin die
    Dispatch-Uhr, derselbe Job erzeugt dieselben Bytes. Nur die Entscheidung, überhaupt zu
    signieren, hängt an der Dauer. Ein Test hält das fest, weil `scripts/demo.sh` genau
    diese Digests ausgibt, damit ein Leser zwei Läufe vergleichen kann.

    Dass ein Ressourcen-Zeitlimit das nicht ersetzt, ist keine Meinung, sondern Arithmetik:
    ein Wall-Limit darf bis 30 Sekunden gehen, eine Handoff-TTL bei einer Sekunde liegen.
    Die beiden beantworten verschiedene Fragen — was ein Worker verbrauchen darf, und wie
    lange die Erlaubnis dazu gilt.

    **Kein Test hier schläft.** `scripts/refusals.py` führt die gesamte Suite einmal pro
    Ablehnung aus, derzeit 175-mal; eine Sekunde Schlaf kostet drei Minuten CI. Die
    Zeitquelle des Runners ist deshalb eine überschreibbare Naht (`_monotonic`), und die
    Tests lassen einen Worker eine Stunde dauern, ohne eine Stunde zu dauern. In der
    Produktion ist es `time.monotonic`; gefunden wurde die Lücke mit einem echten Worker.

16. **HTTP-Eingang** — API-Key wird serverseitig auf einen Principal abgebildet.
    Identität, Tier und Rechte kommen **nie** aus dem Request.

    **Status: steht** (`geniusnew/http_entry.py`). Die Regel ist als Verbot formuliert,
    und so ist sie auch umgesetzt: der Request-Body ist eine **geschlossene Form** mit
    genau einem Feld, der Payload. Ein mitgeschicktes `tier`, `subject`, `user_id`,
    `tools` oder `job_id` wird **abgelehnt, nicht stillschweigend verworfen** — ein Feld,
    das hier durchkäme, wäre in jeder späteren Schicht vertrauenswürdig, denn die prüfen
    gegen den Policy-Grant, den diese Entscheidung ausgewählt hat. Stilles Verwerfen sagt
    dem Angreifer nichts und dem ehrlichen Aufrufer auch nichts.

    Ein `Principal` trägt **nur** das Subject. Tier, Tools und User-ID stehen im Grant;
    sie hier mitzuführen schüfe eine zweite Wahrheitsquelle über Autorisierung, und das
    Erste, was einer zweiten Wahrheitsquelle passiert, ist Widerspruch.

    Schlüssel liegen nie im Klartext: die Registry hält SHA-256-Digests und löst per
    Dictionary-Lookup auf. Kein Vergleichs-Loop, dessen Dauer verrät, wie viel von einem
    Schlüssel stimmte; nach der Konstruktion kein Klartext im Speicher; ein Dump des
    Objekts offenbart kein Credential. Ein unbekannter und ein fehlender Schlüssel ergeben
    **dieselbe** Antwort — ein Test sammelt fünf Varianten ein und verlangt genau eine
    Antwort, damit kein Orakel entsteht.

    Die Job-Kennung wird **hier** erzeugt. Wer sie selbst wählen darf, wählt, mit welcher
    Kennung er kollidiert: der Orchestrator verbraucht jede genau einmal, ein Aufrufer
    könnte also fremde Jobs verdrängen oder einen Namen wiederholen.

    Die Socket-Hälfte ist bewusst die dünne: alles, was entscheidet, ist ohne Server
    testbar. Sie verrät außerdem die Interpreter-Version nicht (der Default-Header nennt
    Python samt Nummer) und protokolliert die Request-Zeile nicht — angreifergewählter
    Text in einem Log, das ein Operator liest, gehört nicht dorthin; Protokollierung
    gehört in die Audit-Chain, wo geschlossen ist, was vorkommen darf.

    `scripts/refusals.py` kennt jetzt auch **zurückgegebene** Ablehnungen: eine Grenze,
    die einem Fremden antwortet, kann ihn nicht anschreien. Ohne das wären Pfad, Methode
    und unbekannter Schlüssel für die Prüfung unsichtbar gewesen. Sie fand daraufhin
    sechs ungetestete Ablehnungen und eine **unerreichbare** — ein Kollisionscheck über
    Schlüssel-Digests, den eine Mapping-Eingabe nie auslösen kann; er ist entfernt statt
    nachträglich mit einem Test geschmückt.

    **Nicht enthalten:** Rate-Limiting, TLS, Sessions, jede Authentifizierung über den
    Schlüssel hinaus. Das sind Deployment-Fragen, und sie hier zu behaupten wäre genau
    die Art Aussage, die `SECURITY.md` verhindern soll.

17. **Verdrahtung** plus ein End-to-End-Test, der den gesamten Pfad geht, die
    Ergebnissignatur prüft und die Audit-Chain gegen den festgehaltenen Kopf verifiziert.

    **Status: steht** (`geniusnew/wiring.py`, `tests/test_end_to_end.py`). Ein Request geht
    über einen echten Socket hinein, der Eingang macht aus dem API-Key ein Subject, der
    Orchestrator lässt zu und routet, das Gateway prüft den Wire unabhängig nach und mintet
    den einzigen Permit, den die Worker-Grenze annimmt, der Worker läuft dahinter, die
    Ergebnisprüfung nimmt an, ohne beauftragt zu haben, und die Audit-Rolle hält fest, was
    jede Instanz entschieden hat. Danach verifiziert die Kette gegen einen Kopf, den der
    Anker hält.

    **Die Verdrahtung hat sofort einen echten Defekt gefunden.** `Dispatch` ließ den
    Approval-Receipt des Gateways fallen, und die Ergebnisprüfung verlangt ihn für einen
    approval-pflichtigen Job — die beiden Komponenten waren also **gar nicht
    zusammensteckbar**. Beide hatten vollständige Testsuiten, beide hatten für ihre eigene
    Hälfte recht. Nichts außer dem Zusammenstecken hätte das gesagt. Genau dafür ist dieser
    Schritt da.

    Was eine Kompositionswurzel den Teilen schuldet, steht im Modul-Docstring und ist hier
    dreierlei: Schlüssel werden **einmal** abgeleitet und nach Rolle vergeben, niemand
    greift in eine andere Komponente; die **Policy kommt von hier**, nicht von einer
    Komponente — damit ist die offene Stelle aus Schritt 13 wenigstens in der Herkunft
    behoben, wenn auch noch nicht in der Prozessgrenze; und die **Uhr ist echt**. Alle
    Module nehmen `now` als Argument und lesen keine Uhr, was sie testbar macht — irgendwer
    muss aber wirklich auf eine sehen, und die TTL-Kontrollpunkte sind nur so ehrlich wie
    dieser eine Aufruf.

    **Approval über HTTP** (Entscheidung: gehört in v0.1). Ein Approval ist an genau einen
    signierten Wire gebunden, also braucht es zwei Anfragen. `POST /jobs` stellt den
    Handoff aus, zeichnet `HANDOFF_ISSUED` auf und antwortet `PENDING_APPROVAL`; der Job
    wartet in einem begrenzten, prozesslokalen Speicher. `Service.approve(job_id)` erteilt
    serverseitig den einmaligen Token und zeichnet `APPROVAL_GRANTED` auf — bewusst ohne
    HTTP-Route, denn wer freigeben darf, ist eine Entscheidung über Personen, und v0.1 kennt
    keinen Principal-Typ dafür. Der Client legt den Token in `X-Approval-Token` an
    `POST /jobs/<job_id>/approve` vor, mit einem Body von genau `{}`. Danach läuft der Job
    durch denselben Ausführungspfad wie jeder andere; das Gateway verbraucht den Token
    atomar.

    Ein falscher Token oder der eines anderen Jobs wird vom Gateway abgelehnt, bevor
    etwas läuft. Die Ablehnung steht in der Kette, der Job wartet weiter auf den richtigen
    Token, und der fremde Token ist nicht verbraucht. Ein unbekannter Job, der Job eines
    anderen Subjects, ein falscher und ein verbrauchter Token ergeben von außen dieselbe
    Antwort `409 REJECTED`. Tests gehen jeden dieser Fälle über den echten Socket.

    Offen bleibt die **Zustellung des Tokens**: Wie er vom Freigebenden zum Client kommt,
    ist Sache des Deployments. Der Speicher wartender Jobs ist prozesslokal wie alle
    Ledger in v0.1.

### Teil 3 — nachweisbar machen

Ein Ergebnis, das nur der Autor reproduzieren kann, ist kein Ergebnis.

18. **`scripts/demo.sh`** — ein Befehl. Startet einen Job, gibt die Audit-Chain aus,
    verifiziert sie gegen den festgehaltenen Kopf und sagt deutlich, ob die Prüfung
    bestanden wurde. Die Ausgabe enthält nur, was Schritt 7 erlaubt.

    **Status: steht** (`scripts/demo.sh`, `scripts/demo.py`). Ein Job geht durch alle
    heute vorhandenen Schichten, die Kette wird gegen den Anker verifiziert, und das
    Skript endet mit `PASS` oder `FAIL` und einem entsprechenden Exit-Code — eine Demo,
    die nicht scheitern kann, beweist nichts.

    Die zweite Hälfte ist der eigentliche Punkt: zwölf Manipulationsversuche (heute
    vierzehn, siehe unten), die alle
    abgelehnt werden müssen. Sieben davon waren einmal ein echtes Loch — aus Review, aus
    eigenem Probing, und eines aus dem Zusammenstecken zweier fertiger Komponenten.

    Die Auflage „nur, was Schritt 7 erlaubt" wird als Test geführt, nicht als Vorsatz:
    `tests/test_demo.py` setzt Kanarienvögel in Root-Secret und Payload und schlägt fehl,
    wenn einer davon in der Ausgabe auftaucht. Gedruckt werden Digests, keine Inhalte und
    kein Schlüsselmaterial — auch kein gekürztes Präfix, denn acht Byte eines Schlüssels
    sind acht Byte.

    **Seit Schritt 17 zeigt sie den echten Pfad.** Der Job geht **über HTTP** hinein, die
    Identität wird aus dem Schlüssel serverseitig bestimmt, Gateway und Ergebnisprüfung
    sind eigene Instanzen, und die Kette nennt drei verschiedene Komponenten als Akteure
    (`orchestrator`, `gateway`, `monitor`).
    Damit steht die Zielbeschreibung von v0.1 nicht mehr als Absicht da, sondern als
    Ausgabe eines Befehls — bis auf die Prozessgrenze zwischen den Instanzen, die eine
    Deployment-Frage bleibt.

    Aus fünf Angriffen sind vierzehn geworden: vier davon gehen über den Socket, weil der
    Eingang das Einzige ist, was ein Fremder erreicht — ein nicht registrierter Schlüssel,
    ein `tier` im Body, eine selbstgewählte Job-Kennung, eine Tür, die es nicht gibt. Die
    übrigen zehn halten die Objekte, die ein Insider hätte; die letzten beiden sind der
    Schreiber selbst, der den Anker zurückzusetzen versucht — einmal aus dem eigenen
    Speicher, einmal durch einen Neustart des Dienstes (Schritt 8).

19. **CI führt den End-to-End-Test mit aus**, nicht nur die Unit-Tests.

    **Status: steht** (`.github/workflows/verify.yml`). Der Workflow ruft
    `unittest discover -s tests` auf und nimmt damit `tests/test_end_to_end.py` (Socket bis
    zur gegen den Anker verifizierten Kette) und `tests/test_demo.py` mit, das
    `scripts/demo.sh` als Unterprozess startet und Exit-Code 0 plus `PASS` verlangt. Keiner
    dieser Tests hat ein `skip`; fällt der Pfad, wird die CI rot. Danach läuft der
    Refusal-Mutation-Guard über dieselbe Suite, also muss auch jede Ablehnung auf dem
    End-to-End-Pfad von einem Test bemerkt werden.

    Grenze: die Isolation verlangt POSIX-Ressourcenlimits, der Anker einen Unix-Socket.
    Deshalb läuft die CI auf drei Systemen, jedes mit der Aussage, die dort gilt:
    `ubuntu-latest` führt Suite, Refusal-Guard und den Quickstart aus einer frischen
    Kopie mit leerer Umgebung aus, und die letzte Zeile muss wörtlich in der README
    stehen; `windows-latest` verlangt, dass die Demo mit Exit-Code 1 und einer
    `FAIL`-Zeile endet, die auf WSL verweist (fail closed). Unter Windows läuft die Demo
    in WSL; die README beschreibt den Weg.

    macOS war „nicht getestet“. Der erste Lauf auf `macos-latest` hat es beantwortet:
    jeder isolierte Worker endete dort mit `ISOLATION_VIOLATED`, auch der
    Referenz-Worker, weil macOS das Adressraum-Limit ablehnt. Die Isolation verweigert
    macOS seitdem beim Aufbau statt einmal pro Job. Der macOS-Job hält fest, was dort
    gilt: die Ursache (das Limit wird abgelehnt — wird es eines Tages angenommen, wird
    der Job rot), dass der Anker läuft, und dass die Demo mit `FAIL` endet.

    Die Isolation behauptet auch ein Speicherlimit. Bisher belegte ein Test nur, dass der
    Wert im Kind ankommt; `test_memory_past_the_limit_is_refused_not_granted` verlangt
    jetzt, dass er greift. Die erste Fassung dieses Tests bestand auch ohne Speicherlimit —
    sie schrieb jede Seite und lief ins CPU-Limit. Wieder eine Zusicherung, die ein anderer
    Pfad erfüllte als der geprüfte; gefunden, indem das Limit testweise entfernt wurde.

20. **README-Quickstart**, den ein Fremder ohne Rückfragen befolgen kann. Am besten von
    jemandem gegengelesen, der das Projekt nicht kennt.

    **Status: geschrieben, nicht gegengelesen.** Der Quickstart steht in `README.md`. Der
    zweite Halbsatz dieses Schritts ist nicht erfüllt, solange niemand ohne
    Projektkenntnis ihn befolgt hat; bis dahin gilt der Schritt als offen.

    Was sich davon prüfen lässt, prüft die CI (Schritt 19). Für den Rest steht das
    Protokoll in `docs/QUICKSTART-REVIEW.md`: wer, in welcher Umgebung, was
    aufgeschrieben wird und wann der Schritt als erledigt gilt. **Offen ist die Person** —
    sie muss benannt werden, und das kann kein Commit.

21. **Tag `v0.1`** auf einem grünen, verifizierten Commit.

## Arbeitsregeln

Diese drei Regeln adressieren die beobachteten Ursachen der vorangegangenen Neuaufbauten.
Sie sind keine Stilfragen.

**Klein schneiden, oft mergen.** Ein Pull Request, der älter als etwa zwei Tage wird, ist
eine künftige Abschreibung. Lässt sich etwas heute nicht mergen, ist es zu groß
geschnitten. Das Altprojekt trägt zehn offene Pull Requests auf veralteten Basen — gute
Arbeit, die nicht mehr landen kann.

**Behauptungen gehören in Checks, nicht in Sätze.** Das gilt auch für Tests selbst.
Dreimal in drei Tagen bestand hier ein Test aus einem anderen Grund als dem behaupteten:
eine Zusicherung, die ein anderer Pfad erfüllte als der geprüfte. Jedes Mal von Hand
gefunden, im Nachhinein. `scripts/refusals.py` macht daraus eine Prüfung: jede Ablehnung
im Code wird einzeln abgeschaltet, und die Suite *muss* rot werden. Beim ersten Lauf
überlebten 24 von 46 Ablehnungen auf `main` — darunter der Payload-Hash-Abgleich, die
Grant-Allow-List und der Approval-Scope-Abgleich. Keine davon war redundant; sie waren
ungetestet. Die CI führt das bei jedem Push aus. Dokumente veralten lautlos, Checks
nicht. Drei Aussagen in der Projektdokumentation waren nachweislich falsch, bis sie
geprüft wurden: eine über den Paketnamen, eine über die Sichtbarkeit des Repositories und
eine über das gültige Import-Gate. Keine davon war böswillig; alle drei sind entstanden,
weil niemand sie nachrechnen musste. Die erste Fassung dieser Roadmap war die vierte: sie
wurde geschrieben, ohne `CONSTITUTION-V1-DRAFT.md` zu lesen, und widersprach ihr an vier
Stellen. Ein unabhängiger Review hat das gefunden.

**Nicht umbenennen.** Jede Identitätsänderung bisher hat einen vollständigen
Migrationszyklus gekostet und strukturell nichts verbessert. Das Projekt heißt GeniusNew.

## Danach

Was nach v0.1 kommt, ist in `docs/MIGRATION-MATRIX.md` unter `REBUILD` klassifiziert und
an einen exakten Quell-SHA gebunden. Diese Roadmap greift dem nicht vor.

Ein Werkzeug liegt bereit, das erst nach v0.1 laufen soll: `scripts/autoresearch.py` mit
den Anweisungen in `docs/AUTORESEARCH.md`. Ein Agent ändert genau eine Datei, das Skript
misst die Laufzeit der Suite und behält eine Änderung nur, wenn Tests, Refusal-Guard und
Demo mindestens so stark bleiben. Es berührt keinen Produktcode.
