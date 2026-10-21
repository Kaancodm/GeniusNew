# Entscheidungszentrale – Implementierungsstand

Stand 20.10.2026. Isolierter vorbereiteter Quellstand, Basis main
`0e6e0dbf5aefa4169c47ae73f8b39ff0cd94f235`. Kein Deployment.

## Nutzbarer Beobachter

Das Control Deck erhält den Einstieg `/decisions` und die rein lesende Route
`/api/decisions`. Ein getrennter CLI-Kollektor liest offene GitHub-PRs mit dem
bestehenden gh-Zugang. Keine Tokens, Modellaufträge oder GitHub-Schreibzugriffe im
HTTP-Prozess. Die bisherige Host-/Origin-Prüfung und Aktions-Allowlist bleiben.

Die Ansicht sammelt Merge-Prüfkandidaten, Konflikte, fehlgeschlagene Checks,
Entwürfe und Frage-/Gate-Auszüge aus PR-Beschreibungen. Quell-PR und vollständiger
Head stehen dabei. Frageauszüge sind ausdrücklich kein bestätigter Offen-Status;
spätere Antworten in Kommentaren und private Chats sind nicht angebunden.

`contracts=SUCCESS` allein gibt keine Merge-Berechtigung. Ein technischer Kandidat
braucht zusätzlich keinen Draft, MERGEABLE, CLEAN und keine laufenden/fehlgeschlagenen
Checks oder formalen Änderungsanforderungen. Das fachliche Review-Gate bleibt
UNKNOWN. Veraltete (>5 Minuten), beschädigte, fehlende oder unvollständige
Snapshots ergeben keine positive Merge-Empfehlung. Der Browser prüft das Alter
auch nach dem Laden erneut.

„Empfehlung übernehmen“ zeigt den konkreten Schritt und öffnet dessen GitHub-Ziel.
Die eigentliche schreibende Aktion wird in der authentisierten GitHub-Oberfläche
bestätigt. Kein Merge per anonymem Deck-POST und keine globale Sammelfreigabe.

Belegte GitHub-Branch-Konflikte erscheinen als Warnhinweis, eigener Filter und
Button „Lösungsprompt kopieren“. Der Prompt nennt PR, Branch, vollen Snapshot-Head,
Quelle, Prüfauftrag und Sicherheitsgrenzen. Er fordert eine frische Prüfung,
Erhalt beider Änderungsseiten und neue Review-Evidenz; kein pauschales ours/theirs,
Force-Push oder Merge nach main. Das Kopieren sendet keinen Auftrag an andere KIs.
Widersprüche zwischen privaten Chat-Aussagen sind ohne deren strukturierte
Anbindung nicht automatisch erkennbar und werden nicht als erfasst dargestellt.

Der allgemeine Button „Konflikt lösen · Prompt erstellen“ erfasst zusätzlich
Widersprüche zwischen KI-Aussagen, Reviews, Regeln und sonstigen technischen
Fragen. Zwei Positionen sind Pflicht; Beteiligte, Quellen und Ziel sind ergänzbar.
Der erzeugte Prompt trennt Behauptungen von Belegen, verlangt unabhängige Prüfung
und berücksichtigt den angezeigten Kontext-Hash. Er erteilt weder Merge- noch
Deployment- oder Anbieterbefugnisse. Ein eigener JavaScript-Verhaltenstest prüft
Inhalt, Pflichtfelder, Grenzen und unbekannte Quellen.

## Collector (nur vorbereitete Arbeitskopie)

```sh
python3 -B -m tools.control_deck.decisions --output /PRIVATE_STAGING/decisions.json
GENIUSNEW_DECISIONS_SNAPSHOT=/PRIVATE_STAGING/decisions.json python3 -m tools.control_deck --host 127.0.0.1 --port 8788
```

`/PRIVATE_STAGING` ist ein Platzhalter. Den Snapshot niemals committen oder in ein
öffentliches Paket legen. Ein Seiten-Reload liest nur den letzten Snapshot;
regelmäßige Sammlung ist noch nicht installiert. Eine defekte Sammlung ersetzt
den vorherigen Snapshot nicht; dessen Zeitstempel läuft sichtbar ab.

## Noch offene schreibende Anschlüsse

Die Buttons „Review umleiten“ und „Notfallreview mit Fable“ sind sichtbar, aber
die tatsächliche Weiterleitung bleibt deaktiviert. Das heutige Deck hat keine
eigene Benutzeranmeldung. Eine Browser-Anfrage oder private Netzadresse beweist
keine Identität und darf keine kostenpflichtige oder privilegierte Aktion auslösen.

