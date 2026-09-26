# Prozessisolation v0.1

Dieses Dokument beschreibt die Worker-Grenze aus Schritt 11 von GeniusNew und die
bekannten Lücken, die ihre Tests ausdrücklich offen halten.

## Trennung der Rollen

`IsolatedWorkerRunner` hält die `WorkerAuthority` und den Ergebnissignierschlüssel im
Elternprozess. Der Worker startet über **exec in einem frischen Python-Interpreter**;
er erhält keine durch Fork geerbte Kopie des elterlichen Adressraums. Über die Grenze
gehen nur die importierbare Worker-Klassenkennung, ihr Zustand als kanonisches JSON,
Limits und eine Kopie der Payload. Das Kind liefert eine nicht vertrauenswürdige,
größenbegrenzte Nachricht in kanonischem JSON zurück. Der Elternprozess prüft vor dem
Signieren den normalen Ergebnisvertrag.

## Durchgesetzte Kontrollen

Der Python-Worker von v0.1:

- startet in einem frischen Interpreter mit geschlossenen geerbten Dateideskriptoren,
  isolierter Standardeingabe und vom Protokoll getrennten Standardausgaben;
- arbeitet in einem neuen temporären Verzeichnis je Job;
- hat eine vom Elternprozess durchgesetzte Zeitgrenze;
- erhält POSIX-Limits für CPU-Zeit, Adressraum, Dateigröße, offene Dateideskriptoren
  und Core-Dumps;
- erhält eine ersetzte Umgebung, deren Verzeichnisse im temporären Job-Verzeichnis liegen;
- darf keine Python-Socket-Operationen ausführen;
- darf nicht durch `/proc`, `/sys` oder `/dev` lesen, einschließlich der Umgebung und
  Dateideskriptoren des Elternprozesses; andere vom Dienstnutzer lesbare Pfade bleiben
  lesbar (siehe die bekannten Grenzen unten);
- darf keine Prozesse, Programme oder Shells starten und keine fremden Prozesse
  signalisieren, soweit der Aufruf ein Python-Audit-Ereignis auslöst (`os.fork`,
  `os.exec*`, `os.spawn*`, `os.posix_spawn`, `os.system`, `subprocess.Popen`, `os.kill`);
  der unten getestete Weg über `_posixsubprocess` bleibt außerhalb dieser Kontrolle;
- darf keine `ctypes`-Audit-Operationen ausführen;
- darf über die überwachten Dateiöffnungen nicht außerhalb des temporären Verzeichnisses
  schreiben; Schreiböffnungen mit nicht nachweisbar sicherem `dir_fd` werden abgelehnt;
- darf keine Dateisystemänderungen über APIs wie rename, remove, link, symlink, chmod
  oder truncate ausführen.

Eine erkannte verbotene Operation ergibt ein signiertes Ergebnis
`FAILED / ISOLATION_VIOLATED`. Ein Abbruch wegen Zeit- oder Prozessressourcenlimit wird
`FAILED / RESOURCE_EXHAUSTED`. Worker-Ausnahmen ergeben `FAILED / WORKER_FAILED`;
der Ausnahmetext überschreitet die Prozessgrenze nicht.

## Nachweise durch Tests

`tests/test_isolation.py` prüft:

- Der deterministische Referenz-Worker liefert innerhalb und außerhalb der Prozessgrenze
  dasselbe signierte Ergebnis.
- Im frischen Worker-Interpreter existiert keine `WorkerAuthority`-Instanz.
- Die Python-Socket-Erzeugung wird abgelehnt.
- Eine überwachte Schreiböffnung außerhalb der Sandbox legt die Zieldatei nicht an.
- Schreiben innerhalb der Sandbox ist erlaubt; das Verzeichnis wird vor der Rückgabe gelöscht.
- Ein Kind darf über die überwachte Python-API keinen weiteren Prozess starten.
- Das Kind darf weder die Elternumgebung durch `/proc` lesen noch seine Ressourcenlimits ersetzen.
- Ein zu lange laufender Worker wird durch die Zeitgrenze des Elternprozesses beendet.
- Die konfigurierten POSIX-Limits sind im Kind sichtbar.
- Ausnahmetexte werden nicht herausgegeben.
- Ungültige Ausgaben durchlaufen weiterhin den Ergebnisvertrag im Elternprozess.

