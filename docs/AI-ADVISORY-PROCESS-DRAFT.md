# Advisory-Engine in eigenem Prozess — Designentwurf

**Stand: 20.10.2026. DRAFT, nur Dokumentation.** Basis dieses Entwurfs ist
`main` bei `a7308289977d4017cfc1a521100db3e0d01ccc98`. Keine der hier geplanten
Prozess-, Transport- oder Sandbox-Eigenschaften ist durch diesen PR implementiert.
**Code erst nach Gemini-Design-PASS am exakten Head. Betrieb bleibt STOP bis zu
Kaans gesondertem GO**, insbesondere systemd, UID/GID-Anlage und Netzwerkregeln.

## 1. Entscheidung und belegter Istzustand

Kaan hat am 20.10.2026 die eigene Prozessgrenze mit eigener OS-UID gewählt und
diesen Doku-Auftrag bestätigt. Die Entscheidung steht in
[PR #185](https://github.com/Kaancodm/GeniusNew/pull/185),
`docs/DECISIONS.md` am Head `bb03453760a4bc4a4ae5bccab01dd2263f58ff75`.
#185 war beim Basisabgleich noch ein offener Draft; die Zeile steht am oben
genannten `main` noch nicht in [DECISIONS.md](DECISIONS.md). Dieser PR basiert
direkt auf `main`, übernimmt keine Änderungen aus #185 und meldet keinen Merge.

Anlass ist M2 aus dem
[Claude-Review zu #179](https://github.com/Kaancodm/GeniusNew/pull/179#issuecomment-6289200712):
`default_engine()` in [advisory.py](../geniusnew/advisory.py) importiert derzeit
`Pilot` aus [tools/ai_pilot/pilot.py](../tools/ai_pilot/pilot.py). Damit laufen
Modell-HTTP und Modell-JSON im Dienstprozess, dessen übrige Komponenten Secrets
und private Schlüssel halten. Die Aussage „Brücke hält keine Schlüssel“ schafft
keine Speichergrenze. [AI-ADVISORY.md](AI-ADVISORY.md) beschreibt noch diese
Implementierung; seine Einschaltanleitung ist keine Betriebsfreigabe.

M1 (Exception-Kontext) bleibt ein unabhängiger Korrektur-PR. Dieser Entwurf
behauptet weder dessen Abschluss noch ein nachgeholtes Gemini-PASS für #179.
Maßstab für die Vorprüfung sind [COLLABORATION.md](COLLABORATION.md),
[GEMINI.md](../GEMINI.md) und der [Review-Leitfaden](../.gemini/styleguide.md).

## 2. Ziel und unveränderte Grenzen

| Beteiligter | Geplante Aufgabe und zulässige Daten |
| --- | --- |
| Dienstprozess / Core | Prüft Auftragsart und begrenzten Text, spricht ausschließlich über den Advisory-AF_UNIX-Transport, prüft Antwort-Rahmen und Text und berechnet dessen SHA-256 selbst. Kein Pilot-Import, kein Modell-HTTP, kein Parsing von Modell-JSON. |
| Advisory-Engine | Eine frische Instanz je Auftrag/Verbindung, unter einer ausschließlich für Advisory verwendeten UID. Führt `tools/ai_pilot` aus, spricht mit Ollama, parst HTTP-Envelope und Modellplan und liefert ausschließlich Text oder eine feste Ablehnung. Kein Prozesspool. |
| Ollama | Bereits getrennte Modelllaufzeit. Einziges Ziel bleibt wörtlich `127.0.0.1:11434`, `POST /api/chat`. Seine Antworten sind untrusted; weder Port noch zurückgemeldete Modell-ID authentisieren den Anbieter. |
| Später freizugebender Prozessmanager | Sauberer Engine-Start, feste UID/GID, Sandbox und Ressourcen, Instanzbindung, Beendigung und Cleanup. Kein Start durch Forken des bereits mit Secrets geladenen Dienstprozesses. |

Die öffentliche Ergebnisfläche bleibt
`service.advisory.request(kind, text) -> AdvisoryResult(kind, text, text_sha256)`.
Zulässig sind weiterhin ausschließlich `summary` und `code_draft`.
Standard bleibt AUS; kein HTTP-Weg, kein LLM-Worker, kein Tool-Aufruf, kein Permit,
keine Signatur, keine Approval- oder Audit-Autorität und kein Dispatch durch
Advisory. Der SHA-256-Digest ist keine Signatur und kein Vertrauensbeweis.
Text mit angeblichen Befehlen oder Freigaben bleibt wirkungslose Eingabe.

Planer und Helfer bleiben exakt `qwen3.5:4b-q4_K_M` und `qwen2.5-coder:1.5b`,
nacheinander, nur auf dem Server. Der [P1-Vertrag](../tools/ai_pilot/README.md)
bleibt für Planfelder, einmalige 128-Bit-Task-ID, Modellprüfung und
16384-Byte-HTTP-Envelope verbindlich. Laptop, iPad, Cloud-Fallback, Modellwechsel
und Modellinstallation sind nicht Teil dieses Schritts.

## 3. Transport und OS-Identität

### 3.1 Übernommenes Worker-Muster

Vorbild ist [worker_service.py](../geniusnew/worker_service.py), insbesondere
`FrameIdentity`, `_CoreExchange.run`, `_expect_eof`, `WorkerLifecycle` und
`_run_service_connection`. Übernommen werden AF_UNIX/SOCK_STREAM, genau ein
Auftrag je Verbindung, feste Identitäten, begrenzte Rahmen, absolute Deadlines,
Authentisierung vor Nutzdaten sowie nachgewiesenes Prozessende. Das Worker-JSON-
Protokoll, Signierrollen und seine netzlose Sandbox werden nicht übernommen.
Bestehende Worker-Schutzmaßnahmen bleiben unverändert.

Das zukünftige, administrierte Profil enthält feste positive numerische
`core_uid`, `engine_uid`, `engine_gid`, eine Core-exklusive Socket-/Lease-Gruppe
und kanonische Socket-/Lease-Pfade unter `/run/`. Engine-, Dienst-, Worker-,
Anker- und Ollama-UID sind verschieden. Pfade und Profil stammen niemals aus
Auftrag, Modellantwort oder Umgebungsvariablen. Socket und Lease sowie ihre
Elternpfade sind root-kontrolliert, ohne Symlinks oder untrusted Schreibrechte;
die Engine darf weder Socketpfad noch Lease ersetzen. Für die IPC-Eltern gilt
wie beim Worker `0710`, für Socket/Lease `0660`; die Engine gehört nicht zur
Core-exklusiven Gruppe und erhält nur die angenommene Verbindung.

Das existierende [config.py](../geniusnew/config.py) erlaubt in `[ai_advisory]`
heute nur `{enabled}`. Die spätere Umsetzung muss das administrierte Profil
ausdrücklich verdrahten und validieren; seine Felder sind noch keine verfügbaren
Konfigurationsoptionen. Unvollständiges Profil bei `enabled = true` verweigert
den Start. Bei AUS erfolgen weder Pilot-Import noch Socket-/Modellzugriff.

### 3.2 Peer-Prüfung vor dem Auftrag

1. Die Engine prüft am angenommenen AF_UNIX-Stream die Dienst-UID mit
   `SO_PEERCRED`, bevor sie Anwendungsdaten verarbeitet.
2. Der Dienst sendet zunächst nur `HELLO` mit frischer Verbindungsnonce,
   **noch keinen Auftragstext**. Empfangsoptionen `SO_PASSCRED` und
   `SO_PASSPIDFD` sind vor dem Handshake aktiv.
3. Für `READY` bindet der Dienst die tatsächlichen Kernel-Sendercredentials
   (`SCM_CREDENTIALS`: UID/GID/PID) und `SCM_PIDFD` an die erwartete Engine und
   ihre überprüfte Manager-Instanz/cgroup. `READY` darf erst nach aktiver
   Sandbox gesendet werden; eine bloße Selbstaussage der Engine ersetzt keinen
   administrierten Profil- und Lifecycle-Nachweis.
4. Erst danach wird genau ein `REQUEST` übertragen. Jeder Teil der Antwort,
   einschließlich Headerfragmenten, wird gegen dieselbe Identität geprüft.
   Senderwechsel, fehlende/mehrdeutige Credentials, unzulässige Ancillary-Daten,
   `SCM_RIGHTS` und Truncation-Flags verweigern; bereits empfangene FDs werden
   auch im Fehlerfall geschlossen.

Ein durch root/systemd erzeugter Listener ist kein Nachweis der Engine-UID:
`SO_PEERCRED` allein beschreibt die Credentials zu `connect`/`listen`, nicht
automatisch den späteren Antwortsender ([unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)).
Deshalb wird das stärkere fragmentweise Worker-Muster erhalten. Seine Regeln
für gepufferte letzte Fragmente, gebundenes pidfd und Exit-Sentinels bleiben
erhalten. EOF liefert keine neue Senderidentität; es wird an die bereits
gebundene Verbindung angeknüpft und auf unerlaubte Controls/Trailing Bytes
geprüft. Fehlende Kernel-Unterstützung führt zur Ablehnung, nicht zu UID-only.

### 3.3 Kleiner binärer Rahmen, kein Modellparser im Dienst

Vorschlag zur Gemini-Vorprüfung: Jeder Rahmen beginnt mit vier Bytes
`uint32` in Netzwerk-Bytefolge. Ihr Wert `N` zählt ausschließlich die folgenden
Rahmenbytes. Der feste Teil danach ist `GNAP` (4 Bytes), Version `1` (1 Byte),
Nachrichtentyp (1 Byte), Verbindungsnonce (16 Bytes): zusammen 22 Bytes.
Die Nonce wird vom Dienst erzeugt und muss in allen Rahmen übereinstimmen;
sie ist keine Modell-Task-ID, kein Grant und kein persistenter Job-Ledger.

| Typ | Richtung / Inhalt nach den 22 Bytes | Zulässiges `N` |
| --- | --- | --- |
| `HELLO = 1` | Dienst → Engine; keine weiteren Daten | genau 22 |
| `READY = 2` | Engine → Dienst; keine weiteren Daten | genau 22 |
| `REQUEST = 3` | Dienst → Engine; 1 Byte Art (`1 = summary`, `2 = code_draft`), dann Auftragstext | 24 bis 4119 |
| `TEXT = 4` | Engine → Dienst; ausschließlich Ergebnistext | 23 bis 4118 |
| `REFUSE = 5` | Engine → Dienst; keine Nachricht, kein Traceback, keine weiteren Daten | genau 22 |

Ablauf: `HELLO`, `READY`, `REQUEST`, Schreibende des Dienstes (`SHUT_WR`),
genau ein `TEXT` oder `REFUSE`, Server-EOF und Prozessende. Die Engine verlangt
nach `REQUEST` EOF, bevor sie das Modell kontaktiert. Keine zweiten Aufträge,
keine Mehrfachantwort, keine unbekannte Version, keine Zusatzfelder, kein
Protokoll-Downgrade. Ein Fehler vor `READY` darf die Verbindung ohne Antwort
schließen; der Dienst verweigert dann ebenfalls.

`N` wird vor jeder vom Peer bestimmten Allokation gegen maximal 4119 geprüft;
nach dem festen Header gilt zusätzlich die engere Typgrenze und die erwartete
Protokollphase. Beide Texte sind maximal **4096 UTF-8-Bytes**, nicht 4096 Zeichen.
Am Python-API-Rand muss der Typ exakt `str` sein; vorläufig höchstens 4096
Zeichen zulassen, dann strikt UTF-8 kodieren und ausdrücklich die Bytezahl
auf höchstens 4096 prüfen. Eingehende Rahmen zuerst nach Bytezahl begrenzen,
dann strikt dekodieren, danach Leere/Whitespace und NUL prüfen. Keine
Ersatzzeichen, Trunkierung, Normalisierung oder Teilantwort. Ein Transport, der
statt Text ein Objekt liefert, wird ebenfalls verweigert.

Der Dienst verwendet für diesen Pfad keinen JSON-, Pickle- oder sonstigen
Objektdeserialisierer. Modell-Metadaten, Pläne, Tool-Calls und Modell-Fehlertexte
passieren die Grenze nicht. Nach Rahmen-, Text-, EOF- und Cleanup-Prüfung
berechnet er den Digest selbst über die unveränderten UTF-8-Bytes.

## 4. Zeit, Nebenläufigkeit und Abbruch

Diese Zahlen sind **Entwurfsvorgaben, keine gemessenen Laufzeitgarantien**:

| Phase | Absolute Obergrenze |
| --- | --- |
| Connect und vollständiger HELLO/READY-Handshake | zusammen 5 Sekunden |
| REQUEST schreiben/lesen und Dienst-EOF | 5 Sekunden |
| Planer und danach Helfer | jeweils 120 Sekunden einschließlich Connect, HTTP-Header, Body und Parsing; zusammen höchstens 240 Sekunden |
| TEXT/REFUSE übertragen und Server-EOF | 5 Sekunden; erstes Antwortbyte spätestens am Ende des Modellbudgets |
| Prozessende und Cleanup feststellen | 5 Sekunden |
| Gesamter Austausch einschließlich vorgenannter Phasen | höchstens 260 Sekunden ab Beginn vor Connect; jede Phase durch diese Deadline gedeckelt |

Alle Fristen sind monoton und absolut. Einzelne Bytes, erfolgreiche Reads und
Schreibfortschritt verlängern sie nicht. Beim Warten auf das erste Ergebnis gilt
das Modellbudget; das kurze Rahmenlimit darf einen zulässigen Kaltstart nicht
auf fünf Sekunden verkürzen. Das heutige `HTTPConnection(timeout=120)` im
Piloten ist allein noch keine absolute Modell-Gesamtfrist. Diese Lücke muss der
spätere Code mit Slow-Drip-Negativtests schließen.

Ein nicht blockierender Bridge-Lock und eine Core-exklusive, hostweite Lease
verhindern parallele verwaltete Advisory-Aufträge. Zusätzlich weist der Manager vor jeder neuen
Verbindung nach, dass unter dieser Engine-UID keine frühere Instanz mehr läuft.
Der Dienst bindet READY an genau diese Instanz. Nach Erfolg oder Fehler bleibt
die Lease bis zum nachgewiesenen Ende und Cleanup belegt. Unbestätigter Zustand
sperrt Folgeaufträge auch über einen Dienstneustart hinweg; kein bloßes Leeren
einer Python-Liste als Recovery. Kein Warten auf einen freien Slot und kein Retry.
Da `flock` beim Prozessende freigegeben wird, setzt der Core zusätzlich **vor
REQUEST** atomar mit bestätigtem Datei-/Verzeichnis-`fsync` einen geschützten
In-flight-/HOLD-Marker neben der Lease. Schreib-/Sync-Fehler verweigern vor REQUEST.
Dieser enthält nur den Belegungszustand, keine Aufträge oder Nutzdaten, und ist
kein zweiter Job-Ledger. Die Engine kann ihn weder schreiben noch entfernen.
Nur vollständig bestätigtes TEXT-Ergebnis plus Cleanup oder ein gesondert
geprüfter Recovery-Nachweis löscht ihn; ein verwaister Marker oder unlesbarer/
beschädigter Zustand bedeutet HOLD. REFUSE nach REQUEST kann bereits begonnene
Inferenz bedeuten und hebt den Marker nicht automatisch auf. Eine leere
Engine-cgroup allein reicht ebenfalls nicht. Der Marker liegt außerhalb
instanzbezogener Cleanup-Verzeichnisse. Unter `/run` übersteht er Core-/Engine-
Neustarts; nach Hostneustart ist ein frischer Manager-/Modellstartnachweis nötig.

Der bisherige abstrakte Unix-Datagramm-Slot des Piloten passt nicht zu einem
Verbot neuer AF_UNIX-Sockets nach dem Bootstrap. Für den Engine-Pfad wird seine
Schutzfunktion durch die verbindliche Lease-/Instanzprüfung ersetzt und separat
getestet; die eigenständige P1-CLI behält ihren Schutz. Ihre bisherige UID-Sperre
ist jedoch keine hostweite Ollama-Sperre. Die Lease-/HOLD-Garantie gilt für den
verwalteten Advisory-Pfad; andere Ollama-Clients kontrolliert sie nicht. Direkte
P1-CLI-Nutzung ist während dessen Betrieb/HOLD kein zugelassener Parallel- oder
Recovery-Weg, solange sie nicht denselben Admission-/HOLD-Pfad nutzt. Das ist
ein späterer Code-/Betriebsnachweis. Kein offener Datagramm-FD als versteckter
Nebenkanal und kein optionaler Bypass-Schalter.

Bei Timeout, Client-Abbruch oder Engine-Crash: Verbindung schließen, keine
Teilantwort freigeben, kein weiterer Helferaufruf, keine Wiederholung im Dienst.
Das erwartete `SHUT_WR` nach REQUEST ist kein Abbruchsignal: vollständigen
Peer-Abbruch, Prozessende und Fristablauf davon unterscheiden und insbesondere
vor dem Helferstart prüfen.
Ein unabhängiger Manager begrenzt die Engine-Lebensdauer; der Dienst erhält dafür
keine zusätzlichen Kill-/Root-Rechte. Eine leere Engine-cgroup beweist nicht das
Ende einer abgebrochenen Ollama-Inferenz. Ist deren Abschluss unklar, bleibt der
Advisory-Slot in HOLD bis zu einem separat geprüften Recovery-Nachweis; ein
Dienstneustart hebt HOLD nicht auf. Kein automatischer Ollama-Neustart.

## 5. Geheimnisfreiheit und Sandbox vor Modellkontakt

Die Engine erhält weder Root-Secret noch private/öffentliche Rollen-Schlüssel,
DSN, DB-Socket, Ankerzustand, Handoff oder Permit. Ihr Auftrag enthält allein Art
und begrenzten Text; der Dienst fügt keine Konfiguration oder eigenen Zustände
hinzu. Die Garantie betrifft die Umgebung der Engine, nicht eine automatische
Erkennung von Secrets in einem vom Aufrufer selbst eingereichten Text.

Der unabhängige Start nutzt feste Programm-/Importpfade, minimales Environment,
keine privilegierenden Capabilities, keine fremden Zusatzgruppen und keine
übernommenen Dienst-FDs. Reale, effektive und gespeicherte UID/GID müssen zum
Profil passen. `no_new_privs` und Nicht-Dumpbarkeit gelten vor `READY`.
Die FD-Allowlist umfasst nur die angenommene IPC-Verbindung und notwendige
Bootstrap-FDs, die anschließend geschlossen werden; stdout/stderr führen nach
`/dev/null`. Keine Prompts, Antworten oder rohen Exceptions in Journald/Core-Logs.
Der Dienst kann eigene feste Ablehnungsgründe protokollieren.

Landlock erlaubt ausschließlich die benötigte, root-installierte Python-/Pilot-
Laufzeit zum Lesen sowie ein leeres, begrenztes Engine-Arbeitsverzeichnis.
Secrets, DSN-Dateien, Anker- und Dienstzustand liegen außerhalb aller Lesepfade.
Offene Dateien werden vor der Sandbox bereinigt; nachträgliches Landlock hebt
bereits erteilte FD-Rechte nicht allgemein auf. Sandbox vor Threads und untrusted
Parsing; kein Best-Effort-Downgrade. Geplante Ressourcenobergrenzen der kleinen
Engine: 256 MiB Speicher, 30 Sekunden CPU, 32 FDs, ein Prozess, kein Core-Dump.
Das Ollama-Modell und dessen bestehende Ressourcenwerte werden nicht verändert;
die Engine-Obergrenzen sind später durch einen positiven Lauf zu bestätigen.

Eine Seccomp-Allowlist mit geprüfter Architektur sperrt neue Prozesse/Programme,
unbekannte Syscalls und Nebenwege wie `io_uring` sowie Privilegwechsel, Namespace-/Mount-
Änderungen, Ptrace, `process_vm_*`, fremde Prozess-FDs und unzulässige Signale.
Für Netzwerk bleiben nur die nötigen IPv4-TCP-Aufrufe; IPv6, UDP, Raw-Sockets,
Netlink sowie neue AF_UNIX-Verbindungen/-Sockets sind gesperrt. Die bereits
übergebene IPC-Verbindung bleibt nutzbar. Native Syscall-Proben müssen diese
Grenzen belegen; ein Python-Audit-Hook ist allein kein Nachweis.

### Netzwerk-Ausnahme mit eigener Durchsetzung

Landlock ABI ≥ 4 kann TCP-Ports begrenzen, nicht die Ziel-IP. Deshalb kombiniert
das geplante Profil **CONNECT ausschließlich zu Port 11434, kein TCP-BIND** mit
einem zusätzlichen kernel-erzwungenen IP-Filter exakt auf `127.0.0.1/32`.
Klassisches Seccomp-BPF kann den `sockaddr` hinter `connect()` nicht prüfen.
Die Worker-Netzsperre dafür zu lockern ist ausgeschlossen.

Als spätere Betriebsumsetzung kommt eine eigene Engine-cgroup mit
`IPAddressDeny=any` und `IPAddressAllow=127.0.0.1/32` infrage; das ist noch keine
Unit-Vorlage oder Änderungsfreigabe. Kernel-/cgroup-BPF-Unterstützung und tatsächliche
Wirkung sind Pflichtnachweise. Nicht unterstützte IP-Filter können wirkungslos
bleiben; eine Konfigurationszeile reicht deshalb nicht. Fehlender Landlock-,
Seccomp-, Credential-/pidfd- oder IP-Filter-Nachweis → Start verweigern, kein READY.
Kein breites `localhost`, kein DNS, Proxy, Redirect oder externer Endpoint.

**Verbleibende Grenze:** `127.0.0.1:11434` hat keine Authentisierung. Ein fremder
lokaler Nutzer kann bei freiem Port einen Ersatzdienst anbieten, auch mit
passender Modell-ID. Die separate Engine-UID authentisiert nur den IPC-Sender,
nicht Ollama oder die Richtigkeit seines Texts. IP-/Portfilter beschränken auch
keinen HTTP-Pfad: `POST /api/chat` und die Modellwahl sind Anwendungsregeln,
keine Kernel-Garantie gegen bereits kompromittierten Engine-Code. Der Zugriff
auf die lokale Ollama-API bleibt deshalb eine ausdrücklich zu prüfende Grenze;
ein authentisierender/API-beschränkender Vermittler wäre ein eigener Entwurf.
Lokaler Root/Kernel und die
administrierte Installation bleiben Vertrauensannahmen; diese Prozessgrenze
ist keine microVM und kein Schutz gegen einen kompromittierten Host.

Technische Grundlagen:
[Landlock 6.12](https://docs.kernel.org/6.12/userspace-api/landlock.html),
[Seccomp-BPF](https://docs.kernel.org/userspace-api/seccomp_filter.html),
[systemd-IP-Filter](https://www.freedesktop.org/software/systemd/man/257/systemd.resource-control.html).

## 6. Ablehnungen und geplante Nachweise

**Alle folgenden Tests sind geplant / nicht ausgeführt.** Jede neue Ablehnung
bekommt im Code-PR einen Test, der ihr Entfernen bemerkt, und das betreffende
Modul einen Eintrag in `GUARDED`. Tests prüfen Verhalten an der Grenze;
Mock-UIDs ersetzen keine Betriebssystem-Probe.

| ID | Ablehnung / Invariante | Geplanter Nachweis |
| --- | --- | --- |
| A1 | AUS, fehlendes Profil, falsche Typen/IDs, unbekannte Profilfelder | Kein Connect, Pilot-Import oder Modellaufruf; aktiv ohne gültigen Transport: `ContractError`, auch in beiden Anker-Verdrahtungen von `__main__.py`. |
| A2 | Socket fehlt, falscher Dateityp, Symlink, unsichere Eigentümer/Rechte | Ablehnung vor Auftragstext; root-kontrollierten IPC-Pfad und Austauschversuch prüfen. |
| A3 | Falsche Dienst-/Engine-UID, falsche GID, fehlende/wechselnde Credentials oder PID | Ablehnung auf beiden Seiten; falscher READY-Sender erhält keinen Prompt. Native Prüfung einschließlich root-erzeugtem Listener, PID-Reuse und fehlender pidfd-Unterstützung. |
| A4 | Falsche Länge/Magic/Version/Nonce/Phase/Art, kurzer Header/Body, zweite Anfrage/Antwort, Trailing Bytes | Grenzwerte 22/23/24/4118/4119, riesiger Längenpräfix ohne Body-Allokation, Fragmentierung und EOF-Vertrag. Unzulässige Ancillary-FDs werden nachweislich geschlossen. |
| A5 | Falscher Texttyp, leer/Whitespace, NUL, ungültiges UTF-8, Übergröße | 4096 Bytes erlaubt, 4097 verweigert, Mehrbyte-Zeichen und Surrogate; keine Normalisierung, Digest stimmt mit angenommenen Bytes überein. |
| A6 | Fehlende Verbindung, Engine-REFUSE/Crash, Timeout in jeder Phase, Slow-Drip | Generischer `ContractError`, keine Teilantwort; absolute 120-/260-Sekunden-Grenzen, schleppende Header/Body/EOF und blockiertes Schreiben. |
| A7 | Zweiter Aufrufer, zwei Prozesse derselben Engine-UID, Altinstanz oder unbestätigter Cleanup | Sofortige Ablehnung; Core-/Engine-Abbruch und Core-Neustart heben den Marker/HOLD nicht auf; kein weiterer verwalteter Advisory-Auftrag bei unklarem Modellabschluss. Crash vor/nach Marker-Sync, REFUSE nach Modellkontakt, Hostneustart und CLI als unzulässigen Parallel-/Recovery-Weg prüfen. |
| A8 | Landlock/Seccomp/IP-Filter/Ressourcenprofil fehlt oder versagt | Kein READY, kein Modellkontakt; kein schwächerer Betriebsmodus. Ressourcenüberschreitung beendet den Auftrag ohne Ergebnis. |
| A9 | Zugriff auf Dienst-Geheimnisse, Environment, geerbte FDs, Dateien, `/proc` und Anker | Synthetische Canaries unter getrennter realer UID unlesbar; `ptrace`, `process_vm_*` und Signalisieren des Dienstes verweigert. |
| A10 | Unerlaubte Netzwerk-/Prozesswege | Kontrolliertes `127.0.0.1:11434` erlaubt; andere Ports, `127.0.0.2:11434`, fremde Test-IP `:11434`, IPv6, UDP, Unix-Nebendienste, Fork/Exec und direkte native Syscalls verweigert. |
| A11 | Modell- oder Fehlerdaten erreichen Dienstparser/Logs/Exception-Kette | Kein Modell-JSON-Decoder/Pilot-Import/Modell-TCP im Dienst; Canary fehlt in `str(exc)` und `traceback.format_exception(exc)`, `__context__` und `__cause__` sind `None`. Encoding- und Transportfehler einschließen. |
| A12 | Versteckte Autorität oder Modellvertragsbruch | Text mit angeblichen Freigaben löst keine Signatur, Permit-, Audit- oder Tool-Aktion aus. Plan-Extrafelder, fremde/erneute Task-ID, falsches Modell/Helper/Kind/Input, Tool-Calls und HTTP-Envelope >16384 bleiben verweigert. |

Für A11 genügt `raise ... from None` innerhalb eines aktiven `except` nicht,
um auch `__context__` zu entfernen. Die spätere Umsetzung muss eine neutrale
Ausnahme ohne gespeicherte Parser-/Transportexception außerhalb des aktiven
Fehlerkontexts erzeugen und das ausdrücklich prüfen. Kein roher Fehlertext im
REFUSE-Rahmen. Weder Fehler noch fehlender Socket lösen jemals `Pilot.run()` im
Dienst aus; auch kein anderer Callable-, CLI-, Modell- oder Cloud-Fallback.

Zusätzlich braucht der Code-PR einen positiven echten Prozesslauf mit zwei
synthetischen lokalen Modellantworten, getrennter UID und aktivem Schutzprofil.
Die bestehenden In-Process-Erwartungen, insbesondere
`test_the_default_engine_is_the_opt_in_pilot` in
[test_advisory.py](../tests/test_advisory.py), werden dabei gezielt durch
Transport-only-Nachweise ersetzt. Keine Testabschaltung, keine behauptete
Betriebsabnahme aus einer Fake-Ollama-Unit-Suite.

## 7. Schritte und Gates

| Schritt | Deliverable | Gate / aktueller Status |
| --- | --- | --- |
| 1 — dieser PR | Dieser Entwurf und Ersatz der Advisory-Zeile in [SECURITY.md](../SECURITY.md), mit Ist-/Zieltrennung | Link-, Konsistenz- und Diff-Prüfung. Gemini-Design-Vorprüfung im PR anfordern; Ergebnis zunächst OFFEN. |
| 2 — späterer Code-PR auf dann aktuellem `main` | Transport, Engine-Einstieg, Profilvalidierung, Isolation und A1–A12; aktualisierte Advisory-Anleitung | Erst nach explizitem Gemini-Design-PASS am geprüften Head; kein offener Critical/High. Geänderte Designflächen erneut vorprüfen. M1-Korrektur separat abgleichen. |
| 3 — Implementierungsabnahme | Tests, Demo, Refusal-Guard, echte UID-/Sandbox-Probe und CI am exakten Code-Head | Gemini-Pro-Sicherheitsreview für die geänderte SECURITY-Grenze; Kaans erforderliche Freigabe. Geplant, kein PASS behauptet. |
| 4 — Betrieb | Gesonderter, überprüfbarer Installations-/Recovery-Plan | Kaans ausdrückliches GO vor systemd, UID/GID, cgroup-/Netzregeln oder Deployment. Bis dahin Advisory im Betrieb AUS. |

Gemini soll insbesondere den binären Rahmen, die asymmetrische Peer-Prüfung,
Deadline- und Cleanup-/Recovery-Regeln, das eigene Netzwerkprofil sowie die
SECRET-/DSN-/Anker-Trennung prüfen. Wegen der ersetzten `SECURITY.md`-Zeile wird
die Anfrage an ein **Gemini-Pro-Modell** gerichtet. Das Review nennt Werkzeug,
Modell, PR und vollen Head-SHA sowie je Bereich PASS/FAIL und belegte Befunde.
Eine Review-Anforderung ist noch kein Review-Ergebnis und keine Codefreigabe.
