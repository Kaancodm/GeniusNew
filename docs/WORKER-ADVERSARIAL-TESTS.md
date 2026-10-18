# Angriffstests für Worker-Sandbox und Core-Antwortprüfung

## Umfang und Evidenzgrenze

Diese Tests ergänzen die begrenzte Zielhost-Probe aus PR #150. Sie laufen als unprivilegierter Benutzer in frischen Kindprozessen, ausschließlich mit synthetischen Dateien und einer lokalen TCP-Gegenstelle. Sie kontaktieren weder den installierten Worker-Socket noch Datenbanken und verändern keine installierten Quellen, systemd-Units, Konten, Secrets oder Anker.

Basis: `29488feac128c4774095da54a8ab9b4c276ace96`. Die produktiven Worker- und Sandbox-Module sind gegenüber dem in #150 geprüften Installationsstand `9b11f7a89fe948d65d6b2eb3337111a5b11d3a57` unverändert.

Im Sandbox-Kind läuft die echte Funktion `_run_service_connection` einschließlich Landlock, Seccomp, Audit-Hook, Ressourcenlimits, Nondumpability und Protokollprüfungen. Ausschließlich die festen UID-/Gruppenbedingungen, die systemd-gebundene Jobverzeichnis-Ermittlung und die Summarizer-Factory werden durch ausdrücklich benannte Test-Fixtures ersetzt. Die Standardausgaben bleiben `/dev/null`, die tatsächliche Deskriptortabelle und der AF_UNIX-Peer werden geprüft. Der Kindprozess verweigert Root-Ausführung.

Für manipulierte Antworten läuft der unveränderte Core-Code `_CoreExchange` gegen einen eigenen bösartigen Testpeer. Credentials und PIDFD kommen vom echten Kernel. Die Test-Lifecycle-Bindung prüft den tatsächlich erzeugten Kind-PIDFD; sie bildet keine systemd-Abnahme nach. Signaturen, echte Jobs, produktive Schlüssel und Freigaben werden dabei nicht erzeugt.

**Damit sind dies ausführbare Komponenten-Nachweise. Sie schließen die vier systemd-/UID-Betriebsgates aus #150 nicht allein. `operational_acceptance` bleibt HOLD.** Ein einfacher positiver Summarizer-Aufruf kann keine Ausführung eines bösartigen Worker-Rumpfes belegen.

## Fälle

| Risiko | Tatsächlicher Nachweis |
| --- | --- |
| Datei außerhalb des Jobverzeichnisses | Lesbare synthetische Datei: Python-Read ergibt `ISOLATION_VIOLATED`; vorab geladener nativer `open` wird mit `EACCES` abgelehnt. Native Dateierzeugung außerhalb wird ebenfalls verweigert; keine Datei bleibt zurück. |
| Netzwerk | Python-Socket-Erzeugung ergibt `ISOLATION_VIOLATED`. Vorab geladenes natives TCP-`connect` zu einem nachweislich erreichbaren Loopback-Listener ergibt `EACCES`. Kein Anspruch auf kernel-seitige UDP-/Unix-Socket-Sperre. |
| Nativer Prozessstart | Vorab geladenes natives `fork` wird mit `SIGSYS` beendet, nachdem READY/Peer-PIDFD geprüft und der Request geschrieben wurde. Beliebige Exceptions oder Timeouts zählen nicht als Erfolg. Der Kontrollprozess erzeugt nur ein eigenes Kind, das sofort mit festem Status endet und geerntet wird. |
| Manipulierte Antwort | Echter Core lehnt falsche Nonce, falschen Request-Digest, Zusatzfeld, falschen Output-Typ, unbekannten Fehlercode, nichtkanonisches und doppeltes JSON, Übergröße, verkürzten Frame und Nachlaufbytes ab. Ein gültiger Testpeer wird angenommen; falsche Kernel-UID wird vor Lifecycle-Bindung abgelehnt. |

Die nativen Read-/Write-/TCP-/Fork-Fälle haben ungeschützte positive Kontrollen in eigenen Kindprozessen. Dadurch kann ein fehlender Dateizugriff, ein unerreichbarer Listener oder eine ungeeignete Payload nicht still als Sandbox-Erfolg gelten.

## Reproduktion

