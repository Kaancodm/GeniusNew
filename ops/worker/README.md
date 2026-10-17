# A2-Worker-Dienstvorlagen

Die beiden `.in`-Dateien beschreiben den getrennten Worker-Dienst. Sie sind im
Repository weder installiert noch aktiviert. `@CORE_UID@`, `@WORKER_UID@`,
`@WORKER_GID@` und `@SOCKET_GID@` müssen durch feste numerische Identitäten
ersetzt werden. `@PYTHON@` ist der absolute, root-kontrollierte Interpreterpfad
des Core; derselbe Interpreter und eine root-kontrollierte GeniusNew-Installation
sind Voraussetzung für den Bootstrap.

Der Core prüft vor dem Listener und erneut vor jedem Job die geladenen Unit-
Dateien ohne Drop-ins, den installierten Code, den Socket unter
`/run/geniusnew-worker/ipc/worker.sock` und die Lease. Das Verzeichnis `ipc`
muss `root:SOCKET_GID` mit Modus `0710` gehören; Socket und reguläre Lease-Datei
brauchen `root:SOCKET_GID` und `0660`. Der nur durch systemd bereinigte Baum
`/run/geniusnew-worker/jobs` darf nicht von Worker oder Core beschreibbar sein.
Der Core braucht die Socket-Gruppe, aber keine Worker-Gruppe, Capabilities oder
privaten Mount-, PID-, User- oder Cgroup-Namespace.

## Bootgebundener Hostnachweis

Der unprivilegierte Core kann `/proc/1/ns/*` wegen der Linux-Ptrace-Prüfung
nicht lesen. Deshalb muss der bereits vertrauenswürdige **Host-Bootpfad als
Root** vor dem Core-Start eine Referenz unter
`/run/geniusnew-worker/ipc/host-state.json` bereitstellen: reguläre Datei,
`root:SOCKET_GID`, Modus `0440`, genau ein Link, keine Symlinks in der
Verzeichniskette. Core und Worker haben darauf kein Schreibrecht. Es gibt
keine automatische Erzeugung durch den Core und keinen Ersatz bei fehlender
Referenz.

`WorkerLifecycle.host_reference()` liefert die kanonischen Bytes dafür. Die
Methode verlangt Host-Root, die volle UID-/GID-Abbildung, dieselben vier
Namespaces und denselben Prozess-Root wie PID 1 sowie geprüfte Lease-, Runtime-
und Installationspfade. Sie erzeugt selbst keine Datei. Nach Einrichtung der
Lease und des Runtime-Verzeichnisses führt der freigegebene Host-Bootpfad mit
dem root-kontrollierten Interpreter und der installierten Version beispielsweise
folgenden Python-Code aus; die Platzhalter entsprechen denen der Unit-Vorlagen:

```python
import os
from geniusnew.worker_service import WorkerLifecycle, WorkerServiceProfile

profile = WorkerServiceProfile(
    core_uid=@CORE_UID@, worker_uid=@WORKER_UID@,
    worker_gid=@WORKER_GID@, socket_gid=@SOCKET_GID@,
    socket_path="/run/geniusnew-worker/ipc/worker.sock",
    lease_path="/run/geniusnew-worker/ipc/lease",
)
reference = WorkerLifecycle(profile).host_reference()
descriptor = os.open("/run/geniusnew-worker/ipc/host-state.json",
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_CLOEXEC, 0o440)
with os.fdopen(descriptor, "wb") as output:
    os.fchown(output.fileno(), 0, profile.socket_gid)
    os.fchmod(output.fileno(), 0o440)
    output.write(reference)
    output.flush()
    os.fsync(output.fileno())
```

Der Core bleibt während dieser Provisionierung gestoppt. Vorhandene Dateien
werden nicht überschrieben. Eine unvollständige Datei wird vom Core abgelehnt;
ein fehlgeschlagener Boot-Schritt darf den Core nicht starten. Das Beispiel
ist keine Installationsfreigabe und wurde hier nicht als Root ausgeführt.

Die Referenz bindet die Boot-ID, `st_dev`/`st_ino` der Mount-, PID-, User- und
Cgroup-Namespaces sowie die Inodes von `/`, `/proc`, `/sys/fs/cgroup`,
Runtime-Root und Lease. Der Core vergleicht seine eigenen lesbaren Handles und
Pfade damit, nach dem Lock-Erwerb zusätzlich den tatsächlich geöffneten
Lease-FD. Er verlangt weiterhin eine vollständige Procfs-Sicht ohne `hidepid`.
Stale Boot-ID, fremder Namespace, anderes Root/Lease oder unlesbare Zustände
ergeben `ContractError` vor Connect und Payload.

Die Herkunft muss der **echte Host-Installations-/Bootpfad** garantieren:
Container-Root oder ein Marker aus einer privaten Umgebung attestieren den
Host nicht. Root und systemd bleiben dabei dieselben Vertrauensinstanzen wie
für Code, Units und Lease. Bei jedem Boot ist eine neue Referenz erforderlich;
`/run` ist flüchtig. Die Core-Unit darf auch indirekt durch Härtungsoptionen
keine eigenen Namespaces erzeugen. Dieser zusätzliche Betriebsvertrag muss in
Claudes echter UID-/systemd-Probe nachgewiesen werden; die Unit- und Mock-Tests
geben ihn nicht für den Betrieb frei.

Eine Installation und Aktivierung sind Produktionsänderungen und brauchen Kaans
gesondertes GO nach `docs/EXECUTION-GATES.md`. Bis Claudes echte UID-/systemd-
Probe alle Gates besteht und Gemini den exakten Implementierungs-Head geprüft
hat, bleibt die Betriebsabnahme **HOLD**. Die Python-Tests und Unit-Vorlagen
ersetzen diesen Systemnachweis nicht.
