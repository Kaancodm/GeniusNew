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
- darf nur sein Job-Verzeichnis und, lesend, die Python-Laufzeit, `geniusnew/` und das
  Verzeichnis seines Worker-Moduls erreichen. Das setzt der Kernel mit Landlock durch
  (Gate A2, `docs/ISOLATION-A2.md`); der Audit-Hook prüft dieselbe Allowlist und meldet
  einen Verstoß. `/proc`, `/sys` und `/dev` bleiben gesperrt;
- darf keine TCP-Verbindung aufbauen und keinen TCP-Port binden (Landlock ABI ≥ 4);
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
unterstützt, Landlock ab ABI 4 (Linux 6.7). Überall sonst verweigert
`IsolatedWorkerRunner` die Ausführung fail closed, genau wie bei fehlenden
POSIX-Ressourcenlimits.

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
- Landlock allein, ohne Hook, in einem Wegwerfprozess: Lesen und Auflisten außerhalb,
  Schreiben außerhalb und in erlaubte Leseverzeichnisse sowie TCP-`connect` werden vom
  Kernel abgelehnt; Job-Verzeichnis und Leseverzeichnisse bleiben nutzbar.
- Der Hook allein lehnt dieselben Lesezugriffe ab und markiert den Verstoß.
- Jeder Fehlschlag beim Einrichten von Landlock (ABI unter 4, Ruleset, Regel,
  `no_new_privs`, `restrict_self`) wird fail closed abgelehnt.
- Eine `0600`-Datei des Dienstnutzers außerhalb der Allowlist erreicht die Ausgabe nicht.

`geniusnew/isolation.py` und `geniusnew/isolation_child.py` stehen in
`scripts/refusals.py`: Wird eine dort erfasste Ablehnung entfernt, muss die Suite
fehlschlagen.

## Bekannte Grenze (offen gehalten)

- **Lesbar bleiben Laufzeit, Paket und Worker-Modul:** Ein Secret in einem dieser
  Verzeichnisse würde herausgegeben. Test:
  `test_the_package_source_is_readable_and_this_is_the_boundary`.

Geschlossen und abgesichert sind der Prozessstart unterhalb des Python-Audit-Hooks
(Seccomp, `test_a_spawn_below_the_audit_hook_is_killed_by_the_kernel`) und das Lesen
von Host-Dateien außerhalb der Allowlist (Landlock,
`test_a_service_secret_outside_the_allowlist_cannot_be_read`).

## Bewusst außerhalb des Umfangs

Dies bleibt eine Prozessgrenze, keine microVM und keine vollständige
Container-Sicherheitsgrenze. Seccomp sperrt das Starten neuer Prozesse und Programme,
Landlock Dateizugriffe außerhalb der Allowlist und TCP. UDP und Unix-Sockets aus
bereits geladenem nativem Code schränkt keine der beiden Schichten ein;
Kernel-Exploits sind ebenfalls außerhalb dieses Modells.

Vor der Ausführung beliebiger nativer Erweiterungen oder nicht vertrauenswürdigen
Drittanbieter-Codes muss eine stärkere, durch das Betriebssystem erzwungene Sandbox
diese Grenze ergänzen oder ersetzen.