Im Checkout auf einem Linux-Host mit Landlock ABI mindestens 4, Seccomp und PIDFD-Kontrollnachrichten, als unprivilegierter Benutzer und mit den bestehenden gepinnten Abhängigkeiten:

```sh
python -B -W error::ResourceWarning -m unittest discover -s tests -p test_worker_service_adversarial.py -v
```

Keine Abhängigkeit und kein neuer produktiver Einspeisepfad werden ergänzt. Die Hilfsdatei `tests/worker_service_attack_child.py` gehört ausschließlich zu den Tests.

## Bereits erhobene Host-Evidenz aus #150

Der von Kaan ausgeführte Lauf vom 18.10.2026, 03:31:57 UTC, meldet 13/13 begrenzte Fälle und Cleanup PASS am Installationscommit `9b11f7a89fe948d65d6b2eb3337111a5b11d3a57`. Das lesbare Operator-Protokoll hat SHA-256 `0bb0f50ada7640cd5b229646e08d6bc91737b558eb7652b1c4f094ad2a356344`. Eine separate Nachkontrolle fand keine Core-/Worker-Prozesse und keine temporären Probeverzeichnisse. Der rootgeschützte Originalbericht wurde über diesen Zugang nicht direkt gelesen; das Operator-Protokoll wurde gegen die von Kaan vorgelegte Ausgabe abgeglichen. Private Host-/Bootkennungen und Serverpfade werden hier nicht veröffentlicht.

## Noch erforderliches Gate

Ein isolierter systemd-Testhost muss dieselben vier Angriffsgruppen mit real getrennten Core-/Worker-UIDs, einer geschützten Kopie des exakten Runtime-Stands und gebundenen Unit-/Invocation-/PIDFD-Nachweisen ausführen. Die Fixture-Abweichungen zur installierten Summarizer-Unit müssen im Bericht explizit stehen; dieser Testaufbau darf keine beliebigen Worker im produktiven Dienst freischalten. Abnahme verlangt Rohbericht, Paket-/Runtime-Hashes, Negativfälle, nachgewiesenes Instanzende, Cleanup und unabhängigen Review. C5, E3 und die vollständige v0.2-Abnahme werden durch diese Tests nicht als erledigt markiert.

## Gezielte Verifikation vom 18.10.2026

- Zehn Testmethoden inklusive zehn manipulierter Antwortvarianten: PASS, 3,166 s, ResourceWarning als Fehler.
- Vier kontrollierte Gegenproben: Die Tests werden jeweils rot, wenn in einer nur dafür erzeugten Wegwerf-Fixture Landlock, Seccomp oder Audit-Hook entfällt oder die Core-Ergebnisprüfung umgangen wird. Die drei Sandbox-Gegenproben ergeben je einen Assertion-Failure ohne Testfehler; die Core-Gegenprobe ergibt fünf Assertion-Failures ohne Testfehler. Die unveränderte Baseline ist grün. Keine produktive Datei wurde für diese Gegenproben geändert.
- AST-Prüfung beider Python-Dateien: PASS.
- Vollsuite, Demos und GitHub-CI werden separat im Wissensblock des PRs belegt; diese gezielten Nachweise ersetzen sie nicht.

Ergänzende Gesamtprüfung desselben Codeinhalts:

- Vollsuite: **1.223 Tests OK**, 107,959 s; ResourceWarning als Fehler.
- HTTP-/Angriffs-Demo: **PASS, 15/15**.
- Persistenz-Demo: **PASS, 3/3**.
- Eigene PostgreSQL-17-Wegwerfinstanz aus bereits vorhandenem Image, dynamischer Host-Port ausschließlich an `127.0.0.1`; Container anschließend erfolgreich gestoppt und automatisch entfernt.
- Lokaler Restore-Drill: **REFUSED / kein PASS**. `scripts/demo_restore.py::ci_container` verlangt `GITHUB_ACTIONS=true` und einen exakten CI-DSN mit Port 5432. Der tatsächliche lokale Testlauf verwendet einen dynamischen Loopback-Port und keine CI-Kennung. Keine dieser Bedingungen wurde geändert oder fingiert. Der echte CI-Restore-Nachweis bleibt offen.
- Keine produktiven Python-Module geändert; deshalb kein neuer produktiver Refusal-Guard-Eintrag. Die vier Gegenproben ergänzen die bestehenden Guards, ersetzen sie nicht.