## Gemeinsames Befehlsjournal

`/api/command-journal` liest einen gemeinsamen serverseitigen Metadatenverlauf aus
`GENIUSNEW_COMMAND_JOURNAL` (Standard:
`~/.local/state/genius-control-deck/commands.json`). iPad und Laptop sehen dadurch
nach Aktivierung dieselbe Quelle, kein gerätespezifisches localStorage.

Die Ansicht zeigt kopierbare feste Testbefehle mit Ausführungsort und einen Verlauf:
ausstehend/fehlgeschlagen rot ✕, bestätigter Testlauf grün ✓. Kopieren führt nichts
aus und schreibt keine Freigabe. Ein grüner historischer Lauf bleibt auf seinen
Zeitpunkt, seine Umgebung und seinen Quellen-Hash begrenzt. Geänderte Quelldateien
übernehmen keinen aktuellen grünen Status.

Ein separater CLI-Runner führt ausschließlich zwei feste Testsuiten aus und schreibt
Exitcode, Testanzahl, Zeitpunkt, Umgebung und SHA-256 des Outputs atomar in das
Journal. Keine freien Shell-Befehle, keine Modellaufrufe, keine Testausgaben/Secrets
in der HTTP-Antwort. Maximal 100 Ereignisse / 128 KiB; kein stilles Löschen bei voller
Historie. Ein Lock verhindert konkurrierende Schreiber. Ein fehlender/beschädigter
Verlauf wird nicht automatisch auf null zurückgesetzt.

Das Journal ist Ausführungsmetadaten unter einer vertrauenswürdigen OS-Identität,
keine manipulationssichere Audit-Kette und kein zweiter Aufgaben-/Freigabespeicher.
Freie Link-/Befehlseingabe und schreibende Chat-Aktionen sind noch nicht implementiert.

Der Kopierbereich bietet Server-/Termius-Befehle sowie PowerShell-Aufrufe zum selben
Server. Das private Profil `GENIUSNEW_COMMAND_TARGETS` enthält ausschließlich den
geprüften SSH-DNS-Namen und Benutzernamen, keine Schlüssel. Fehlt es oder ist es
ungültig, gibt es keinen kopierbaren PowerShell-Platzhalter. Eine erfolgreiche
Serverprüfung bestätigt den PowerShell-/SSH-Startweg nicht; dieser bleibt rot
ausstehend, bis dafür eigene Ausführungsevidenz vorliegt. Kopieren führt nichts aus.
Erst der kopierte Runner, im Checkout gestartet, schreibt einen Eintrag. Jeder
Eintrag trägt das Ziel (`server`, `termius`, `powershell`), den Commit-HEAD, den
Quellstand-Hash und einen Ergebnisbeleg (`Ran N tests`-Zeile plus Ausgabe-Ende,
mit Hash). Ein Ziel zeigt nur seinen eigenen letzten Lauf. Ändern sich Quellstand
oder HEAD, erscheint der Befund als `Veraltet`, nicht als grün. Ein Ergebnis, dessen
Beleg nicht zur Testanzahl passt, macht das ganze Journal `unavailable`.
Grenze: Das Journal ist eine Datei unter derselben OS-Identität. Der Beleg erkennt
Inkonsistenz, aber keine bewusste Fälschung durch diese Identität.

Alle Snapshot-PR-Links und der aktuelle Regel-/Toolstand sind im selben Bereich
kopierbar. Der allgemeine Konfliktprompt lässt sich dort erstellen und kopieren.
Ein dauerhaftes geräteübergreifendes Protokoll auch dieser Kopieraktionen und frei
eingegebener Befehle/Links braucht noch den authentisierten Schreibanschluss.
Die Oberfläche weist darauf ausdrücklich hin; Browserkopien gelten nie als Erfolg.

```sh
python3 -B -m tools.control_deck.command_journal --run decision-tests --repo /CHECKED_CHECKOUT --output /PRIVATE_STAGING/commands.json --target server
```

Die Pfade sind Platzhalter. `GENIUSNEW_COMMAND_JOURNAL` und der Runner müssen auf
dasselbe private Journal zeigen. `--target` ist die vom Operator angegebene
Zielumgebung; der Lauf prüft sie nicht. Snapshot-Vorschauen synchronisieren sich nicht.

### Begrenzte Archivierung des Befehlsjournals

