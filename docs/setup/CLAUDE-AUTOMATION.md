# Claude-Hooks: Umfang und Grenzen

Die Dateien unter `.claude/` sind Repository-Artefakte. Ihre Prüfung oder Übernahme
startet weder Claude noch einen API-Aufruf und ersetzt kein vorgeschriebenes Review.

## Tool-Inventar des PreToolUse-Guards

Der Matcher `.*` erfasst jedes Tool. Der Guard erteilt niemals ein explizites `allow`;
ohne Guard-Entscheidung bleiben Claudes übrige Berechtigungen wirksam.

| Tool | Verhalten |
| --- | --- |
| `Read`, `Grep`, `Glob` | Lesende Standard-Tools; keine zusätzliche Schreibfreigabe |
| `Write`, `Edit`, `NotebookEdit` | Pfade prüfen; normale Projektdateien passieren die zusätzliche Prüfung |
| `Bash` | Jeder Befehl braucht mindestens `ask`, auch Interpreter, Shell-Indirektion und Git-Befehle wie `commit`, `reset`, `checkout`, `rebase`, `config`; erkennbare gesperrte Pfade ergeben `deny` |
| Andere Tools, einschließlich `MultiEdit`, `Task`, `Agent` und aller `mcp__…`-Tools | `ask`, weil ihre Schreibwirkung nicht aus dem Namen oder einem Pfadfeld beweisbar ist |

Direkte Schreibpfade unter `.claude/` und `.git/` sowie das Projektverzeichnis selbst
sind im Python-Hook fest mit `deny` geschützt, unabhängig von `critical-paths.txt`.
Dateien mit Secret-Mustern werden auch direkt im Projektstamm abgelehnt. Bei mehreren
Pfaden hat `deny` Vorrang vor `ask`. Pfade werden gegen das tatsächliche Hook-Projekt
und den Aufruf-Arbeitsordner geprüft; symbolische Links werden aufgelöst. Ein Ziel
außerhalb des Projekts benötigt `ask`. Fehlende oder unerwartete Eingaben, eine fehlende
oder ungültige Policy und Laufzeitfehler blockieren mit Exit-Code 2.

Die Sitzung bleibt im Worktree, zu dem die Hook-Dateien gehören. Ein `cwd` außerhalb
dieses Projekts oder unter `.claude/worktrees/` wird mit Exit-Code 2 blockiert, damit
ein Wechsel des Arbeitsordners geschützte Dateien nicht auf `ask` herabstuft. Für einen
anderen verknüpften Worktree wird eine separate Sitzung mit dessen eigenen Hooks und
passendem `CLAUDE_PROJECT_DIR` gestartet. Automatischer Worktree-Wechsel innerhalb
derselben Sitzung ist hier nicht freigegeben.

Ein interner POSIX-Watchdog beendet blockiertes Einlesen nach fünf Sekunden mit
Exit-Code 2, vor dem konfigurierten äußeren Timeout von zehn Sekunden. Fehlt die
Watchdog-Unterstützung, blockiert der Hook. Getestet wird direkt als Python-Subprozess,
ohne die Hooks in der laufenden Agent-Sitzung zu aktivieren:

```sh
python3 -W error::ResourceWarning -m unittest tests/test_claude_protect_critical.py -v
```

## Sicherheitsgrenze

Dieser Hook ist eine zusätzliche Freigabeprüfung, keine Shell- oder Dateisystem-Sandbox.
Er prüft Schreibpfade und Ausführungen. `Read`, `Grep` und `Glob` behalten die normalen
Client-Berechtigungen; der Hook filtert keine Leseinhalte und verhindert allein keinen
Secret-Import in den Modellkontext. Das Quellenpaket eines Reviews enthält deshalb nur
die ausdrücklich ausgewählten Repository-Dateien, niemals Credentials oder private Daten.
Ein freigegebener Shell-/MCP-Aufruf kann Dateien verändern, einschließlich der Hooks;
die Freigabe muss deshalb den ganzen Aufruf berücksichtigen. Es gibt keine automatische
Freigabe anhand von Shell-Text oder Regex-Heuristiken. Die bestehende Prüfung auf
wörtlich enthaltene gesperrte Pfade bleibt als zusätzliche Ablehnung erhalten; sie
beweist keine Abwesenheit weiterer Dateizugriffe. Eine Dateisystemänderung zwischen
Pfadprüfung und Tool-Ausführung kann ein Hook allein nicht verhindern. Auch der Ausfall
des Interpreters vor Hook-Start, `SIGKILL`, eine deaktivierte Hook-Konfiguration oder
unterschiedliche Tool-/Hook-Semantik des Clients liegen außerhalb seines Nachweises.
Die tatsächliche Integration in eine konkrete Claude-Code-Version braucht eine eigene
Prüfung. OS-Isolation und die bestehenden Repository-/Security-Gates bleiben erforderlich.
