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
privaten Mount-, PID- oder User-Namespace.

Eine Installation und Aktivierung sind Produktionsänderungen und brauchen Kaans
gesondertes GO nach `docs/EXECUTION-GATES.md`. Bis Claudes echte UID-/systemd-
Probe alle Gates besteht und Gemini den exakten Implementierungs-Head geprüft
hat, bleibt die Betriebsabnahme **HOLD**. Die Python-Tests und Unit-Vorlagen
ersetzen diesen Systemnachweis nicht.