Der Runner verweigert weiterhin das 101. Ereignis eines aktiven Segments und jeden
Schreibversuch über 128 KiB. Der Bediener kann das aktive Segment ausdrücklich
archivieren, auch vor Erreichen dieser Grenzen:

```sh
python3 -B -m tools.control_deck.command_journal --archive --output /PRIVATE_STAGING/commands.json
```

Diese Aktion führt keinen Katalogbefehl aus. Sie benutzt denselben exklusiven Lock
wie der Runner und erstellt `commands.json.archive-1.json` bis höchstens
`commands.json.archive-8.json`. Jedes Archiv enthält unveränderte Ereignisfelder in
Schema 1. Bestehende Archive werden weder überschrieben noch gelöscht. Die aktive
Datei wechselt atomar auf Schema 2: geordnete SHA-256-Digests der Archive und ein
leeres aktives Ereignisfeld. Schema-1-Journale bleiben lesbar. Leser prüfen alle
referenzierten Archive einschließlich Dateityp, Größe, Hash, Ereignisbelegen und
segmentübergreifender Zeitreihenfolge; auch archivierte Ziel-/Aktions-/HEAD-/
Quellstandbindungen bestimmen weiterhin den letzten Befund je Ziel.

Maximal bestehen neun Evidenzdateien mit je 128 KiB und 100 Ereignissen, also
1.125 MiB und 900 Ereignisse, plus Lock und kurzlebige Schreibdatei. Volle Archive
ergeben STOP für weitere Archivierung; die aktive Datei kann bis zu ihrem eigenen
Limit weiterlaufen. Eine externe Aufbewahrung oder ein neuer Journal-Zyklus braucht
einen gesonderten Betreibervertrag. Es gibt keine automatische Löschung, keinen
Hintergrundjob und keinen unbeschränkten Archivzähler.

Schreibfolge: temporäre Archivdatei schreiben und `fsync`, ohne Überschreiben
verlinken, Verzeichnis synchronisieren, danach das neue aktive Manifest schreiben,
`fsync`, atomar ersetzen und Verzeichnis erneut synchronisieren. Bei Fehler vor
der Manifest-Veröffentlichung bleibt das aktive Journal erhalten. Ein bereits
veröffentlichtes nächstes Archiv sperrt weitere Befehlsläufe; `--archive` darf den
Schritt nur wiederholen, wenn seine Bytes exakt zum aktiven Segment passen.
Andernfalls STOP. Ein Fehler nach der atomaren Veröffentlichung wird ebenfalls
als unbestätigt gemeldet; Leser prüfen den tatsächlich vorhandenen Zustand.
Fehlende Archive oder ein fehlendes aktives Journal neben Archiven werden nie als
neue leere Historie behandelt.

Regressionen decken 100/101 Ereignisse, Byte-Limit, acht volle Archivplätze,
unveränderte Evidenz, Ziel-/stale-/pending-Bindung, Manifest-/Hash-/Zeitfehler,
fehlende Dateien, Symlinks/FIFOs, Schreib-/Syncfehler, Wiederholung und konkurrierende
Locks ab. Dies ist keine Crash-Abnahme eines echten Hosts. Die bestehende Grenze
bleibt: dieselbe OS-Identität kann Dateien einschließlich Hashes bewusst fälschen
oder zurückrollen; das Journal ist keine authentisierte Ausführungsquittung.
Produktive Journaldateien und Dienste werden durch diesen Entwurf nicht umgestellt.

## Ein gemeinsamer Entscheidungsstand

`/api/operating-context` und `python3 -m tools.control_deck.operating_context` lesen
dieselbe private Datei (`GENIUSNEW_OPERATING_CONTEXT`, Standard:
`~/.local/state/genius-control-deck/operating-context.json`). Sie enthält Quellen,
Version, neue Regeln, Werkzeug-/Kontingentzutände und versionsgebundene Empfangs-
bestätigungen. Ein ausgedachtes AVAILABLE ist verboten: statische Dateien dürfen
nur unknown, exhausted oder not_connected melden. Exhausted wird nie automatisch
zum verfügbaren Reset umgedeutet. Eine Live-Erreichbarkeitsprüfung bleibt separat.

Ein Inhalts-Hash bindet Version, Zeitpunkt, Regeln und Toolzustände. Auch geänderter
Text unter derselben Versionsbezeichnung ändert den Hash. Der getestete Helfer
`require_current(context, assigned_digest, receipt_digest)` verweigert fehlenden
Kontext und abweichende Auftrags-/Empfangsstände. Das ist ein Vergleichsvertrag,
keine Authentisierung und noch keine aktive Sperre im bestehenden Auftragsbuch.

