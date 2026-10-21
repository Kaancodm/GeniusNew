# Begrenzte Abnahme des tatsächlichen Worker-Dienstes

Status: Implementiertes Prüfwerkzeug, noch kein Live-Abnahmenachweis. Die vollständige Betriebsabnahme bleibt ausdrücklich **HOLD**.

Das Werkzeug verwendet die unveränderte, installierte GeniusNew-Implementierung aus Main-Commit `a7308289977d4017cfc1a521100db3e0d01ccc98`; alle installierten Quellen werden vor dem Lauf gegen das Manifest geprüft. Es ersetzt keine Produktionsklassen oder Worker durch synthetische Programme. Testdaten und der nur zur Runner-Konstruktion nötige Ergebnisschlüssel sind neu erzeugte, nicht produktive Werte; `_run_worker` erzeugt keine signierten HTTP-/DB-Aufträge.

## Dateien und Standardverhalten

- `driver.py`: Root-Steuerung, festes setpriv-Profil, eigene Kindprozesse, PIDFD-gebundene Signale, Berichte und Cleanup.
- `core.py`: eigener Prozess unter UID/GID 20001 und Zusatzgruppe 20003, alle Capabilities entfernt, NoNewPrivileges. Importiert ausschließlich die zuvor hashgeprüfte installierte Implementierung. Python startet mit `-I -B`.
- `common.py`: Manifest-, Identitäts-, Ergebnis- und Aggregationsverträge ohne GeniusNew-Import.

Ohne `--run` gilt `--check`. Das prüft nur Manifest und Syntax der Helfer. Es importiert kein GeniusNew, startet keinen Prozess oder Dienst und schreibt keinen Bericht:

```sh
python3 -I -B tools/worker_acceptance/driver.py --check --manifest /absoluter/pfad/manifest.json
```

Das Manifest enthält `source_commit`, `expected_sources` (alle 23 `geniusnew/*.py` mit SHA-256) und `expected_units` (die beiden tatsächlich gerenderten `/etc/systemd/system/geniusnew-worker*`-Dateien mit SHA-256). Der Livepfad verlangt außerdem exakt die vier bekannten SQL-Migrationsdateien; zusätzliche Paketdateien wie Erweiterungsmodule oder Bytecode werden vor dem Import verweigert. Er verweigert abweichende Quellen, Unit-Dateien, ungeschützte Pfade oder fremde bereits laufende Core-/Worker-Prozesse.

## Ausdrücklich aktivierter Livepfad

Ein unabhängig geprüftes Paket muss unveränderlich unter einem rootkontrollierten Stagingverzeichnis liegen. Die Paketstruktur ist exakt `<Staging>/manifest.json`, `<Staging>/tools/worker_acceptance/{driver.py,common.py,core.py}` und das vorab erzeugte rootkontrollierte Verzeichnis `<Staging>/reports`. Bestehende Berichte werden nicht überschrieben.

Die geprüften Installationsdateien gehören nicht zum Staging-Paket. `expected_sources` verwendet Manifestpfade `geniusnew/<modul>.py`; diese werden unter `/opt/geniusnew/.venv/lib/python3.13/site-packages/geniusnew/<modul>.py` aufgelöst. `expected_units` bezeichnet `/etc/systemd/system/geniusnew-worker.socket` und `/etc/systemd/system/geniusnew-worker@.service`. Die vier Migrationen liegen unter `/opt/geniusnew/.venv/lib/python3.13/site-packages/geniusnew/migrations/`: `0001_core_foundation.sql`, `0002_pending_jobs.sql`, `0003_approval_store.sql` und `0004_audit_chain.sql`.

Der Root-Aufruf benötigt gleichzeitig `--run --acknowledge-worker-test --manifest <Staging>/manifest.json --report-dir <Staging>/reports` und isoliertes Python mit deaktivierter Bytecode-Ausgabe. Freigabe und Paketprüfung sind Aufgaben des Bedieners. Dieses Dokument ist keine Live-Ausführungsfreigabe.

Der Driver verändert keine Accounts, Mitgliedschaften, installierten Dateien, Unit-Konfigurationen, Aktivierung, Core-HTTP-Dienste, Datenbanken, Secrets oder Anker. Er erzeugt nur ein eigenes temporäres `/run/gn-worker-acceptance-*` mit rootkontrollierten lesbaren Helfern und den Bericht im Stagingverzeichnis. Die Kind-Core-Prozesse führen echte Jobs am vorhandenen socketaktivierten Worker aus.

## Serielle Fälle und Erfolgskriterien

