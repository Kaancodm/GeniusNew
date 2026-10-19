# A2/C5-Systemprobe: getrennte Core-/Worker-UIDs unter systemd

**Status:** Werkzeug für die gesondert freizugebende Systemprobe aus dem
A2/C5-Entwurf ([PR #140](https://github.com/Kaancodm/GeniusNew/pull/140),
`docs/WORKER-SERVICE-A2-C5-DRAFT.md` dort). Die Probe ist **keine**
Runtime-Implementierung: Sie ändert weder `geniusnew/` noch Units, Rechte oder
Dienste eines echten Betriebs. Sie prüft auf einem Wegwerf-Host, ob die
Betriebssystem-Eigenschaften tragen, auf die der Entwurf baut. Code:
`tools/a2c5_probe/`, Tests: `tests/test_a2c5_probe.py`.

## Was sie prüft

Ein Core-Prozess (eigene UID, ohne Root, Capabilities oder systemd-Schreibrechte)
spricht über einen `Accept=yes`-Socket mit je einer Worker-Instanz (andere UID).
Jede Instanz läuft in einer gemeinsamen Worker-Slice. Der Core nimmt eine
kooperative `flock`-Lease, prüft vor jedem Connect, dass keine frühere Instanz
lebt oder auf Bereinigung wartet, liest READY mit Kernel-Credentials auf **jedem**
Empfangsstück, bindet den Absender an genau eine Unit-Invocation und sendet erst
dann Nutzdaten. Die Lease fällt erst, wenn systemd, die Kernel-Cgroup und das
verwaltete Job-Verzeichnis das Ende bestätigen.

| Prüfpunkt | Befunde | PASS nur wenn |
| --- | --- | --- |
| 1. Kernel-Credentials vor Nutzdaten, auch fragmentiert | `C1.fragmented_ready`, `C1.wrong_uid_service`, `C1.sender_switch`, `C1.early_ready`, `C1.unattributed_early_data`, `C1.descriptor_passing`, `C1.foreign_client` | READY in ≥ 3 Stücken, jedes mit Worker-UID/-GID desselben Prozesses, Absender = `MainPID` der Unit; jede Fehlidentität (fremde UID, zweiter Prozess mitten im Frame, READY vor HELLO, Daten vor `SO_PASSCRED`, `SCM_RIGHTS`) wird vor dem ersten Nutzdatenbyte abgelehnt, **beide Seiten** zählen 0 Nutzdatenbytes; ein fremder Client ohne Socket-Gruppe bekommt `EACCES`, mit Gruppe keine Antwort |
| 2. Serielle Belegung, zweiter Dispatch verweigert | `C2.two_core_processes`, `C2.two_threads_one_core`, `C2.second_connection_without_lease`, `C2.max_concurrency` | Von zwei gleichzeitigen Dispatches (zwei Core-Prozesse; zwei Threads mit je eigener Dateibeschreibung) läuft genau einer, der andere scheitert an der Lease **vor** dem Connect (`NAccepted` +1); eine zweite Verbindung ohne Lease bekommt keine Antwort; der Root-Monitor sieht über den ganzen Lauf höchstens eine belebte Instanz |
| 3. Bestätigtes Ende und Bereinigung | `C3.end_after_success`, `C3.end_after_hang`, `C3.end_after_crash`, `C3.early_close_then_linger`, `C3.core_restart_reconciliation`, `C3.unreadable_state_refuses` | Nach Erfolg, Hänger (SIGTERM ignoriert, Kind in neuer Session), Absturz (`abort` mit zurückgelassenem Kind), Weiterleben nach RESULT und SIGKILL des Cores mitten im Job: Ende erst bestätigt, wenn Unit beendet, Cgroup leer/entfernt und Job-Verzeichnis entfernt sind; danach kein Prozess der Worker-UID mehr; der neu gestartete Core verweigert, solange die alte Instanz lebt, und verbindet sich nicht; unlesbarer Cleanup-, Cgroup- oder systemd-Zustand führt zur Ablehnung vor dem Connect |
| 4. Kein Zugriff auf Test-Secrets und Anker-Testzustand | `C4.worker_identity`, `C4.test_secrets`, `C4.anchor_test_state`, `C4.core_only_paths` | Worker hat nur seine UID/GID, keine fremden Gruppen, keine Capabilities, ist nicht dumpbar; Lesen von Root-Secret-, DSN-, Nutzdaten- und Anker-Kanarien, Secret- und Anker-Verzeichnis, Lease, `/proc/<core>/environ` und Connect auf den Worker-Socket scheitern mit `EACCES`/`EPERM`, über Python **und** direkt über libc; Positivkontrollen: Core liest sein Secret, Worker liest eine öffentliche Kontrolldatei, alle Ziele liegen mit erwartetem Eigentümer und Modus vor |

`P.*` (Vorbedingungen) und `X.cleanup` (alles wieder entfernt) gehören zu jedem
Bericht.

**Urteile.** FAIL heißt: eine Eigenschaft wurde verletzt (Nutzdaten an falsche
Identität, zwei Instanzen gleichzeitig, Ende bestätigt bei lebendem Prozess,
lesbares Secret, Connect trotz unlesbarem Zustand). HOLD heißt: nicht belegt –
Zustand nicht lesbar, unbekannte Form, Szenario nicht ausgelöst, Positivkontrolle
fehlt. Unbekannt wird nie zu PASS. Gesamturteil: FAIL vor HOLD vor PASS; PASS nur,
wenn jeder Befund PASS ist. Exit-Status 0/1/2 für PASS/FAIL/HOLD.

## Was sie nicht prüft

- Landlock, Seccomp und die Sandbox der Worker-Code-Ausführung (`docs/ISOLATION-A2.md`,
  `tests/test_isolation.py`). Der Probe-Worker läuft bewusst ohne sie, damit die
  UID-Grenze und das systemd-Lebensende allein belegt werden.
- Payloads in Journal oder Core-Dumps und die Dump-Sperre gegen untrusted Code.
  Geprüft wird nur, dass der Worker vor READY nicht dumpbar ist und `RLIMIT_CORE`
  0/0 hat; Journal und `core_pattern` werden nicht ausgewertet.
- Signale, `ptrace` und `process_vm_readv` gegen fremde Prozesse.
- Das spätere Protokoll und seine Signatur: Die Probe signiert nichts, nutzt keine
  Schlüssel und keinen Teil von `geniusnew/`.
- Der Root-Monitor tastet alle 20 ms ab; eine Überlappung kürzer als ein Takt
  bliebe unbemerkt. Die Szenarien halten Instanzen sekundenlang.
- Eine Verbindung, die noch in der Listen-Warteschlange liegt und von systemd noch
  nicht angenommen wurde, sieht die Leerlaufprüfung nicht (keine Unit, keine Cgroup,
  kein Job-Verzeichnis). Die Probe löst das nicht aus; für die Implementierung bleibt
  es eine offene Frage.

## Voraussetzungen des Testhosts

Ein isolierter Linux-Host oder eine Wegwerf-VM **ohne** produktive Dienste,
Schlüssel oder Daten: systemd als PID 1, reines cgroup v2 unter `/sys/fs/cgroup`,
D-Bus-Systembus, `useradd`/`userdel`/`groupadd`/`groupdel`, `git`, Python ≥ 3.11 unter
einem nur für `root` schreibbaren Pfad, ein sauberer Git-Checkout des zu prüfenden
Commits (auch ohne unversionierte Dateien). Netzwerk wird nicht gebraucht; die Probe nutzt nur Unix-Sockets unter `/run`.

## Ausführen

```sh
git checkout <voller SHA>          # sauber, ohne lokale Änderungen
sudo python3 -m tools.a2c5_probe run --disposable-host --report-dir /root/gnprobe-reports
```

Ohne `--disposable-host`, ohne Root, ohne systemd als PID 1 oder bei unsauberem
Checkout legt die Probe nichts an und schreibt nur einen HOLD-Bericht. Ein Lauf
dauert etwa eine Minute. Das Berichtsverzeichnis nicht in den Checkout legen.

## Was sie am Host ändert

Für die Dauer eines Laufs, mit zufälliger Lauf-ID `<id>`:

- vier Systemnutzer `gnp<id>c|w|a|f` (Core, Worker, Anker, Fremd) mit eigener
  Gruppe, ohne Home und Login, und eine Socket-Gruppe `gnp<id>ipc`;
- `/run/gnprobe<id>/` mit Code-Kopie (root, 0644), Konfiguration ohne Secrets,
  zufälligen Kanarien als Test-Secrets und den Test-Nutzdaten (Core 0600 in 0700; der Worker erhält Nutzdaten nur über die geprüfte Verbindung) und Anker-Testzustand
  (Anker 0600 in 0700), Lease (root:Core 0640), Socket-Verzeichnis (root:ipc 0750);
- je Szenario eine `.socket`- und eine `@.service`-Unit unter `/run/systemd/system`
  (flüchtig), eine Slice `gnprobe<id>w.slice` und transiente Core-Units über
  `systemd-run`.

Am Ende stoppt die Probe alle Units, entfernt Unit-Dateien, `/run/gnprobe<id>`,
Nutzer und Gruppen und prüft das (`X.cleanup`). Bricht die Probe ab, läuft die
Bereinigung trotzdem; was sie nicht bestätigen kann, steht als HOLD im Bericht.
Kanarien sind kurzlebige Testwerte im Sinn von AUTO 6 der Ausführungs-Gates
(`docs/EXECUTION-GATES.md` auf Branch `chatgpt/execution-gates-20261006`, noch nicht
auf `main`), keine produktiven Schlüssel.

## Bericht

`a2c5-probe-<Zeit>.json` und `.md`: Gesamturteil, voller Commit-SHA und ob der
Checkout sauber war, Kernel, OS, systemd, Python, Landlock-ABI, numerische UIDs/GIDs,
SHA-256 der ausgeführten Probe-Dateien, Fristen und je Befund Urteil und
Beobachtungen. Beobachtungen sind nur Zahlen, Wahrheitswerte und kurze feste Texte
(Ablehnungsgründe, errno-Namen, Unit-Namen). Vor dem Schreiben prüft die Probe, dass
weder Kanarien noch die Test-Nutzdaten im Bericht stehen; sonst ersetzt sie ihn durch
einen HOLD-Bericht ohne Beobachtungen (`X.report_redaction`).

## Beobachtungen für den Runtime-Entwurf (#140)

Aus dem ersten Lauf (siehe PR), auf einem einzelnen Kernel und systemd belegt:

- Bei `Accept=yes` hält systemd eine eigene Kopie der Verbindung, bis die Instanz
  tot ist. EOF beim Core bedeutet damit zugleich: Instanz beendet. Ein Worker, der
  nach RESULT weiterlebt, verzögert EOF bis zum Cgroup-Ende; mit der Fristregel aus
  #140 wird daraus „kein gültiges Ergebnis“. Fristen müssen das einplanen.
- `SO_PEERCRED` auf der Client-Seite nennt den systemd-Listener (UID 0), nicht die
  antwortende Instanz – wie im Entwurf vermutet, also kein Identitätsnachweis.
- Daten, die vor `SO_PASSCRED` geschrieben werden, kommen mit PID 0 und Overflow-UID
  an und sind damit ablehnbar; die Reihenfolge „Connect, `SO_PASSCRED`, HELLO“ trägt.
- `MaxConnections=1` beantwortet eine zweite Verbindung nicht; als alleinige
  Serialisierung reicht das nach dem Entwurf trotzdem nicht.