Der künftig authentisierte Koordinator muss vor jedem Start/jeder Übergabe und vor
einer schreibenden Aktion den aktuellen Hash lesen, dem Agenten bereitstellen und
dessen passende Empfangsbestätigung prüfen. Bei Änderung HOLD und neu einlesen.
Ein Lauf darf keinen späteren Entscheidungsstand als schon bekannt ausgeben.
Empfangsbestätigungen kommen ausschließlich von authentisierten Adaptern; Modelle
dürfen sie nicht selbst in diese Datei schreiben. Ein Hash belegt bereitgestellte
Bytes und keine Garantie des Modellverständnisses.

Bestehende Codex-/Claude-/Gemini-CLI-Starts und private Chat-Sitzungen sind noch
nicht an diesen Vertrag angeschlossen. Deshalb zeigt die Ansicht derzeit überall
NOT_CONFIRMED; keine bereits erfolgte Benachrichtigung wird behauptet. Claude muss
die entsprechende Session-/Governance-Anbindung im eigenen Bereich integrieren.
Das Dashboard führt weder ein zweites Aufgabenregister noch eine abweichende
Freigabequelle ein. Aktuelle Kaansweisungen stehen über älteren Projektdokumenten.

Vor Live-Aktivierung erforderlich:

1. Authentisierte Kaansitzung, CSRF-/Replay-Schutz und begrenzter Aktionsvertrag.
2. Derselbe volle Head-SHA, Prüfumfang, Pflichtprüfertyp und identische offene
   Befunde im Ersatzauftrag. Neuer Head invalidiert die alte Empfehlung.
3. Anbieterzustand getrennt als installiert / authentisiert / erreichbar /
   Kontingent geprüft anzeigen. Ohne verfügbaren geprüften Adapter BLOCKED.
4. Keine Selbstprüfung: Implementierer und unabhängiger Prüfer bleiben getrennt.
   Gemini Pro ist der allgemeine Reviewkanal nach Kaans Weisung vom 20.10.;
   besondere Claude-DB-/Security-Gates werden dadurch nicht aufgehoben.
5. Weiterleitungen über das bereits vorhandene private Auftragsbuch abwickeln,
   mit request_key, Owner/Versuch, Lease und HOLD bei unbekanntem Ergebnis.
   Keine zweite maßgebliche Aufgaben-/Freigabedatenbank im Dashboard.
6. Fable-Notfallreview nur nach ausdrücklicher Auswahl. Der Anbieter
   `claudeapikey.dev` ist ein Drittanbieter; die aktuelle API-Kompatibilität,
   offizielle Modell-ID, Datenschutz-/Kostenbedingungen und bestehende private
   Zugangskonfiguration wurden in diesem Auftrag nicht geprüft.
7. Vor Fable-Aufruf zeigt die Bestätigung Anbieter/Modell, Ziel-PR/SHA,
   freigegebenen redigierten Datenumfang sowie Kosten-/Tokenlimit. Kein Upload
   privater Fabrikdaten, Secrets, DSNs oder Logs als Nebeneffekt. Schlüssel bleiben
   serverseitig; Modell-/Anbieterwechsel niemals still.
8. Notfallreview ist zusätzliche Evidenz. Es darf Critical/High, besondere
   Pflichtprüfer, Kaans Merge-/Deploy-Freigabe oder das Kostenlimit nicht umgehen.

## Aktivierung und Rückweg

Vor Aktivierung: unabhängiges Review des Diffs, vorgeschriebene Projektprüfungen,
Live-Dateihashes neu vergleichen, vorhandenes mobiles Deck erhalten und explizites
Deployment-GO einholen. Die neue Ansicht ersetzt keine produktiven Dateien.
Erst nach freigegebenem Rollout Snapshot-Pfad und periodischen Collector einrichten.
Rückweg: vorherige geprüfte Deck-Dateien und unveränderte Dienstkonfiguration.
Die drei ablehnenden Module sind in `scripts/refusals.py` registriert. Vor Integration
braucht der endgültige Head die vollständige Projektprüfung einschließlich der
betroffenen Refusal-Guards sowie das unabhängige Gemini-Review.

## Grenzen dieses Stands

Keine Chat-Synchronisation, kein automatischer Merge, kein Reviewversand,
kein kostenpflichtiger Modellaufruf, kein produktiver Dienst- oder DB-Eingriff.
Die Ansicht ist implementiert; die Live-Aktionsadapter bleiben eigene offene Arbeit.
