# A2/C5: Getrennter Worker-Dienst und Betriebsvertrag

**Status: IMPLEMENTIERUNG IN PR #140; SYSTEMABNAHME HOLD.** Dieser Entwurf
basierte ursprünglich auf `main` `62b6d1966da5e30fe71f230629a48dea369ae62c`
vom 08.10.2026. Nach der Design-Freigabe vom 09.10.2026 hat Kaan am 17.10.2026
die reversible Implementierung des getrennten Worker-Pfads in PR #140 beauftragt.
Kaan hat anschließend Merge, Installation und Aktivierung ausdrücklich beauftragt;
diese Freigabe ersetzt keine erfolgreiche Systemabnahme. Der Serverpfad
bleibt bis zur unabhängigen Gemini-Prüfung und einer bestandenen echten
UID-/systemd-Probe ohne Betriebsabnahme. Der Entwurf ergänzt den bestehenden
[C5-Betriebsentwurf in PR #117](https://github.com/Kaancodm/GeniusNew/pull/117),
ohne dessen Dateien zu übernehmen oder zu ersetzen.

## Ausgangspunkt und Ziel

[`docs/ROADMAP-V02.md`](ROADMAP-V02.md) verlangt für A2 einen unprivilegierten
Worker-Nutzer zusätzlich zur Landlock-Allowlist. Für C5 müssen Kern und Anker als
getrennte Dienste, ihre Nutzer, Backup/Restore und Schlüsselrotation betrieblich
nachvollziehbar sein. Die Landlock-Mindestgrenze ist bereits implementiert.
`geniusnew/isolation.py::_run_isolated` startet das Kind heute jedoch mit
`subprocess.Popen` ohne UID-Wechsel; Kind und Core haben daher dieselbe OS-UID.
Der C5-Entwurf #117 hält fest, dass dadurch auch PostgreSQL-`peer` den Worker nicht
vom Core unterscheiden kann. Eine systemd-Unit um den unveränderten Core löst das
nicht: deren `User=` gilt auch für die vom Core gestarteten Kinder.

**Ziel dieses Vorschlags:** Der Core bleibt unprivilegiert, der untrusted Worker läuft
unter einer anderen, festen OS-UID und kann weder Core-Secret und Datenbank-DSN noch
Ankerzustand lesen. Ein fehlender oder nicht eindeutig authentisierter Worker-Dienst
führt zur Ablehnung. Die bisherige Ausführungs-, Approval-, Signatur-, Audit- und
Crash-Recovery-Reihenfolge bleibt erhalten. Für die erste Abnahme ist nur der bereits
konfigurierte `DeterministicSummarizer` vorgesehen; weitere Worker-Typen sind ein
eigener Vertrag.

## Vorschlag: ein Dienstprozess je Verbindung

```text
HTTP → Core (geniusnew-core UID; Policy, Gateway, Ledger, WorkerAuthority)
              │ genau eine lokale AF_UNIX-Stream-Verbindung je Dispatch
              ▼
       systemd-Socket (root:geniusnew-worker-ipc, 0660)
              │ Core-Lifecycle-Lease + Accept=yes, MaxConnections=1
              ▼
       Worker-Instanz (geniusnew-worker UID; keine Secrets/DB-Rechte)
              │ HELLO → Landlock + Seccomp + feste Dienstlimits
              │ credential-geprüftes READY → Nutzdaten → Worker-Code
              ▼
       begrenzte Ausgabe → Core validiert und signiert → Verifier → Audit
```

Die Socket-Unit nimmt Verbindungen auf einem **Dateisystempfad unter `/run/`** an;
abstrakte Unix-Sockets und TCP sind für diese Grenze ausgeschlossen. `Accept=yes`
startet pro Verbindung eine neue, nach genau einem Request endende Dienstinstanz.
`MaxConnections=1` begrenzt Verbindungen dieser Socket-Unit, ist aber **keine
hostweite UID-Sperre und kein Beleg für beendete Prozesse**. Vorgeschlagen sind
zusätzlich `KillMode=control-group`, `SendSIGKILL=yes`, `Restart=no` und feste
Start-, Laufzeit- und Stop-Deadlines. Ein beendeter Hauptprozess, EOF oder das
Absenden von SIGKILL genügen nicht zur Freigabe des nächsten Jobs. Der folgende
Lifecycle-Vertrag muss die gesamte Belegung einschließlich Bereinigung abdecken.
Ein zweiter Dispatch wird bei belegtem Slot sofort abgelehnt; keine zusätzliche
Job-Warteschlange, kein automatisches Wiedersenden, kein lokaler Fallback.
Alle Einstellungen und ihre Laufzeitwirkung sind im separat freizugebenden
systemd-Test nachzuweisen; Konfigurationen werden hier nicht installiert.
Die Semantik dieser Vorgaben
steht in [systemd.socket(5)](https://man7.org/linux/man-pages/man5/systemd.socket.5.html)
und [systemd.kill(5)](https://man7.org/linux/man-pages/man5/systemd.kill.5.html).
Die Service-Unit setzt `User=geniusnew-worker`, eine eigene primäre Gruppe, keine
fremden ergänzenden Gruppen und keine Linux-Capabilities. Die in der Gruppenliste
wiederholte primäre GID ist zulässig. Der Core erhält insbesondere
**weder Root noch `CAP_SETUID`/`CAP_SETGID`**. Er verbindet sich als
`geniusnew-core` über eine ausschließlich für diesen Socket bestimmte Gruppe.
Der Anker-Nutzer gehört keiner dieser Gruppen an. Die Unit-Vorlagen liegen in
`ops/worker/`; Installation und Aktivierung bleiben gesondert freizugeben.

Der Socket-Knoten liegt in einem von `root` kontrollierten Verzeichnis, das nur
Core und systemd traversieren können. Die spätere Installation muss Eigentümer,
Gruppe, Modus, kanonischen Pfad und Symlink-Freiheit **vor** dem ersten Dispatch
prüfen. Ein Socket auf einem anderen Pfad oder mit erweiterten Zugriffsrechten ist
kein Fallback. Die Instanz bekommt allein die von systemd angenommene Verbindung;
sie öffnet keinen eigenen Listener. Dienst und Socket dürfen keine öffentliche
Netzwerkfreigabe erzeugen.

Eine neue Instanz darf erst nach bestätigter Bereinigung des Vorgängerjobs
Nutzdaten erhalten. Ein neuer Prozess allein beweist diese Eigenschaft nicht.
Sie besitzt weder Core-Root-Secret noch `WorkerAuthority`, Handoff-Schlüssel, DB-Zugang oder
Ankerzustand. Die bestehende Worker-Code-Sandbox bleibt verpflichtend: mindestens
Landlock ABI 4, Seccomp gegen Prozessstart, POSIX-Limits, Audit-Hook und nur das
Job-Verzeichnis plus geprüfte Laufzeitpfade lesbar. Kann eine Schicht nicht gesetzt
werden, endet die Instanz vor dem Aufruf von `Worker.run`.

## Serielle Belegung bis zum bestätigten Ende

**Invariante für v0.2:** Auf einem Host existiert für die konfigurierte Worker-UID
höchstens eine Jobinstanz, einschließlich Start, Handshake, Ausführung, Beendigung
und Bereinigung. Die UID ist ausschließlich für dieses Betriebsprofil reserviert;
keine interaktiven Logins, andere Dienste oder zweiten Listener unter dieser UID.
Parallele Worker sind ein eigener späterer Architekturentscheid.

Der Core hält dafür eine nichtblockierende exklusive `flock`-Lease auf einem
festen, von `root` angelegten Inode unter `/run/`. Alle Core-Instanzen für dieselbe
Worker-UID verwenden denselben geprüften Pfad und Inode. Jeder Erwerb verwendet
eine eigene offene Dateibeschreibung; kein geteilter oder duplizierter Lease-FD
zwischen konkurrierenden Dispatches/Threads. Nur die Core-Gruppe darf
die Datei öffnen; Worker und Anker weder öffnen, ersetzen noch löschen. Pfad,
Owner, Modus, Symlink-Freiheit und lokale Dateisystemsemantik sind Start-Gates.
Die Lease bleibt auf der vertrauenswürdigen Core-Seite und wird weder per FD noch
per IPC an den Worker übergeben. Sie ist keine Erhöhung der Core-Privilegien.
Die zusätzlich benötigte Lease-Datei gehört erst in einen freigegebenen Betriebs-PR.
`flock` ist eine kooperative Sperre zwischen vertrauenswürdigen Core-Instanzen,
keine eigenständige Worker-Sandbox; siehe [flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html).

**Host-Sicht ohne zusätzliche Core-Rechte:** Ein direkter Vergleich mit
`/proc/1/ns/*` scheitert für den unprivilegierten Core an der Linux-Ptrace-Prüfung.
Der Implementierungsvertrag verwendet deshalb einen bootgebundenen,
root-kontrollierten Beleg unter `/run/geniusnew-worker/ipc/host-state.json`
(`root:SOCKET_GID`, `0440`). Der bereits vertrauenswürdige Host-Bootpfad erstellt
ihn vor dem Core-Start aus dem echten Host-Zustand. `WorkerLifecycle.host_reference()`
prüft dafür Root, die volle UID-/GID-Abbildung, gleiche Mount-/PID-/User-/Cgroup-
Namespaces und gleichen Prozess-Root wie PID 1 sowie die installierten Pfade.
Die Methode liefert kanonische Bytes; sie installiert nichts und wird nicht
im Dispatch aufgerufen. Der Core vergleicht seine eigenen Namespace-Handles,
Boot-ID und Pfad-Inodes (Root, Procfs, Cgroup-Root, Runtime-Root und Lease) mit
dem Beleg; nach Lock-Erwerb vergleicht er auch den geöffneten Lease-FD. Die
Procfs-Sicht muss weiterhin vollständig sein. Jeder fehlende, unlesbare,
veraltete oder abweichende Beleg verweigert vor Connect und Nutzdaten.

Die Referenz beweist Gleichheit mit dem durch Root bereitgestellten Zustand;
ihre Herkunft aus dem tatsächlichen Host muss der freigegebene Installations-
und Bootpfad gewährleisten. Container-Root ist kein Hostnachweis. Es entsteht
kein neuer dauerhaft privilegierter Dienst und der Core erhält keine neue
Capability. Die Core-Unit darf auch keine indirekt eingerichteten privaten
Namespaces verwenden. Provisionierung und Neuaufbau nach Reboot stehen in
[`ops/worker/README.md`](../ops/worker/README.md) und bleiben Teil der echten
Systemabnahme; bis dahin **HOLD**.

Nach Erwerb der Lease und **vor Connect** muss der Core über nur lesbare,
vertrauenswürdige systemd-/Cgroup-Zustände ausschließen, dass eine frühere
Worker-Instanz noch lebt oder ihre Bereinigung aussteht. Nach dem Handshake wird
die Kernel-PID der erwarteten Unit-Invocation und Cgroup zugeordnet; unklare
Zuordnung verweigert den Payload. Ein UID-Vergleich ist kein Nachweis, welches
Programm läuft. Installierter Bootstrap, Unit und ihre unveränderlichen Codepfade
gehören deshalb ebenfalls in die Startprüfung. Der Core bekommt keine
systemd-Schreibrechte, Polkit-Ausnahme oder zusätzlichen Capabilities.

Der Slot wird erst freigegeben, wenn systemd das Ende der gebundenen Invocation,
eine leere Cgroup einschließlich Nachkommen und die abgeschlossene Bereinigung
der privaten Jobpfade bestätigt. Die Bereinigung darf nicht nur der untrusted
Worker behaupten; vorgesehen ist dienstmanagerverwalteter temporärer Zustand,
kein Worker-eigener Erfolgsmarker. Nutzdaten dürfen weder in persistenten Logs
noch Core-Dumps verbleiben. Ein hängender Prozess oder ein unlesbarer/unbekannter
Lifecycle-Zustand sperrt neue Jobs fail closed. `READY`, Ergebnis und EOF sind
**keine** Freigaben der Lease.

Stürzt der Core ab, kann seine Kernel-Sperre verschwinden, obwohl der Worker lebt.
Jede neue Core-Instanz muss daher unter derselben Lease die vollständige
Lifecycle-Abstimmung erneut ausführen, bevor sie verbindet. Die bloße Abwesenheit
eines Locks oder ein neu erzeugter Socket ist kein Leerlaufnachweis. Wie
Unit-Invocation, Cgroup-Leere und Bereinigung auf dem Ziel-systemd ohne zusätzliche
Core-Rechte zuverlässig gelesen werden, bleibt ein zwingendes Probe-Gate. Scheitert
es, bleibt dieser Dienstentwurf HOLD; keine stillschweigende neue Privilegierung.
Die Probe erzwingt auch `AccessDenied`, fehlende Dateien und unlesbare Invocation-,
Cgroup- oder Cleanup-Zustände: jeweils Ablehnung vor dem nächsten Connect/Payload,
niemals Interpretation eines Lesefehlers als leere Cgroup oder beendeter Job.

## Keine Nutzdaten in Logs oder Speicherabbildern

Die spätere Worker-Service-Unit muss `StandardOutput=null` und
`StandardError=null` explizit setzen; kein geerbter Journal-, Datei- oder TTY-FD.
Das IPC-Protokoll läuft ausschließlich über den von systemd angenommenen,
explizit verwendeten Socket-FD, **nicht über stdout/stderr**. Auch Tracebacks und
native Schreibversuche auf FD 1/2 dürfen keinen Payload im Host-Journal erzeugen.
Neue Journal-/Log-Sockets und fremde Logdateien bleiben durch die unverändert
geforderten nativen Socket- und Dateigrenzen unerreichbar. Diagnostik enthält nur
feste Statuscodes aus einer geschlossenen Menge, keine Worker-Fehlertexte.

`LimitCORE=0` (Soft- und Hardlimit) ist eine zusätzliche Schutzschicht, reicht
allein aber bei einem Pipe-Core-Handler nicht aus; siehe
[core(5)](https://man7.org/linux/man-pages/man5/core.5.html). Der vertrauenswürdige
Bootstrap muss vor READY und vor jeder Payload `PR_SET_DUMPABLE=0` setzen und
prüfen. Vor untrusted Code sperrt Seccomp das erneute Aktivieren sowie weitere
UID-/GID-/Dateisystem-ID-Wechsel; Prozessstart und Exec bleiben bereits verboten.
Fehlt eine Schicht, gibt es kein READY und keine Nutzdaten. Die Wirkungen auf
`/proc`-Leserechte sind bei der Invocation-Zuordnung mitzuprüfen; siehe
[PR_SET_DUMPABLE(2)](https://man7.org/linux/man-pages/man2/PR_SET_DUMPABLE.2const.html).
Die Implementierung setzt diese Bootstrap-Prüfung; ihre Kernelwirkung bleibt
Teil der echten Systemprobe. Die globale Core-Dump-Konfiguration und bestehende
Systemdienste werden in diesem PR nicht geändert.

## Autorität und IPC-Vertrag

Der Core prüft weiterhin den signierten Handoff über das Gateway, verbraucht
Approvals, erhält einen einmaligen `DispatchPermit` und commitet
`EXECUTION_COMMITTED` mit Audit **vor** der Worker-Ausführung. Die
`WorkerRunner`-Grenze bleibt auf der Core-Seite: Nur sie hält
`WorkerAuthority`, prüft Tool, TTL und Ergebnisform und signiert den validierten
Ausgang. Die Worker-Instanz erhält **keinen** Handoff, Permit, Approval-Token,
Principal, Policy, Schlüssel oder Job-Ledger-Zugriff. Die bisherige
`WorkerVerifier`-Rolle bleibt ausschließlich öffentlich. Dieser Entwurf behauptet
keine zusätzliche kryptografische Trennung zwischen Core und Ergebnis-Signierer:
der Core hält heute bereits `WorkerAuthority`, und ein gemeinsames Root-Secret
bleibt die in [`SECURITY.md`](../SECURITY.md) dokumentierte Grenze.

| Schritt | Vertrag für die Implementierung |
| --- | --- |
| Verbindungsaufbau | Core prüft absoluten Socket-Pfad, Eigentümer/Modus und die erwartete Dienstkonfiguration. Fehler, fehlender Socket, unbekannte UID oder unklarer Zustand: `ContractError`, kein lokaler Worker-Fallback. |
| Core-Identität | Die Worker-Instanz prüft auf der angenommenen AF_UNIX-Verbindung die vom Kernel gelieferten Peer-Credentials gegen die **konfigurierte numerische Core-UID**. Die Gruppenzugehörigkeit öffnet nur den Socket; sie ersetzt nicht die UID-Prüfung. |
| Bootstrap ohne Nutzdaten | Nach Connect aktiviert der Core `SO_PASSCRED`, bevor er genau ein `HELLO` mit Version und frischer, zufälliger 256-Bit-Nonce als 64 Hexzeichen sendet. HELLO enthält weder Text, Job-/Nutzerkennung, Request-Digest noch textabhängige Metadaten. Die Worker-Instanz sendet vorher nichts und prüft zunächst die Core-Peer-UID. |
| Worker-Identität vor Payload | Der vertrauenswürdige Bootstrap setzt feste Dienstlimits und die erforderlichen Sandbox-Schichten und antwortet genau einmal mit `READY`, Version und derselben Nonce. Der Core liest mit `recvmsg`, verlangt kernelgeprüfte `SCM_CREDENTIALS` der **konfigurierten numerischen Worker-UID** und bindet PID/GID sowie Unit-Invocation an diese Verbindung. Erst nach vollständiger Frame-, Nonce-, Credential- und Lifecycle-Prüfung darf er den Request senden. Kein Vertrauen allein in `SO_PEERCRED` des systemd-Listeners oder in behauptete IDs im JSON. READY ist keine kryptografische Sandbox-Attestierung; deren Wirksamkeit braucht reale Negativtests. |
| Request | Erst nach verifiziertem READY genau ein versionierter, kanonischer JSON-Request mit der Handshake-Nonce, festem Worker-Typ `summarize`, einer Kopie des vorhandenen `text`-Payloads und engen Ressourcenlimits. Kein frei wählbarer Python-Modulpfad, `read_paths`, Kommando oder Dateipfad im Wire. Der Dienst leitet seine Allowlist aus dem installierten Code ab und verweigert fehlende, ungültige oder zu hohe Limits, statt sie still zu ersetzen. Engere Request-Limits werden vor `Worker.run` zusätzlich angewandt, niemals feste Dienstlimits gelockert. |
| Antwort | Genau ein kanonischer RESULT-Frame mit Nonce, Request-Digest und entweder validierbarer Ausgabe oder einem Wert aus einer geschlossenen Fehlercode-Menge. Danach schließt die Worker-Instanz den Socket; der Core wartet das dadurch entstehende serverseitige EOF innerhalb der Gesamtdeadline ab. Fehlendes EOF, Zusatzdaten oder Timeout verweigern ein signiertes Erfolgsergebnis. Keine Exceptions, Logs, Pfade oder beliebigen Fehlertexte im Wire. Der Core vergleicht Nonce und Digest, prüft unveränderte Sender-Credentials und Ausgabe und signiert erst nach vollständiger Protokollprüfung einschließlich EOF. Die Lifecycle-Lease bleibt unabhängig davon bis zum bestätigten Instanzende gehalten. |
| Framing und Zustände | Exakt `HELLO → READY → REQUEST → RESULT → EOF`, also je zwei Frames pro Sender, nicht zwei Jobs. Jeder Frame hat eine Vier-Byte-Länge in Netzwerk-Byteorder; HELLO/READY höchstens je 256 Byte, REQUEST/RESULT höchstens je 32 KiB kanonische JSON-Bytes. Null, Überlänge, Trunkierung, Zusatzbytes, wiederholte oder vertauschte Frames, unbekannte Version/Felder und nichtkanonische Kodierung werden abgelehnt. Harte Phasen- und Gesamtdeadlines werden durch Fragmente nicht verlängert. |
| Ancillary-Daten | Credential-Prüfung für alle empfangenen Worker-Daten einschließlich Länge, fragmentierter Frames und Ergebnis; keine Lese-Abkürzung über `recv`, die Ancillary-Daten verwirft. Fehlende, widersprüchliche, abgeschnittene (`MSG_CTRUNC`) oder gewechselte Credentials verweigern. Kein Descriptor-Passing: `SCM_RIGHTS` und unbekannte Control-Messages verweigern; bereits angelieferte FDs schließen, damit auch Ablehnungen kein FD-Leck erzeugen. |
| Lebensdauer | Eine Verbindung, ein Job, eine Worker-Instanz. Nach Erfolg oder Fehler muss sie vollständig enden. Lease-Freigabe erst nach bestätigtem Ende und Bereinigung, nie schon bei RESULT/EOF. Zeitablauf, Crash, Signal, Identitätswechsel oder Protokollfehler lösen **keinen automatischen zweiten Dispatch** aus. |

`SO_PEERCRED` und `SO_PASSCRED` sind Linux-spezifisch; der Entwurf bleibt wie die
bestehende Worker-Sandbox Linux-only. Die Identitätsprüfung ist nur gültig, wenn
Core- und Worker-UID verschieden, stabil konfiguriert und ohne fremde
Gruppenmitgliedschaft sind. `SO_PASSCRED` wird beim Core erst nach Connect und
vor HELLO gesetzt; so braucht der Bootstrap keinen vor Connect automatisch
angelegten abstrakten Client-Socket. Dass READY nicht vor diesem Zeitpunkt
ankommt und Credentials bei fragmentierten Streams vollständig prüfbar sind,
ist ausdrücklich Gegenstand der Systemprobe. Ein kompromittierter Core bleibt eine
eigene Vertrauensgrenze; eine andere Worker-UID allein macht ihn nicht unabhängig vom
Ergebnis-Signierer. Die Kernel- und systemd-Eigenschaften hinter diesem Vorschlag
sind in [unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html),
[systemd.socket(5)](https://man7.org/linux/man-pages/man5/systemd.socket.5.html)
und [systemd.exec(5)](https://man7.org/linux/man-pages/man5/systemd.exec.5.html)
dokumentiert. **Der tatsächlich gelieferte Credential-Nachweis bei
`Accept=yes` ist ein Abnahmegegenstand, kein hier behauptetes Testergebnis.**

## Fehler-, Zustands- und Restore-Vertrag

Der bestehende `Orchestrator.dispatch` commitet die Ausführung, bevor
`WorkerEndpoint.dispatch` läuft. Ein IPC-Ausfall danach kann deshalb nicht durch
erneutes Senden desselben Jobs „geheilt“ werden. Er wird als `ContractError` in den
bestehenden `DispatchAttempted`-Pfad überführt: die Job-ID bleibt verbrannt, der
Audit-Nachweis der Ausführungsentscheidung bleibt erhalten, und es gibt weder ein
signiertes Erfolgsergebnis noch eine stille Ersatz-Ausführung. Auch ein
fehlgeschlagener HELLO-/READY-Handshake nach diesem Commit verbraucht
die Job-ID, obwohl keine Nutzdaten übertragen wurden; die Commit-Reihenfolge wird
für den Handshake nicht verschoben. Ein Timeout nach
einem möglicherweise ausgeführten Worker bleibt **unbekannter Ausgang**. Eine
Protokollantwort ist erst nach vollständiger Längen-, Digest-, Credential- und
Output-Prüfung eine akzeptierbare Antwort. Auf Neustart prüft der Core zuerst
DB, Ledger, Audit und unabhängigen Anker; ein Worker-Dienst darf diese Prüfungen
nicht umgehen. Der Worker-Dienst selbst hat keinen persistenten Jobzustand, der
aus einem Backup rekonstruiert werden dürfte.

Der C5-Backup-/Restore-Vertrag aus #117 und der bereits getestete Restore-Drill
bleiben maßgeblich: Ein DB-Backup hinter dem unabhängigen Anker startet nicht
stillschweigend. Eine Wiederherstellung von Socket oder Worker-Prozess ist kein
Ersatz für die verankerte Historie. Die Rollen- und Pfadprüfung nach einem Restore
muss vor dem Listener wiederholt werden. Schlüsselrotation ist ein eigener
auditierter Ablauf; dieser Entwurf erzeugt oder rotiert keine Schlüssel.

## Identitäten und künftig benötigte Berechtigungen

| Identität | Minimal benötigter Zugriff | Ausdrücklich ausgeschlossen |
| --- | --- | --- |
| `geniusnew-core` | Eigene Core-Konfiguration und Root-Secret; bestehende Runtime-DB-Rolle; Connect auf genau den Worker-Socket; Core-only Lifecycle-Lease und lesbarer systemd-/Cgroup-Zustand; Client des getrennten Ankers. | Root, `CAP_SETUID`, `CAP_SETGID`, Schreibrecht auf Ankerzustand, Eigentum am Worker-Socket. |
| `geniusnew-worker` | Lesbarer installierter Python-/GeniusNew-Code und genau ein temporäres Job-Verzeichnis; angenommener Socket-FD. | Root-Secret, DB-DSN und DB-Login, Ankerzustand/-Socket, Core-API-Keys, Netzwerk-Listener, Core-Lifecycle-Lease, andere Gruppen als seine feste primäre GID oder Capabilities. |
| `geniusnew-anchor` | Eigener Zustandsordner und vorhandener Anker-Socket gemäß C2. | Worker-Socket-Gruppe, Core-DB-Rolle und Worker-Dateien. |
| `root`/systemd | Anlage der statischen Nutzer, eng begrenzten Gruppe und Socket-/Service-Units erst nach separatem Betriebs-GO. | Laufende Job-Entscheidungen oder Kenntnis der Anwendungs-Payloads im normalen Betrieb. |

Die konkrete Nutzer-/Gruppenanlage und Unit-Dateien ändern die
Sicherheitskonfiguration. Kaans ergänzendes Betriebs-GO vom 17.10.2026 autorisiert
diese Arbeiten; eine Installation muss zusätzlich belegen, dass `geniusnew-worker` weder
`peer`-Zugang als Core noch Leserechte auf Core- oder Anker-Dateien erlangt.

## Bedrohungsmodell und negative Nachweise

Angreifer ist zunächst Worker-Code, der absichtlich Python und native Syscalls
ausnutzt. Hinzu kommen ein lokaler fremder Benutzer, ein fehlerhafter Core-Client
und ein abgestürzter oder fehlkonfigurierter Worker-Dienst. `root`, Kernel und
systemd gelten für diese Prozessgrenze als Trusted Computing Base; ein
kompromittierter Core ist wegen des gemeinsamen Root-Secrets gesondert zu
bewerten.

| Risiko | Geplante Grenze | Reproduzierbarer Nachweis für die spätere Abnahme |
| --- | --- | --- |
| Worker liest Secret, DSN oder Ankerzustand | Andere UID, private Datei-/Socketrechte, Landlock-Allowlist, kein Secret im IPC | Job versucht Lesen jedes Kanarienpfads per Python und rohem Syscall; Ausgabe und Logs enthalten keinen Kanarienwert. UID und Dateirechte am Testhost messen. |
| Fremder Prozess ersetzt Socket oder gibt sich als Core aus | Root-kontrollierter Pfad, Socketgruppe nur für Core, Peer-UID-Prüfung | Fremd-UID und Worker-UID können weder verbinden noch gültigen Request einspeisen; Symlink/Mode-/Owner-Manipulation führt vor Dispatch zu `ContractError`. |
| Falscher Dienst erhält Nutzdaten | HELLO ohne Jobdaten; Credential-/Nonce-/Invocation-geprüftes READY vor REQUEST | Ein Testdienst mit falscher UID erhält **null Nutzdatenbytes**, auch bei gültig aussehendem READY; ebenso verweigern fehlende Credentials, falsche Nonce, verspätetes/doppeltes READY und Identitätswechsel. Positive Kontrolle: korrekte UID erhält genau einen Request. |
| Manipulierte Antwort oder Ancillary-Daten | Gebundene Identität, Nonce, Request-Digest, striktes Framing | Falscher Digest, Credentials-Wechsel zwischen Header und Body, fragmentierte Frames, `MSG_CTRUNC`, `SCM_RIGHTS` und Zusatzbytes verweigern; FD-Anzahl bleibt auch nach wiederholten Angriffen begrenzt. |
| Worker ruft eigene Tools oder startet Prozesse | Feste Worker-Registry, bestehende Seccomp-/Landlock-Grenze | Prompt/Worker fordert unerlaubten Tool-/Prozessaufruf: kein Aufruf; Abschalten jeder Ablehnung macht die Refusal-Suite rot. |
| Worker sendet Daten über UDP oder Unix-Sockets | Kein Netzwerk-FD außer angenommener IPC-Verbindung; explizite systemd- und Kernel-Grenze für neue Sockets | Native `socket`, `connect` und `sendto` für AF_INET/AF_INET6 und fremde AF_UNIX-Ziele unter dem Mindestkernel testen. Python-Audit-Hook allein und Landlock ABI 4 zählen nicht als Nachweis. |
| IPC hängt oder flutet Core | 32-KiB-Grenze, eine Verbindung je Job, kurze Deadlines, systemd-Verbindungs-/Prozessgrenzen | Unvollständige, überlange und endlose Frames werden begrenzt beendet; keine unbegrenzten Prozesse, FDs oder wartenden Jobs. |
| Crash nach `EXECUTION_COMMITTED` | Dauerhaft verbrannte Job-ID, kein Retry, vorhandene Audit-/Anker-Prüfung | SIGKILL vor/nach Connect, Empfang und Antwort; Neustart führt keinen Job doppelt aus und akzeptiert kein Ergebnis doppelt. |
| Überlappende oder verwaiste Jobinstanzen | Hostweite Core-only Lease, exklusives UID-Betriebsprofil, bestätigtes Cgroup-Ende und Bereinigung | Zwei unabhängige Core-Prozesse und zwei konkurrierende Threads desselben Cores senden gleichzeitig; nur einer erwirbt den Slot. Worker schließt früh den Socket und lebt weiter, Kind hängt, Core stirbt oder startet neu: niemals zweite Worker-Instanz oder zweiter Payload vor bestätigtem Ende. Unbekannter Lifecycle-/Cleanup-Zustand verweigert. |
| Worker-Payload gelangt in Host-Logs oder Core-Dumps | Explizit null-gerichtete stdout/stderr, kein anderer Log-FD, gesperrte Dump-Reaktivierung vor Payload | Untrusted Python und native Syscalls schreiben Kanarien nach FD 1/2, drucken Tracebacks, versuchen Dump-Reaktivierung/ID-Wechsel und crashen danach. Auf dem disposable Host mit Pipe-Core-Handler dürfen Journal und Dumps keine Kanarie enthalten; korrekter IPC bleibt funktionsfähig. Fehlende Bootstrap-Sperren verweigern READY, entfernte Ablehnungen machen die Negativsuite rot. |
| Folgejob liest Reste seines Vorgängers | Dienstmanagerbestätigte Bereinigung vor Lease-Freigabe, keine Payload-Logs/Core-Dumps | Zwei aufeinanderfolgende Jobs mit verschiedenen Kanarien; zweiter Job kann den ersten weder über `/proc`, Speicher, private Jobpfade, Logs noch Core-Dumps lesen. Native Signal-/Ptrace-/`process_vm_readv`-Versuche gegen fremde Prozesse bleiben negative Abnahmefälle. |

Der Lifecycle-Vertrag bleibt ein **Abnahmerisiko**: Die feste Worker-UID darf nur diesem
Dienst gehören, und systemd muss nachweislich die Cgroup einer beendeten Instanz
vollständig leeren, bevor der nächste Job startet. Schlägt das auf dem Zielhost
fehl, ist die Dienstarchitektur HOLD; `MaxConnections=1` allein beweist keine
Isolation von einem verwaisten Prozess. Parallele Worker-Ausführung bleibt aus
dem A2/C5-Auftrag ausgeklammert und braucht pro Job getrennte OS-Identitäten
oder nachweislich kernelseitig gesperrte Signale, Ptrace,
`process_vm_readv` und fremde Prozessdateien. Landlock ABI 4 allein sperrt
weder alle IPC-Wege noch alle nativen Prozesszugriffe; siehe
[Linux-Kernel-Dokumentation zu Landlock](https://docs.kernel.org/userspace-api/landlock.html).

## Umsetzung in PR #140 und offene Abnahme

Kaan hat am 17.10.2026 die reversible Implementierung auf dem bestehenden
Draft-Branch #140 beauftragt. Der PR enthält Code, Negativtests, Refusal-Guard und
nicht installierte Unit-Vorlagen. Er verändert weder `SECURITY.md` noch Secrets,
produktive Rechte oder installierte Dienste.

| Datei/Ort | Umgesetzter Zweck |
| --- | --- |
| `geniusnew/worker_service.py`, `geniusnew/worker_bootstrap.py`, `geniusnew/wiring.py`, `geniusnew/config.py` | Fester Service-Runner im `serve`-Pfad, UID-/systemd-/Cgroup-Prüfung, Lease, versioniertes IPC mit Kernel-Credentials und Nonce; kein lokaler Fallback. |
| `geniusnew/isolation.py`, `geniusnew/workers.py` | Bestehende Landlock-/Seccomp-/Ressourcenbegrenzung im Dienst, Fehler der vertrauenswürdigen Grenze ohne signierbares Worker-Ergebnis. |
| `tests/test_worker_service.py`, vorhandene Kern- und Servertests, `scripts/refusals.py` | Negativfälle für Profil, Transport, Antworten und Lifecycle sowie Mutation jeder neuen Ablehnung. |
| `ops/worker/` | Nicht installierte Socket- und Dienstvorlagen mit getrennten numerischen UIDs und festen Rechten. |

**HOLD:** [Claudes neue Probe in PR #144](https://github.com/Kaancodm/GeniusNew/pull/144)
am Head `55d5e2605d829bb44b2d9967066747ce48042019` meldet zweimal 30/30 PASS
in einem verschachtelten systemd-Labor. Die Rohberichte samt SHA-256 fehlen im
PR zum Zeitpunkt der Integration; diese Angabe ist daher ein gemeldetes Ergebnis,
kein hier unabhängig verifizierter Abnahmenachweis. Der Zielhost ist weiterhin
UNKNOWN. Die Probe verwendet eigene synthetische Core-/Worker-Programme. Sie
führt weder den tatsächlichen GeniusNew-Dienst noch dessen Hostreferenz,
PIDFD-Bindung, Landlock/Seccomp oder festen `-I -S`-Bootstrap aus.

Für die Betriebsabnahme fehlen deshalb weiterhin die Rohberichte und ein Lauf
am finalen Implementierungs-Head mit echten unterschiedlichen UIDs, geladenen
Produktionsvorlagen, geprüftem Python-Installationsbaum und bootgebundener
Hostreferenz. Dieser Lauf muss Credential-/Invocation-/PIDFD-Bindung, Sandbox,
Crash-/Neustart-Reconciliation und vollständige Bereinigung auf dem Zielhost
belegen. Ein Mock oder der synthetische Laborlauf ersetzt ihn nicht.
Kaans gesondertes GO für Merge, Installation und Aktivierung liegt vor.
Die Betriebsabnahme bleibt vom echten Nachweis abhängig. Kaan hat für diesen PR
Gemini 4 Argon als Ersatz für Gemini Pro und Copilot autorisiert; das unabhängige
Review muss den exakten Implementierungs-Head nennen.

Der Dienst verwendet einen festen absoluten Bootstrap mit `python -I -S`;
Startup-Hooks aus `.pth` und `sitecustomize` werden nicht ausgeführt. Der Core
prüft die vollständigen root-kontrollierten Python-Laufzeitbäume vor Connect.
Der Host-Installer muss deren Vertrauenswürdigkeit schon vor dem ersten Core-
oder Root-Python-Start herstellen. Einzelheiten und Platzhalter stehen in
[`ops/worker/README.md`](../ops/worker/README.md). Die Landlock-Leserechte auf
Paket und Python-Laufzeit werden dadurch nicht auf Quellpaket-Nachbarn erweitert.

Der Job-Wall-Timer im Dienst ist ein Python-Signal und kann von untrusted
Worker-Code manipuliert werden. Der Core verweigert eine Antwort nach der
zweiten, extern gemessenen Zwei-Sekunden-Grenze und hält die Lease bis zum
nachgewiesenen Instanzende; systemd setzt zusätzlich `RuntimeMaxSec=12s`.
Damit entsteht kein später signierbarer Erfolg. Die echte Probe und der
unabhängige Review müssen das Verhalten eines weiterlaufenden Workers bis zur
Bereinigung ausdrücklich bewerten.
