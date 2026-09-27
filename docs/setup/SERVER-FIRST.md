# Server-first Betrieb

GeniusNew wird server-first betrieben: Entwicklungs- und Betriebswerkzeuge laufen auf
einem Linux-Server; iPad, iPhone und Laptop dienen als Bedienoberflächen. Modellanbieter
bleiben externe Cloud-Dienste, aber deren CLI, Arbeitsverzeichnis und Befehle laufen auf
dem Server.

## Gate 0 — nur lesen, nichts installieren

Vor jeder Installation wird der Serverzustand mit `ops/server/audit.sh` erfasst. Das
Skript verändert keine Pakete, Dienste, Netzwerkregeln, Repository-Dateien oder
Zugangsdaten. Es gibt keine Tokens, Schlüssel, Passwörter, Remote-URLs oder privaten
Konfigurationswerte aus.

```sh
cd ~/GeniusNew
bash ops/server/audit.sh
```

Erwartet werden mindestens die Statuszeilen für:

- Linux-/Debian-Version, CPU, RAM und freien Speicher
- Git, Python, tmux, Node/npm
- Docker und Tailscale
- Claude Code, Codex CLI und Gemini CLI
- systemd-Status von Docker, Tailscale und SSH
- GeniusNew-Branch, Head-SHA und Anzahl lokaler Änderungen

Nicht prüfbare Punkte bleiben `MISSING`, `inactive` oder `UNKNOWN`; sie werden nicht
still als vorhanden angenommen.

## Ziel nach Gate 0

Auf dem Server sollen später mindestens diese Werkzeuge vorhanden sein:

- Git + Python venv
- tmux
- Docker Engine + Compose Plugin
- Tailscale
- Claude Code
- OpenAI Codex CLI
- Google Gemini CLI
- GeniusNew-Arbeitskopie unter `~/GeniusNew`

PostgreSQL, Reverse Proxy, TLS, systemd-Units für GeniusNew und produktive Secrets werden
**nicht** in Gate 0 eingerichtet. Sie folgen erst nach dem passenden Datenbank-/Deploy-
Design und den dafür vorgeschriebenen Reviews.

## Verifizierte Installationsquellen (27.09.2026)

Die späteren Installationsschritte müssen sich an den offiziellen Quellen orientieren:

- Docker Engine: offizielle Docker-APT-Pakete für Debian 13 (Trixie).
- Tailscale: offizielle Linux-/Debian-Pakete; Authentisierung erst nach Installation.
- Claude Code: nativer Linux-Installer von Anthropic; Installation als normaler Nutzer.
- Codex CLI: `npm install -g @openai/codex`; Anmeldung mit `codex --login` möglich.
- Gemini CLI: `npm install -g @google/gemini-cli`; benötigt Node.js 20 oder neuer.

Keine Zugangsdaten oder Auth-Tokens werden in dieses Repository geschrieben.

## Bedienung später

Für die tägliche Arbeit reicht eine tmux-Sitzung auf dem Server. Eine sinnvolle
Fensteraufteilung ist:

```text
genius
├── shell
├── claude
├── codex
├── gemini
└── ops
```

Der genaue Start dieser Fenster wird erst festgelegt, wenn Gate 0 belegt hat, welche
Werkzeuge bereits installiert sind.
