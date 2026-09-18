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

   **Status: nicht abgeschlossen.** Mechanismus, Autoritätstrennung und Tests stehen
   (`geniusnew/audit_chain.py`): Position, signierter Kopf mit eigenem Audit-Schlüssel,
   und ein Anker, gegen den eine gekürzte und neu signierte Kette scheitert. Der Anker
   bindet sich an eine *Kette*, nicht an eine Länge: ein Vorrücken muss belegen, dass der
   Eintrag an der bereits festgehaltenen Position weiterhin auf den festgehaltenen Hash
   führt. Ein bloß monotoner Zähler war hier nachweislich zu wenig — er akzeptiert jede
   längere Kette, auch eine ohne gemeinsame Geschichte.
   Offen bleibt die *Externalität* des Ankers. Er liegt derzeit im selben Prozess wie die
   Kette, und damit im Vertrauensbereich dessen, der schreibt — das modelliert die Grenze,
   es ist sie nicht. Wo der Anker tatsächlich liegt, ist eine Deployment-Entscheidung, und
   sie hängt an Schritten 12 bis 14: erst wenn Gateway und Ergebnisprüfung als getrennte
   Instanzen existieren, gibt es überhaupt einen Ort außerhalb des Schreibers. Der Schritt
   gilt als erledigt, wenn der Anker dort liegt.

   Zweite offene Abhängigkeit: die Kopfsignatur ist HMAC und damit symmetrisch — wer
   prüfen kann, kann auch signieren. Eine Trennung in privaten Signatur- und öffentlichen
   Prüfschlüssel braucht ein Primitiv außerhalb der Standardbibliothek und ist deshalb
   eine Abhängigkeitsentscheidung, keine Codeänderung.

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

    **Offen bleibt außerdem die Prozessgrenze.** In einem Prozess gibt es keine
    Speichergrenze; der Orchestrator hält einen Endpunkt in die Ausführung hinein.
    Nachweisbar ist, was an der API gilt: er hält keine fremde Autorität in seinem eigenen
    Zustand, kann kein Permit minten und kein Ergebnis annehmen. Die Trennung nach §8 ist
    in v0.1 eine logische Rollentrennung, keine Speichertrennung.

14. **Ergebnisprüfung** — unabhängige Annahme: Signatur, Integrität, TTL. Weder Worker
    noch Orchestrator validieren ihr eigenes Ergebnis.

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

16. **HTTP-Eingang** — API-Key wird serverseitig auf einen Principal abgebildet.
    Identität, Tier und Rechte kommen **nie** aus dem Request.

17. **Verdrahtung** plus ein End-to-End-Test, der den gesamten Pfad geht, die
    Ergebnissignatur prüft und die Audit-Chain gegen den festgehaltenen Kopf verifiziert.

### Teil 3 — nachweisbar machen

Ein Ergebnis, das nur der Autor reproduzieren kann, ist kein Ergebnis.

18. **`scripts/demo.sh`** — ein Befehl. Startet einen Job, gibt die Audit-Chain aus,
    verifiziert sie gegen den festgehaltenen Kopf und sagt deutlich, ob die Prüfung
    bestanden wurde. Die Ausgabe enthält nur, was Schritt 7 erlaubt.
19. **CI führt den End-to-End-Test mit aus**, nicht nur die Unit-Tests.
20. **README-Quickstart**, den ein Fremder ohne Rückfragen befolgen kann. Am besten von
    jemandem gegengelesen, der das Projekt nicht kennt.
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