`geniusnew/isolation.py` und `geniusnew/isolation_child.py` stehen in
`scripts/refusals.py`: Wird eine dort erfasste Ablehnung entfernt, muss die Suite
fehlschlagen.

## Bekannte Grenzen (offen gehalten)

Zwei zusätzliche Tests belegen bestehende Lücken. Sie behaupten ausdrücklich, dass
die Lücke vorhanden ist. Wer sie schließt, muss den jeweiligen Test umkehren und
`SECURITY.md` im selben, gesondert freigegebenen PR anpassen.

- **Prozessstart unterhalb des Audit-Hooks:** Der Weg über
  `multiprocessing.util.spawnv_passfds` zu `_posixsubprocess.fork_exec` startet einen
  Prozess, den der Python-Audit-Hook nicht unterbindet. Der Testprozess schreibt eine feste
  Markierung in ein vom Test erzeugtes temporäres Verzeichnis außerhalb des
  Job-Verzeichnisses. Er erbt Ressourcenlimits, aber keine Python-Audit-Sperren.
  Der Worker wartet auf sein Ende; ein erfolgreiches Ergebnis und der tatsächliche
  Dateiinhalt sind beide Teil der Prüfung.
  Test: `test_a_spawn_below_the_audit_hook_escapes_and_this_is_the_boundary`.
- **Lesen außerhalb der Sandbox:** Ein Worker kann Dateien außerhalb von `/proc`,
  `/sys` und `/dev` lesen, soweit der Dienstnutzer darauf zugreifen darf. Der Test
  verwendet ausschließlich eine selbst erzeugte Datei mit einem Kanarienwert und
  prüft dessen Rückgabe im angenommenen Ergebnis.
  Test: `test_a_read_outside_the_temporary_directory_is_allowed_and_this_is_the_boundary`.

Seit Python 3.14 gibt es für `_posixsubprocess.fork_exec` ein internes Audit-Ereignis
([Python-Dokumentation](https://docs.python.org/3.14/library/audit_events.html)).
Der aktuelle Hook lehnt dieses Ereignis nicht ab. Die Grenze hängt deshalb nicht
allein davon ab, ob die Python-Version das Ereignis erzeugt. Die lokalen Tests dieser
Änderung liefen unter Python 3.12; die CI verwendet Python 3.11.

Beide Fälle setzen entsprechenden Worker-Code voraus. Ein Client liefert eine Payload
und wählt den Worker nicht selbst. Das Schließen dieser Grenzen braucht durch das
Betriebssystem erzwungene Kontrollen, etwa seccomp, Landlock oder Namespaces. Eine
zusätzliche Audit-Hook-Regel allein liefert dafür keinen Nachweis; bereits geladene
Module müssen beispielsweise kein neues Import-Ereignis auslösen.

## Bewusst außerhalb des Umfangs

Dies ist eine Prozessgrenze für den Python-Worker von v0.1. Sie ist keine microVM,
keine Container-Sicherheitsgrenze, kein seccomp-Profil und kein Nachweis gegen
feindlichen nativen Code, bereits geladene FFI-Objekte, Kernel-Exploits oder direkte
Syscalls. Die Roadmap hält Firecracker/microVM-Isolation außerhalb von v0.1.

Vor der Ausführung beliebiger nativer Erweiterungen oder nicht vertrauenswürdigen
Drittanbieter-Codes muss eine durch das Betriebssystem erzwungene Sandbox diese
Grenze ersetzen oder ergänzen.
