# Design Gate A2: Der Worker liest keine Host-Dateien

Stand: 28.09.2026, Basis `main` `6d9048ab31a1b1d8dd63e81e596a74db3921449d`. Entwurf von
Claude (Zuständigkeit laut Roadmap v0.2, #59); die Umsetzung ist ein eigener PR.

## Problem

Heute darf ein Worker alles lesen, was der Dienstnutzer lesen darf; gesperrt sind nur
`/proc`, `/sys` und `/dev`, und das nur durch den Python-Audit-Hook. Seine Ausgabe geht an
den Client zurück. Ein Worker kann also das Root-Secret, später die DB-Zugangsdaten und
die Zustandsdatei des Ankers herausgeben. Belegt durch
`tests/test_isolation.py::test_a_read_outside_the_temporary_directory_is_allowed_and_this_is_the_boundary`
(offen gehalten, `SECURITY.md`).

**Mindestziel** (Roadmap): Root-Secret, DB-Zugangsdaten und Ankerzustand sind für den
Worker nicht lesbar. **Volles Ziel:** Der Worker liest nur sein Job-Verzeichnis und die
Python-Laufzeit.

## Vorschlag: Landlock im Kind, ohne Privilegien, ohne neue Abhängigkeit

Landlock ist ein Linux-Sicherheitsmodul, mit dem sich ein Prozess selbst einschränkt. Es
braucht kein Root, keinen eigenen Nutzer und keine Bibliothek: drei Syscalls
(`landlock_create_ruleset`, `landlock_add_rule`, `landlock_restrict_self`) über `ctypes`,
im selben Muster wie der Seccomp-Filter aus #57. Die Einschränkung gilt ab dann für jedes
`open`, lässt sich nicht aufheben und wird an Kindprozesse vererbt.

### Regeln

1. **Alles verbieten, was die Kernel-ABI kennt.** `handled_access_fs` enthält jedes
   Dateisystemrecht der erkannten ABI. Was nicht ausdrücklich erlaubt ist, ist verboten,
   auch Schreiben, Umbenennen und Löschen außerhalb des Job-Verzeichnisses.
2. **Allowlist, sonst nichts:**
   - Job-Verzeichnis: alle Rechte.
   - Nur lesend (`READ_FILE`, `READ_DIR`): `stdlib`, `platstdlib`, `purelib`, `platlib`
     aus `sysconfig` (aufgelöst mit `realpath`), das Paketverzeichnis `geniusnew/` und das
     Verzeichnis des Worker-Moduls, das der Elternprozess aus der Klasse bestimmt.
   - Nicht: Repository-Wurzel, venv-Wurzel, `/etc`, Home-Verzeichnisse.
3. **Reihenfolge im Kind:** Importe → Ressourcenlimits → Landlock → Seccomp (#57) →
   Audit-Hook → Worker auflösen und ausführen. Landlock kommt vor den Hook, weil der
   `ctypes` sperrt, und vor das Auflösen des Workers, damit auch Code beim Import
   eingesperrt ist.
4. **Fail closed an zwei Stellen.** Der Elternprozess fragt beim Bau des
   `IsolatedWorkerRunner` die ABI-Version ab; liegt sie unter dem Minimum, gibt es einen
   `ContractError`, wie bei fehlendem Seccomp. Im Kind bricht jeder fehlgeschlagene
   Landlock-Syscall vor dem Worker-Code ab. Einen Best-Effort-Modus gibt es nicht.
5. **Zwei Schichten, zwei Tests.** Der Audit-Hook prüft Lesezugriffe gegen dieselbe
   Allowlist und meldet `isolation_violated`; Landlock setzt sie im Kernel durch, auch für
   alles, was der Hook nicht sieht. Jede Schicht bekommt einen eigenen Test, der ohne die
   andere läuft. Sonst verdeckt die eine im Refusal-Guard das Fehlen der anderen.

### Mindest-ABI (Entscheidung für Kaan)

| ABI | Kernel | Was zusätzlich gesperrt wird |
| --- | --- | --- |
| 1 | 5.13 | Dateisystem (Mindestziel und volles Ziel) |
| 4 | 6.7 | TCP `bind`/`connect` im Kernel; heute sperrt Netz nur der Hook |
| 6 | 6.12 | abstrakte Unix-Sockets und Signale an Prozesse außerhalb |

Vorschlag: **ABI 4** verlangen, wenn der Server-Kernel das hergibt, sonst ABI 1 und die
Netzsperre bleibt beim Hook. UDP deckt Landlock in keiner ABI ab.

## Prototyp (nicht im Repository)

Lokal am 28.09.2026: Kernel 6.18, Landlock-ABI 7, Python 3.11, venv mit den gepinnten
Abhängigkeiten. Ein Prozess lud `geniusnew.isolation_child`, schränkte sich nach den
Regeln oben ein (mit Netz- und Scope-Rechten) und führte `DeterministicSummarizer` aus.

| Versuch | Ergebnis |
| --- | --- |
| Worker importieren und ausführen, Stdlib nachladen | läuft |
| Secret-Datei (`0600`, Dienstnutzer) außerhalb lesen | `EACCES` |
| `/etc/passwd` lesen, `/home` auflisten | `EACCES` |
| Außerhalb schreiben, in die Stdlib schreiben | `EACCES` |
| Im Job-Verzeichnis schreiben und lesen | erlaubt |
| TCP-Verbindung zu `127.0.0.1` | `EACCES` |

**Befund:** Wird die Sandbox gesetzt, bevor `cryptography` importiert ist, scheitert der
Import an `libgcc_s.so.1` außerhalb der Allowlist. Das echte Kind lädt `cryptography`
schon beim Import von `geniusnew.isolation`, also vor der Sandbox. Ein Worker, der erst
später eine native Erweiterung mit Systembibliotheken lädt, scheitert: fail closed, und
so gewollt. Systembibliotheksverzeichnisse kommen nicht pauschal auf die Allowlist.

## Nachweis für das Gate

- Der Test `…_read_outside_the_temporary_directory_is_allowed_and_this_is_the_boundary`
  wird umgekehrt: Lesen außerhalb wird abgelehnt, der Kanarienwert erreicht den Client
  nicht.
- Neu: Ein Worker liest eine `0600`-Datei des Dienstnutzers (Mindestziel) → abgelehnt.
- Neu: Schreiben außerhalb, Auflisten außerhalb, Schreiben in die Stdlib → abgelehnt;
  Nachladen aus der Stdlib funktioniert weiter.
- Neu: Landlock allein, in einem Unterprozess ohne Hook → Lesen außerhalb `EACCES`.
- Neu: fehlendes Landlock oder zu alte ABI (Syscall injizierbar wie `prctl` in #57) →
  `ContractError` beim Bau des Runners.
- Bei ABI 4: TCP-Verbindung aus dem Worker → abgelehnt.
- Jede neue Ablehnung vom Refusal-Guard erfasst; `SECURITY.md`-Zeile „Ein Worker darf
  lesen …“ wird geändert (Kaans OK).

## Risiken und offene Punkte

- **CI:** Ob die GitHub-Runner (`ubuntu-latest`) Landlock aktiv haben, ist **UNKNOWN**.
  Ohne Landlock lehnt die Isolation ab, und alle Worker-Tests werden rot. Erster Schritt
  der Umsetzung ist deshalb ein Probelauf in der CI, bevor Code auf die Sandbox baut.
- **Reihenfolge:** Die Umsetzung ändert dieselben Dateien wie #57 und beginnt erst, wenn
  #57 gemergt ist. Vorschlag: Codex setzt um (hält #57), Claude reviewt die Sicherheit;
  ist Codex nicht frei, übernimmt Claude. Ein Implementierer pro Branch.
- **Lesbar bleibt**, was auf der Allowlist steht: Stdlib, site-packages, der Quelltext von
  `geniusnew/` und das Verzeichnis des Worker-Moduls. Secrets gehören deshalb nie in
  diese Verzeichnisse, sondern unter einen eigenen Pfad mit `0600` (Betriebsanleitung,
  Gate C5). Im Dienstbetrieb (#61) gibt es nur eingebaute Worker aus `geniusnew/`.
- **Eigener OS-Nutzer für Worker** bleibt als Tiefenverteidigung sinnvoll, braucht aber
  ein Privileg zum Nutzerwechsel. Vorschlag: in C5 (systemd), nicht Teil von A2.
- **Andere Plattformen:** ohne Linux-Landlock keine Ausführung, wie bisher ohne POSIX-
  Limits und ohne Seccomp.

## Entscheidungen für Kaan

1. A2 vor der Beta schließen (Vorschlag: ja; der Prototyp zeigt, dass der Aufwand klein
   ist).
2. Mindest-ABI 4 (Kernel ≥ 6.7) oder 1 (Kernel ≥ 5.13); hängt am Server-Kernel.
3. Eigener OS-Nutzer für Worker in C5 statt in A2.
