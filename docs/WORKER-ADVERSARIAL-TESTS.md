# Angriffstests für Worker-Sandbox und Core-Antwortprüfung

## Geltungsbereich

Diese Tests ergänzen die begrenzte Zielhost-Probe aus #150. Sie verwenden ausschließlich synthetische Dateien, eigene Kindprozesse und eine lokale TCP-Gegenstelle. Keine installierten Quellen, Dienste, Konten, Datenbanken, Secrets oder Anker werden verändert.

Basis: `29488feac128c4774095da54a8ab9b4c276ace96`. Die produktiven Worker-, Sandbox- und Bootstrap-Module sind gegenüber Installationscommit `9b11f7a89fe948d65d6b2eb3337111a5b11d3a57` unverändert.

Im Kind läuft die echte Funktion `_run_service_connection`. Die produktive `_assert_worker_identity` bleibt vollständig aktiv: alle vier UID-/GID-Werte, Zusatzgruppen, NoNewPrivileges und fünf Capability-Sätze werden geprüft. Landlock, Seccomp, Audit-Hook, Ressourcenlimits, Nondumpability und Protokollprüfungen bleiben unverändert. Nur die systemd-gebundene Jobverzeichnis-Ermittlung und die Summarizer-Factory sind Test-Fixtures.

Für manipulierte Antworten läuft der echte Core `_CoreExchange` gegen eigene Testpeers mit Kernel-Credentials und echten PIDFDs. Die Test-Lifecycle-Bindung prüft den erzeugten Kind-PIDFD; sie ersetzt keinen systemd-Nachweis. Es werden keine Signaturen, produktiven Jobs oder Freigaben erzeugt.

**Dies sind Komponenten-Nachweise. Die vier systemd-/UID-Betriebsgates aus #150 und die vollständige Betriebsabnahme bleiben HOLD.**

## Verpflichtender eigener CI-Schritt

Die Suite liegt in `adversarial_tests/` und läuft zusätzlich zur normalen Testsuite verpflichtend im bestehenden CI-Job `tests`, vor Demo, Persistenz- und Restore-Prüfung. Ihr Fehlschlag schlägt den Job und damit `contracts` fehl; es gibt kein Überspringen, keine bedingte Ausführung und kein `continue-on-error`.

Dadurch verändert sie die normalen Root-/Container-Aufrufe von `unittest discover -s tests` nicht. Unterstützt wird ein Linux-Runner mit Landlock ABI mindestens 4, Seccomp und PIDFD-Kontrollnachrichten. Fehlt die benötigte Isolation, verweigert die Angriffssuite und liefert keinen Angriffs-PASS.

Vor Python senkt der eigene CI-Schritt mit `setpriv` seine Identität auf den ursprünglichen nicht privilegierten Runner-Benutzer ab, entfernt Zusatzgruppen und alle Capability-Sätze und setzt NoNewPrivileges. Die kurzzeitige Root-Ausführung endet vor dem Python-Start; Schutzregeln und Konten des Hosts werden nicht verändert.

Reproduktion als angemeldeter unprivilegierter Benutzer mit autorisiertem sudo-Zugang, im Checkout und mit aktivierter vorhandener Venv:

```sh
adversarial_python="$(command -v python)"
adversarial_uid="$(id -u)"
adversarial_gid="$(id -g)"
sudo -- /usr/bin/setpriv \
  --reuid="$adversarial_uid" --regid="$adversarial_gid" --clear-groups \
  --bounding-set=-all --inh-caps=-all --ambient-caps=-all \
  --no-new-privs \
  "$adversarial_python" -B -W error::ResourceWarning \
  -m unittest discover -s adversarial_tests -v
```

Keine neue Abhängigkeit und kein produktiver Einspeisepfad werden ergänzt. Die Hilfsdatei `adversarial_tests/worker_service_attack_child.py` gehört ausschließlich zu den Tests.

## Abgedeckte Fälle

| Risiko | Tatsächliche Prüfung |
| --- | --- |
| Dateien | Lesbare synthetische Canary: Python-Read ergibt ISOLATION_VIOLATED. Vorab geladener nativer open zum Lesen und Erzeugen außerhalb des Jobverzeichnisses ergibt EACCES; keine Datei bleibt zurück. |
| Netzwerk | Python-Socket-Erzeugung ergibt ISOLATION_VIOLATED. Natives TCP-connect zu einem nachweislich erreichbaren Loopback-Listener ergibt EACCES. Keine Behauptung einer kernel-seitigen UDP-/Unix-Socket-Sperre. |
| Nativer Prozessstart | Vorab geladenes natives fork wird nach READY und Request durch SIGSYS beendet. Ein beliebiger Fehler oder Timeout genügt nicht. Die ungeschützte Kontrolle erzeugt nur ein eigenes, sofort endendes und geerntetes Kind. |
| Manipulierte Antwort | Falsche Nonce, falscher Digest, Zusatzfeld, falscher Output-Typ, unbekannter Fehlercode, nichtkanonisches/doppeltes JSON, Übergröße, verkürzter Frame und Nachlaufbytes werden jeweils mit exakt passendem Ablehnungsgrund verweigert. Der Testpeer muss erfolgreich enden. |
| Fehler im Prüfwerkzeug | Ein absichtlich nach REQUEST/EOF mit Exit 71 abbrechender Peer darf weder als anderer Parserfehler noch als erwartete Truncation einen gültigen Nachweis liefern. |
| Kontrollen | Gültiger Peer wird angenommen und endet mit Exit 0; falsche Kernel-UID verweigert vor Lifecycle-Bindung. Native Datei-/TCP-/Fork-Payloads haben ungeschützte positive Kontrollen. |