1. Deterministischer positiver Kontrolllauf mit exakter erwarteter Antwort und bestätigtem Idle-Zustand.
2. Core A hält die echte Lifecycle-Lease. Der echte Runner in einem separaten Core B muss wegen belegter Lease verweigern; Trace belegt null `_connect`-Aufrufe. Danach frischer positiver Kontrolllauf.
3. Sieben Protokollfälle nach echten Credentials/PIDFD-gebundenem READY und echter `lifecycle.bind`: falsche Nonce, Zusatzfeld, `wall_seconds=3.0` über dem festen Ceiling 2.0, nichtkanonisches JSON, vollständiger zu großer kanonischer Request, abgeschnittener Frame und Bytes nach dem Request. Erfolg verlangt keinen Antwort-Byte, belegtes Instanzende über `await_end`, vollständigen Cleanup und danach einen neuen erfolgreichen Kontrolllauf. Timeout oder beliebige Exception zählt niemals als erwartete Ablehnung.
4. An beiden beobachteten Stellen – nach READY/Bind vor REQUEST sowie nach vollständig zurückgekehrtem Request-Write vor EOF – wird der tatsächliche Worker über seine positiv verifizierte PIDFD gestoppt und nur der eigene Core-Kindprozess getötet. Ein frischer Core muss bei weiter lebendem altem Worker vor jeder Verbindung verweigern. Danach SIGCONT, bestätigter Cleanup und neuer erfolgreicher Core.
5. An denselben beiden Stellen wird nur der authentisierte tatsächliche Worker über PIDFD getötet. Der Core muss `WorkerBoundaryError` liefern; danach vollständiger Cleanup und ein neuer erfolgreicher Core.

Der Trace beobachtet Aufrufe/Rückkehrwerte und ersetzt keine Implementierungsmethode. `after_request_write` verlangt tatsächliche Rückgabebytes gleich dem kanonischen REQUEST. Ein Exception-Unwind mit `return None` zählt nicht als abgeschlossener Write.

Vor jedem Worker-Signal bindet der Driver PIDFD/PID, alle UID-/GID-Werte, tatsächliche Cgroup samt Prozessliste, MainPID, aktive InvocationID und unveränderte Unit-Quelle. Signale gehen nur an behaltene PIDFDs. Es gibt keine PID-Suche als Signalziel und keinen PID-basierten Signal-Fallback. Ein Fehler zwischen Spawn und PIDFD-Erhalt schließt die eigene Control-Pipe und wartet begrenzt auf den Kindalarm. Registrierte Kinder und gestoppte Worker werden auch bei SIGINT, SIGTERM, SIGHUP oder Ausnahme bereinigt.

## Bericht und verbleibende Grenzen

Der vollständige JSON-Bericht wird nach `reports/worker-acceptance.json` und auf stdout ausgegeben. Er enthält nur feste Statuscodes, Prüfungsnamen, Hashes, Zeiten und begrenzte Hostmetadaten (UTC, Hostname, Kernel, Architektur, Boot-ID, Python-Version). Keine Payloads, Geheimnisse, Umgebungsvariablen oder beliebigen Exceptiontexte. Fehlerausgaben der Kinder werden nicht weitergegeben.

Ein bereits vorhandener Bericht und ungültige Hostmetadaten werden vor dem ersten Livefall verweigert. Beim abschließenden Schreiben verhindert `O_EXCL | O_NOFOLLOW` weiterhin Überschreiben und Symlink-Folgen.

Exit 0 bedeutet ausschließlich, dass alle 13 begrenzten Fälle plus Cleanup bestanden sind (`bounded_suite=PASS`). Exit 2 bedeutet HOLD/unklare oder fehlgeschlagene Evidenz. **`operational_acceptance` bleibt auch bei Exit 0 immer `HOLD`.**

Nicht durch diese echten Dienste nachgewiesen: bösartige dynamische Worker-Rümpfe für Dateisystem, Netzwerk und native Syscalls sowie manipulierte Worker-Antwort-Fixtures. Der fest installierte Summarizer führt solche Angriffsrümpfe nicht aus. Auch DB-`EXECUTION_COMMITTED`, HTTP-Replay und doppelte Annahme werden durch diese Transport-/Crash-Probe nicht erneut nachgewiesen; deren B7-/Vertrags-Gates bleiben getrennt.

SIGKILL des Root-Drivers und Hostausfall sind nicht abfangbar: Dann fehlen eigener Abschlussbericht und Cleanup-Garantie. Die feste Dienstgrenze `RuntimeMaxSec=12s`/`TimeoutStopSec=2s` ist eine zusätzliche Betriebsgrenze, ersetzt aber keinen nachgewiesenen Cleanup. Fehlende Nachweise bleiben HOLD.

## Entwicklungsprüfung ohne Livewirkung

```sh
python3 -B -m unittest discover -s tests -p 'test_worker_acceptance_*.py'
python3 scripts/refusals.py tools/worker_acceptance/common.py
python3 scripts/refusals.py tools/worker_acceptance/core.py
python3 scripts/refusals.py tools/worker_acceptance/driver.py
git diff --check
```

Die drei Module sind im zentralen Refusal-Guard mit dem fokussierten Unittestmuster registriert; Scanner, vollständige unmutierte Baseline und Mutationslogik bleiben unverändert. Host-/Liveprüfungen werden von den Unittests weder gestartet noch durch ein Unit-PASS behauptet. Für DB-Verträge der vollständigen Baseline darf ausschließlich eine dedizierte wegwerfbare PostgreSQL-Instanz verwendet werden.
