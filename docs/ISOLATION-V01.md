# Prozessisolation v0.1

Dieses Dokument beschreibt die Worker-Grenze von GeniusNew und die bekannten Grenzen,
die durch Tests belegt werden.

## Trennung der Rollen

`IsolatedWorkerRunner` hält die `WorkerAuthority` und den Ergebnissignierschlüssel im
Elternprozess. Der Worker startet über **exec in einem frischen Python-Interpreter**;
er erhält keine durch Fork geerbte Kopie des elterlichen Adressraums. Über die Grenze
gehen nur die importierbare Worker-Klassenkennung, ihr Zustand als kanonisches JSON,
Limits und eine Kopie der Payload. Das Kind liefert eine nicht vertrauenswürdige,
größenbegrenzte Nachricht in kanonischem JSON zurück. Der Elternprozess prüft vor dem
Signieren den normalen Ergebnisvertrag.

## Durchgesetzte Kontrollen

Der Python-Worker:

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
  lesbar;
- darf über die überwachten Python-APIs keine Prozesse, Programme oder Shells starten
  und keine fremden Prozesse signalisieren;
- erhält **vor dem Python-Audit-Hook einen Seccomp-Filter**. Der Kernel beendet das Kind
  bei `fork`, `vfork`, `execve`, `execveat`, `clone` ohne `CLONE_THREAD` sowie
  bei einer fremden Syscall-ABI. x32-Syscalls werden ebenfalls abgelehnt.
  `clone3` bekommt `ENOSYS`, weil dessen Flags in einem Speicherbereich liegen, den
  classic BPF nicht lesen kann; libc kann dadurch auf `clone` zurückfallen. Threads
  bleiben erlaubt;
- darf keine `ctypes`-Audit-Operationen ausführen;
- darf über die überwachten Dateiöffnungen nicht außerhalb des temporären Verzeichnisses
  schreiben; Schreiböffnungen mit nicht nachweisbar sicherem `dir_fd` werden abgelehnt;
- darf keine Dateisystemänderungen über APIs wie rename, remove, link, symlink, chmod
  oder truncate ausführen.

Eine erkannte verbotene Operation ergibt ein signiertes Ergebnis
`FAILED / ISOLATION_VIOLATED`. Dasselbe gilt für ein Kind, das der Seccomp-Filter mit
`SIGSYS` beendet. Ein anderer Abbruch durch Zeit- oder Prozessressourcen ergibt
`FAILED / RESOURCE_EXHAUSTED`. Worker-Ausnahmen ergeben `FAILED / WORKER_FAILED`;
der Ausnahmetext überschreitet die Prozessgrenze nicht.

Der Filter wird derzeit nur unter Linux auf x86_64 mit 64-Bit-Interpreter
unterstützt. Überall sonst verweigert `IsolatedWorkerRunner` die Ausführung fail closed,
genau wie bei fehlenden POSIX-Ressourcenlimits.

## Nachweise durch Tests

`tests/test_isolation.py` prüft unter anderem:

- Referenz-Worker und isolierter Worker liefern denselben Vertrag.
- Im Worker existiert keine `WorkerAuthority` des Elternprozesses.
- Python-Socket-Erzeugung und überwachte Schreibzugriffe außerhalb der Sandbox werden
  abgelehnt.
- Prozessstart über die Python-API wird abgelehnt.
- Der zuvor offene Weg über `_posixsubprocess` wird nun vom Kernel beendet, bevor ein
  gestartetes Programm außerhalb des Job-Verzeichnisses schreiben kann.
- Ein Worker darf weiterhin einen Thread starten.
- Die Filterregeln werden zusätzlich mit rohen Syscalls in Wegwerfprozessen geprüft:
  `fork`, `vfork`, `execve`, `execveat`, `clone`, `clone3`, fremde ABI und x32. Ein gewöhnlicher `getpid`-Syscall dient als Kontrolle.
- Das Kind darf weder die Elternumgebung durch `/proc` lesen noch seine Ressourcenlimits
  ersetzen.
- Zeitlimit, Ressourcenlimits, Fehlertext-Redaktion und der Elternprozess-Ergebnisvertrag
  bleiben erhalten.
- Fehlschläge beim Setzen von `no_new_privs` oder Installieren des Filters werden
  fail closed abgelehnt.

`geniusnew/isolation.py` und `geniusnew/isolation_child.py` stehen in
`scripts/refusals.py`: Wird eine dort erfasste Ablehnung entfernt, muss die Suite
fehlschlagen.

## Bekannte Grenze (offen gehalten)

Ein zusätzlicher Test belegt weiterhin eine bestehende Lücke:

- **Lesen außerhalb der Sandbox:** Ein Worker kann Dateien außerhalb von `/proc`,
  `/sys` und `/dev` lesen, soweit der Dienstnutzer darauf zugreifen darf. Seine
  Ausgabe kann zum Client zurückgehen.
  Test:
  `test_a_read_outside_the_temporary_directory_is_allowed_and_this_is_the_boundary`.

Der zuvor offen gehaltene Prozessstart unterhalb des Python-Audit-Hooks wird durch den
Seccomp-Filter geschlossen und durch
`test_a_spawn_below_the_audit_hook_is_killed_by_the_kernel` abgesichert.

Das verbleibende Host-Lesen braucht eine kernel-erzwungene Pfadkontrolle, zum Beispiel
Landlock oder eine entsprechend stärkere Container-/Namespace-Grenze. Eine weitere
Python-Audit-Hook-Regel reicht dafür nicht als Nachweis.

## Bewusst außerhalb des Umfangs

Dies bleibt eine Prozessgrenze, keine microVM und keine vollständige
Container-Sicherheitsgrenze. Seccomp schützt hier gezielt gegen das Starten neuer
Prozesse und Programme. Rohe Netzwerk- und Datei-Syscalls aus bereits geladenem
nativem Code werden dadurch nicht allgemein eingeschränkt; Kernel-Exploits sind
ebenfalls außerhalb dieses Modells.

Vor der Ausführung beliebiger nativer Erweiterungen oder nicht vertrauenswürdigen
Drittanbieter-Codes muss eine stärkere, durch das Betriebssystem erzwungene Sandbox
diese Grenze ergänzen oder ersetzen.