## Review und bisherige Evidenz

Am ersten PR-Head `ec5424bc2bac3daf239827f38c9da6fdf5c56c2c` liefen zehn gezielte und 1.223 vollständige Tests grün. GitHub bestätigte am selben Head 1.223 Tests, Demo 15/15, Persistenz 3/3 und beide CI-Restore-Fälle. Das sind historische Nachweise dieses Heads.

CodeRabbit fand anschließend einen echten Nachweisfehler: Eine nach REQUEST/EOF mit Exit 71 abbrechende Fixture ließ die zehn generischen ContractError-Assertions grün werden. Direkt reproduziert: null Failures/Errors trotz fehlendem RESULT. Die verschärften Tests prüfen exakten Ablehnungstext und erfolgreichen Peer-Exit; dieselbe kaputte Fixture ergibt nun zehn Assertion-Failures ohne Testfehler. Ein dauerhafter Crash-Regressionstest kam hinzu.

Der Codex-Review beanstandete außerdem Root-Kompatibilität, fehlende Kernelvoraussetzungen, ausgelassene Privilegienprüfungen und den allein gesendeten Größenheader. Die dedizierte verpflichtende CI-Suite erhält normale Root-Läufe und prüft Isolationsvoraussetzungen ohne Tests zu überspringen. Die vollständige produktive Privilegienprüfung ist wieder aktiv. Die Übergrößenprüfung muss exakt den Längenfehler liefern; eine bloße Truncation zählt nicht mehr.

Vor der Verschärfung des Privilegienprofils liefen die elf gezielten und 1.224 vollständigen Tests erneut grün, Demo 15/15 und Persistenz 3/3 ebenfalls. Diese Ergebnisse werden nicht als PASS der finalen Capability-/CI-Änderung ausgegeben. Deren frische CI und Review sind gesondert nachzuweisen.

## Rootless-Labor: bisher nur Voraussetzung, kein Abnahmenachweis

Kaan bestätigte per Termius den Wechsel zu Namespace-UID/GID 20002 ohne Zusatzgruppen. Der Remote-Werkzeugprozess selbst kann den UID-Mapping-Helfer wegen NoNewPrivileges=1 nicht verwenden; diese Schutzbedingung bleibt bestehen.

Ein zusätzlicher Versuch in einem eigenen Rootless-Container verwendete ein nur lesbares Dateisystem, keine Netzwerkfreigabe und nach setpriv keine Capabilities. Die positive Antwortkontrolle verweigerte beim echten EOF-Parser. Direkte Diagnose: Der Kernel liefert EOF-Credentials `(0, 65534, 65534)` statt des erwarteten Null-Sentinels. Der Core lehnt diese unverändert ab. Der Rootless-Aufbau ist damit für diesen Transportstand kein erfolgreicher Labor-Nachweis. Die Parsergrenze wurde nicht geändert, kein Container blieb zurück. Der Versuch belegt keinen Fehler auf dem normalen Zielhost und kein vollständiges systemd-Labor.

## Noch erforderliches Betriebsgate

Ein geeigneter isolierter systemd-Testhost muss dieselben vier Angriffsgruppen mit real getrennten Core-/Worker-UIDs, geschützter Kopie des exakten Runtime-Stands und gebundenen Unit-/Invocation-/PIDFD-Nachweisen ausführen. Fixture-Abweichungen zur installierten Summarizer-Unit müssen im Bericht stehen. Beliebige Worker dürfen nicht im produktiven Dienst freigeschaltet werden.

Abnahme verlangt Rohbericht, Paket-/Runtime-Hashes, Negativfälle, bestätigtes Instanzende, Cleanup und unabhängigen Review. C5, E3 und v0.2 bleiben offen.

Die bereits von Kaan ausgeführte begrenzte Probe aus #150 meldete am 18.10.2026, 03:31:57 UTC, 13/13 Fälle und Cleanup PASS am Installationscommit `9b11f7a89fe948d65d6b2eb3337111a5b11d3a57`. SHA-256 des lesbaren Operator-Protokolls: `0bb0f50ada7640cd5b229646e08d6bc91737b558eb7652b1c4f094ad2a356344`. Eine Nachkontrolle fand keine Core-/Worker-Prozesse oder temporären Probeverzeichnisse. Der rootgeschützte Originalbericht wurde über diesen Zugang nicht direkt gelesen; das Operator-Protokoll wurde gegen Kaans Ausgabe abgeglichen.

Stand der lokalen finalen Umstrukturierung: normale Suite 1.213 Tests OK (105,199 s), Demo 15/15 PASS, Persistenz 3/3 PASS; eigene PostgreSQL-Testinstanz entfernt. Python-AST, Shell-Syntax des neuen CI-Schritts und Diffprüfung PASS. Kaan führte am 18.10.2026 die elf Angriffs-Komponententests direkt per Termius mit dem beschriebenen setpriv-Profil und vollständig aktiver produktiver Privilegienprüfung aus: 11 Tests in 2,748 s, OK. Quelle ist die von Kaan übermittelte Terminalausgabe, kein unabhängig ausgelesener Rohbericht. Damit ist das lokale Komponenten-Gate erfüllt; frische CI und Review des veröffentlichten Reviewfix-Commits stehen separat aus. Der fehlgeschlagene Rootless-Lauf bleibt ein fehlgeschlagener Versuch. Die systemd-Betriebsabnahme bleibt HOLD.
