# A2/C5: Getrennter Worker-Dienst und Betriebsvertrag

**Status: ENTWURF / HOLD.** Basis ist `main`
`62b6d1966da5e30fe71f230629a48dea369ae62c` vom 08.10.2026. Dieses Dokument
aktiviert keinen Dienst und erteilt keine Freigabe für Code, Installation oder
Änderungen an Sicherheitskonfigurationen. Der Entwurf benötigt Kaans
Architekturentscheidung und die Design-Vorprüfung für eine neue Prozessgrenze nach
[`docs/COLLABORATION.md`](COLLABORATION.md). Er ergänzt den bestehenden
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
              │ Accept=yes: eine Dienstinstanz je Verbindung
              ▼
       Worker-Instanz (geniusnew-worker UID; keine Secrets/DB-Rechte)
              │ Landlock + Seccomp + Ressourcenlimits vor Worker-Code
              ▼
       begrenzte Ausgabe → Core validiert und signiert → Verifier → Audit
```

Die Socket-Unit nimmt Verbindungen auf einem **Dateisystempfad unter `/run/`** an;
abstrakte Unix-Sockets und TCP sind für diese Grenze ausgeschlossen. `Accept=yes`
startet pro Verbindung eine neue, nach genau einem Request endende Dienstinstanz.
Die Service-Unit setzt `User=geniusnew-worker`, eine eigene primäre Gruppe, keine
ergänzenden Gruppen und keine Linux-Capabilities. Der Core erhält insbesondere
**weder Root noch `CAP_SETUID`/`CAP_SETGID`**. Er verbindet sich als
`geniusnew-core` über eine ausschließlich für diesen Socket bestimmte Gruppe.
Der Anker-Nutzer gehört keiner dieser Gruppen an. Installationsbefehle und Units
gehören erst in einen gesondert freigegebenen Umsetzungs- und Betriebs-PR.

Der Socket-Knoten liegt in einem von `root` kontrollierten Verzeichnis, das nur
Core und systemd traversieren können. Die spätere Installation muss Eigentümer,
Gruppe, Modus, kanonischen Pfad und Symlink-Freiheit **vor** dem ersten Dispatch
prüfen. Ein Socket auf einem anderen Pfad oder mit erweiterten Zugriffsrechten ist
kein Fallback. Die Instanz bekommt allein die von systemd angenommene Verbindung;
sie öffnet keinen eigenen Listener. Dienst und Socket dürfen keine öffentliche
Netzwerkfreigabe erzeugen.

Eine pro Verbindung neue Instanz enthält keine Nutzdaten früherer Jobs. Sie besitzt
weder Core-Root-Secret noch `WorkerAuthority`, Handoff-Schlüssel, DB-Zugang oder
Ankerzustand. Die bestehende Worker-Code-Sandbox bleibt verpflichtend: mindestens
Landlock ABI 4, Seccomp gegen Prozessstart, POSIX-Limits, Audit-Hook und nur das
Job-Verzeichnis plus geprüfte Laufzeitpfade lesbar. Kann eine Schicht nicht gesetzt
werden, endet die Instanz vor dem Aufruf von `Worker.run`.

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

| Schritt | Vorgeschlagener Vertrag für die spätere Implementierung |
| --- | --- |
| Verbindungsaufbau | Core prüft absoluten Socket-Pfad, Eigentümer/Modus und die erwartete Dienstkonfiguration. Fehler, fehlender Socket, unbekannte UID oder unklarer Zustand: `ContractError`, kein lokaler Worker-Fallback. |
| Core-Identität | Die Worker-Instanz prüft auf der angenommenen AF_UNIX-Verbindung die vom Kernel gelieferten Peer-Credentials gegen die **konfigurierte numerische Core-UID**. Die Gruppenzugehörigkeit öffnet nur den Socket; sie ersetzt nicht die UID-Prüfung. |
| Worker-Identität | Der Core verlangt beim ersten Antwort-Frame vom Kernel geprüfte Sender-Credentials der **konfigurierten numerischen Worker-UID** (`SO_PASSCRED`/`SCM_CREDENTIALS`). Er verlässt sich bei Socket-Aktivierung nicht allein auf `SO_PEERCRED` des Listeners. Fehlt der Nachweis, ist er mehrdeutig oder wechselt der Sender, wird verweigert. Das konkrete Verhalten auf dem Ziel-systemd muss vor Code durch einen isolierten Probetest bestätigt werden. |
| Request | Genau ein versionierter, kanonischer JSON-Frame pro Verbindung, mit festem Worker-Typ `summarize`, einer Kopie des vorhandenen `text`-Payloads und engen Ressourcenlimits. Kein frei wählbarer Python-Modulpfad, `read_paths`, Kommando oder Dateipfad im Wire. Der Dienst leitet seine Allowlist aus dem installierten Code ab und verweigert fehlende, ungültige oder zu hohe Limits, statt sie still zu ersetzen. |
| Antwort | Genau ein kanonischer Frame mit Request-Digest und entweder validierbarer Ausgabe oder einem Wert aus einer geschlossenen Fehlercode-Menge; danach EOF. Keine Exceptions, Logs, Pfade oder beliebigen Fehlertexte im Wire. Der Core vergleicht den Digest, prüft Credentials und Ausgabe und signiert erst danach. |
| Framing | Vier-Byte-Länge in Netzwerk-Byteorder, anschließend höchstens 32 KiB kanonische JSON-Bytes. Null, Überlänge, Trunkierung, Zusatzbytes, zweiter Frame, unbekannte Version/Felder und nichtkanonische Kodierung sind Ablehnungen. Beide Seiten begrenzen Lesen, Schreiben, offene FDs und Zeit. Kein Descriptor-Passing. |
| Lebensdauer | Eine Verbindung, ein Job, ein Prozess. Nach Erfolg oder Fehler endet die Instanz. Zeitablauf, Crash, Signal, UID-Wechsel oder Protokollfehler liefern keinen Erfolg und lösen **keinen automatischen zweiten Dispatch** aus. |

`SO_PEERCRED` und `SO_PASSCRED` sind Linux-spezifisch; der Entwurf bleibt wie die
bestehende Worker-Sandbox Linux-only. Die Identitätsprüfung ist nur gültig, wenn
Core- und Worker-UID verschieden, stabil konfiguriert und ohne fremde
Gruppenmitgliedschaft sind. Ein kompromittierter Core bleibt eine eigene
Vertrauensgrenze; eine andere Worker-UID allein macht ihn nicht unabhängig vom
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
signiertes Erfolgsergebnis noch eine stille Ersatz-Ausführung. Ein Timeout nach
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
| `geniusnew-core` | Eigene Core-Konfiguration und Root-Secret; bestehende Runtime-DB-Rolle; Connect auf genau den Worker-Socket; Client des getrennten Ankers. | Root, `CAP_SETUID`, `CAP_SETGID`, Schreibrecht auf Ankerzustand, Eigentum am Worker-Socket. |
| `geniusnew-worker` | Lesbarer installierter Python-/GeniusNew-Code und genau ein temporäres Job-Verzeichnis; angenommener Socket-FD. | Root-Secret, DB-DSN und DB-Login, Ankerzustand/-Socket, Core-API-Keys, Netzwerk-Listener, ergänzende Gruppen oder Capabilities. |
| `geniusnew-anchor` | Eigener Zustandsordner und vorhandener Anker-Socket gemäß C2. | Worker-Socket-Gruppe, Core-DB-Rolle und Worker-Dateien. |
| `root`/systemd | Anlage der statischen Nutzer, eng begrenzten Gruppe und Socket-/Service-Units erst nach separatem Betriebs-GO. | Laufende Job-Entscheidungen oder Kenntnis der Anwendungs-Payloads im normalen Betrieb. |

Die konkrete Nutzer-/Gruppenanlage und Unit-Dateien wären Änderungen an der
Sicherheitskonfiguration. Sie sind **nicht** Teil der aktuellen Freigabe. Eine
spätere Installation muss zusätzlich belegen, dass `geniusnew-worker` weder
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
| Falscher Dienst liefert ein Ergebnis | Sender-Credentials, Request-Digest, striktes Framing und Output-Prüfung | Testdienst mit falscher UID, ohne Credentials oder anderer Antwort-Digest wird abgelehnt; bei systemd-Aktivierung die real beobachteten Credentials protokollieren. |
| Worker ruft eigene Tools oder startet Prozesse | Feste Worker-Registry, bestehende Seccomp-/Landlock-Grenze | Prompt/Worker fordert unerlaubten Tool-/Prozessaufruf: kein Aufruf; Abschalten jeder Ablehnung macht die Refusal-Suite rot. |
| Worker sendet Daten über UDP oder Unix-Sockets | Kein Netzwerk-FD außer angenommener IPC-Verbindung; explizite systemd- und Kernel-Grenze für neue Sockets | Native `socket`, `connect` und `sendto` für AF_INET/AF_INET6 und fremde AF_UNIX-Ziele unter dem Mindestkernel testen. Python-Audit-Hook allein und Landlock ABI 4 zählen nicht als Nachweis. |
| IPC hängt oder flutet Core | 32-KiB-Grenze, eine Verbindung je Job, kurze Deadlines, systemd-Verbindungs-/Prozessgrenzen | Unvollständige, überlange und endlose Frames werden begrenzt beendet; keine unbegrenzten Prozesse, FDs oder wartenden Jobs. |
| Crash nach `EXECUTION_COMMITTED` | Dauerhaft verbrannte Job-ID, kein Retry, vorhandene Audit-/Anker-Prüfung | SIGKILL vor/nach Connect, Empfang und Antwort; Neustart führt keinen Job doppelt aus und akzeptiert kein Ergebnis doppelt. |
| Worker greift über gleiche UID auf eine andere Jobinstanz zu | Neue Instanz je Verbindung, keine langlebige Payload im Worker-Dienst, Sandbox vor Worker-Code | Zwei verschiedene Jobs mit Kanarien: zweiter Job kann ersten weder aus Speicher, `/proc`, temporären Dateien noch Logs lesen. Signal-/Ptrace-/`process_vm_readv`-Versuche fail closed prüfen; ABI 4 allein ist dafür kein Nachweis. |

Die letzte Zeile ist ein **Design-Risiko**: Ein pro Verbindung gestarteter Dienst
hat zwar keine Payload-Historie, aber gleichzeitig laufende Instanzen teilen die
Worker-UID. Der spätere Umsetzungsentwurf muss entweder nachweislich Signale,
Ptrace, `process_vm_readv` und fremde Prozessdateien kernelseitig sperren oder
pro Job getrennte OS-Identitäten verwenden. Bis ein Test diese Grenze belegt,
bleibt parallele Ausführung beziehungsweise die neue Dienstarchitektur HOLD.
Landlock ABI 4 allein sperrt weder alle IPC-Wege noch alle nativen
Prozesszugriffe; siehe [Linux-Kernel-Dokumentation zu Landlock](https://docs.kernel.org/userspace-api/landlock.html).

## Geplanter Dateischnitt und Abnahme

**In diesem Entwurfs-PR geändert:** nur dieses Dokument. Keine Runtime-, Test-,
CI-, Unit-, Secret-, Rechte- oder `SECURITY.md`-Datei wird verändert.

**Erst nach neuem GO in einem getrennten Implementierungs-PR zu prüfen:**

| Datei/Ort | Geplanter Änderungszweck |
| --- | --- |
| `geniusnew/isolation.py`, `geniusnew/isolation_child.py` | Bestehenden lokalen Kindprozess im `serve`-Pfad durch strikt begrenzten IPC-Client und eine einmalige Worker-Instanz ersetzen; Kernel-Sandbox und Ressourcenlimits erhalten. |
| `geniusnew/wiring.py`, `geniusnew/config.py` | Socket-/UID-Betriebsprofil nur für den Dienst konfigurieren und vor Listener prüfen; Demo/Test-Injektionen bleiben ausdrücklich getrennt. Kein unbemerkter Fallback. |
| Neues kleines `geniusnew/worker_service.py` | Versioniertes Protokoll, Kernel-Credentials, eine Anfrage je Dienstinstanz, feste Worker-Registry, kein Signierschlüssel. |
| `tests/test_isolation.py`, neue gezielte IPC-/Diensttests, `tests/test_b7_crash_recovery.py` | Negative UID-, Secret-, Protokoll-, Zeit-, Parallelitäts- und Crash-Tests; beide Seiten der Grenze tatsächlich ausführen. |
| `scripts/refusals.py`, gegebenenfalls `.github/workflows/verify.yml` | Jede neue Ablehnung mutieren; disposable CI-Umgebung mit real verschiedenen UIDs und gegebenenfalls systemd-Integration nachweisen. |
| C5-Dokumentation/Unit-Vorlagen in Abstimmung mit #117 | Getrennte Nutzer und Rechte, Socket-Aktivierung, Backup/Restore, Rotation, E3-Protokoll; keine Übernahme fremder Änderungen ohne Abstimmung. |
| `SECURITY.md` | Nur in einem eigens freigegebenen Grenz-PR mit umgekehrten Boundary-Tests aktualisieren. |

Abnahme erfordert zuerst die Design-Vorprüfung der Prozessgrenze und Kaans
Entscheidungen zu parallelen Worker-Instanzen, tatsächlichen systemd-Credentials
und dem künftigen Betriebsprofil. Danach: gezielte Negativtests, vollständige
Unittests mit echtem PostgreSQL, Demo, Persistenz-/Restore-Demo und
Refusal-Guard **nacheinander** auf demselben Head, `contracts` grün, unabhängiger
Security-Review und C5/E3-Nachweis auf einem sauberen Linux-Testhost. Kein Mock
einer UID oder eines Peer-Credentials zählt als OS-Isolationsnachweis.

**HOLD:** Bis zu einem neuen ausdrücklichen GO keine Implementierung, Installation,
Änderung von Sicherheitskonfigurationen, kein Main-Merge und kein Deployment.
