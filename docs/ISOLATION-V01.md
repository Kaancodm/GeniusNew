# Prozessisolation v0.1

Dieses Dokument beschreibt genau, was die Worker-Grenze aus Schritt 11 erzwingt.

## Vertrauensgrenze

**IsolatedWorkerRunner** behält **WorkerAuthority** und den privaten
Ergebnis-Signierschlüssel im Elternprozess. Der Worker startet per **exec in einem
frischen Python-Interpreter**; er erbt keine Kopie des Adressraums. Nur die Kennung
einer importierbaren Worker-Klasse, kanonischer JSON-Instanzzustand, Limits und eine
Kopie der Nutzlast überschreiten diese Grenze. Die Antwort des Kindes ist eine nicht
vertrauenswürdige, größenbegrenzte kanonische JSON-Nachricht. Erst der Elternprozess
prüft den normalen Ergebnisvertrag und signiert.

## Erzwungene Grenze

Für den Python-Worker-Pfad v0.1 gilt:

- Frischer Interpreter; geerbte Dateideskriptoren sind geschlossen, stdin ist
  isoliert und stdout/stderr sind vom Worker-Protokoll getrennt.
- Jeder Job erhält ein frisches temporäres Verzeichnis.
- Der Elternprozess erzwingt die maximale Laufzeit.
- POSIX-Limits begrenzen CPU-Zeit, Adressraum, Größe einer einzelnen Datei, offene
  Deskriptoren und Core-Dumps.
- Höchstens acht unterschiedliche Dateien dürfen standardmäßig angelegt werden. Mit
  dem Einzellimit von 1 MiB ergibt das zusätzlich eine logische Job-Obergrenze von
  8 MiB; beide Werte sind eng begrenzt konfigurierbar.
- Die Umgebung wird durch Werte ersetzt, die auf das Job-Verzeichnis zeigen.
- Python-Socketzugriffe, neue Prozesse, exec, Shell-Starts, fremde Signale und
  **ctypes**-Auditereignisse werden verweigert.
- Lesen ist nur im Job-Verzeichnis sowie für Python-Laufzeitcode (.py, .pyc und
  native Importmodule) unter den beim Start festgelegten Importwurzeln erlaubt.
  Andere Hostdateien, Dateideskriptor-Aliase und Datenressourcen werden verweigert.
- Schreibzugriffe außerhalb des Job-Verzeichnisses sowie Low-Level-Write-Opens, deren
  **dir_fd** nicht sicher geprüft werden kann, werden verweigert.
- Dateisystemmutationen wie rename, remove, link, symlink, chmod und truncate werden
  verweigert.

Eine verbotene Operation wird zu einem signierten
**FAILED / ISOLATION_VIOLATED**-Ergebnis. Laufzeit- oder Ressourcenabbrüche werden zu
**FAILED / RESOURCE_EXHAUSTED**. Worker-Ausnahmen werden zu
**FAILED / WORKER_FAILED**; ihr Text verlässt den Kindprozess nicht.

## Tests sind der Nachweis

**tests/test_isolation.py** prüft unter anderem:

- Der deterministische Referenz-Worker liefert innerhalb und außerhalb der
  Prozessgrenze dasselbe signierte Ergebnis.
- Im Kindprozess existiert keine **WorkerAuthority**.
- Netzwerkzugriff, Prozessstart, **ctypes**, Ressourcenerhöhung und Schreiben außerhalb
  der Sandbox werden verweigert.
- Eine Hostdatei außerhalb der Sandbox kann nicht gelesen oder über das signierte
  Ergebnis herausgegeben werden; ein normaler Python-Import bleibt funktionsfähig.
- Schreiben innerhalb der Sandbox funktioniert, das Verzeichnis wird anschließend
  entfernt und die Zahl unterschiedlicher Dateien ist begrenzt.
- Überlange Worker werden beendet; Ausnahmeinformationen lecken nicht und fehlerhafte
  Ausgaben durchlaufen weiterhin den Ergebnisvertrag des Elternprozesses.

**geniusnew/isolation.py** und **geniusnew/isolation_child.py** sind außerdem durch
**scripts/refusals.py** geschützt. Das Entfernen einer erkannten
Sicherheitsablehnung muss die CI fehlschlagen lassen.

## Bewusste Nicht-Ziele

Dies ist Prozessisolation für den Python-Pfad v0.1, keine microVM, kein
Container-Sicherheitsnachweis, kein seccomp-Profil und kein Schutzbeweis gegen
bösartigen nativen Code, vorgeladene FFI-Objekte, Kernel-Exploits oder direkte
Syscalls. Die Roadmap hält Firecracker/microVM-Isolation ausdrücklich außerhalb von
v0.1.

Vor der Ausführung beliebiger nativer Erweiterungen oder fremden Drittanbieter-Codes
muss diese Grenze durch eine vom Betriebssystem erzwungene Sandbox ersetzt oder
umschlossen werden.
