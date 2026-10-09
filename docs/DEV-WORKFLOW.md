# GeniusNew: Entwicklungsumgebung
Stand: 08.10.2026. Dieser Entwicklungsstarter ist unabhängig vom offenen Regelentwurf
#134; dessen Sicherheitslockerungen widersprechen dem aktuellen PR-Cleanup-Auftrag
und gelten hier nicht.
Basis: main 62b6d1966da5e30fe71f230629a48dea369ae62c.
Alle Aussagen „geprüft“ beziehen sich auf die hier genannten Prüfungen, nicht auf frühere Chats.

## Arbeitsteilung und Anschlüsse

Die öffentliche Anleitung beschreibt das Verfahren. Rechnerinventar, Gerätezugänge,
Anmeldestatus, persönliche Pfade und tatsächlich installierte CLI-Versionen gehören
in private Betriebsnachweise und werden hier nicht veröffentlicht.

| Werkzeug | Aufgabe | Nachweis vor Verwendung |
| --- | --- | --- |
| Entwicklungsrechner / Tailscale | Repo, Tests, getrennte KI-Sitzungen, privater Zugang | Berechtigung und Erreichbarkeit privat prüfen |
| Lokale KI | Einfache Vorprüfungen und Entwürfe | Echte Antwort und Qualität für die konkrete Aufgabe prüfen |
| Codex | Verantwortlicher Implementierer | Authentifizierung, Kontingent und isolierten Arbeitsbranch prüfen |
| Claude | Unabhängiger Security-/DB-Review | Berechtigung und Kontingent prüfen; keine eigene Arbeit freigeben |
| Gemini A / B | Unabhängige Vorprüfung, getrennte lizenzierte Profile | Persönliche Anmeldung, Pro-Modell und Kontingent je Profil prüfen |
| Cursor / Warp | Optionaler Editor bzw. Terminal-Agent | Kompatible CLI und persönliche Berechtigung separat prüfen |
| CodeRabbit | PR-Review | Tatsächlichen Review am relevanten Commit prüfen |
| Gumloop / HARPA | Optionale Cloud-Koordination bzw. Browser-Aufgaben | Konkreten Auftrag, Verbindung und Berechtigung prüfen |
| Framer / Replit / Microsoft 365 | Optionale weitere Werkzeuge | Zugang und konkreten Auftrag separat prüfen |

Die Abo-Existenz bestätigt keinen API-Zugang, kein Kontingent und keine Verbindung.
Gemini CLI nutzt den offiziellen Google-Login; kein OAuth-Token wird für einen Drittanbieter
exportiert. Gemini API und deren Abrechnung sind gesondert zu prüfen.
Gumloop erhält zunächst GitHub-/Connector-basierte Aufgaben. Ein Cloud-Dienst erreicht
die private Tailscale-Adresse nicht allein durch eine Webhook-URL.
Produktiv-Publishing von Framer/Replit ist ein eigener Freigabeschritt.

## Serverbefehle
Diese Befehle auf dem Debian-Server im Terminal/Termius ausführen.
Der autorisierte Entwicklungsrechner bleibt der Arbeitsort; andere Geräte können ihn steuern.

~~~bash
genius-workflow status
genius-workflow ai "Erkläre kurz, was ein Git-Branch ist."
genius-workflow new meine-aufgabe
~~~

Der letzte Befehl gibt den vollständigen Worktree-Pfad aus. Beispiel:

~~~bash
genius-workflow start codex "$HOME/tasks/geniusnew/workflow/meine-aufgabe"
genius-workflow attach codex "$HOME/tasks/geniusnew/workflow/meine-aufgabe"
~~~

Claude startet mit automatischer Annahme von Dateibearbeitungen. Cursor verwendet
Auto-review; Codex kann im Arbeitsverzeichnis schreiben. Routinearbeit hat eine
dauerhafte Aufgabenfreigabe; Produktion/Zugänge/Secrets/main brauchen gezieltes GO.
Der alte Befehl genius-dev wird nicht überschrieben.

Review oder zweites Gemini-Konto:

~~~bash
genius-workflow start claude "$HOME/tasks/geniusnew/workflow/meine-aufgabe" --review
genius-workflow attach claude "$HOME/tasks/geniusnew/workflow/meine-aufgabe" --review
genius-workflow start gemini-b "$HOME/tasks/geniusnew/workflow/meine-aufgabe"
genius-workflow attach gemini-b "$HOME/tasks/geniusnew/workflow/meine-aufgabe"
~~~

Gemini A verwendet das vorhandene Profil. B verwendet GEMINI_CLI_HOME unter
~/.config/genius-workflow/gemini-b und benötigt die persönliche Google-Anmeldung.
Ein Terminal-Auftrag erzeugt keine automatische Freigabe von Websites/Produktionsdaten.
Jede Starter-Sitzung hält den Worktree exklusiv, auch im Review-/Plan-Modus, weil
CLI-Modi wechseln können. Der Starter behauptet keine unveränderliche Read-only-
Sandbox für Claude/Gemini; unabhängige Reviews brauchen einen separat verifizierten
Read-only-Checkout oder ein exaktes Quellenpaket. Parallele Sitzungen auf demselben
Worktree werden abgelehnt. Der gemeinsame Checkout ist kein Task-Worktree.
Der Starter prüft alle effektiven Fetch- und Push-URLs inklusive Git-Rewrites und
akzeptiert für `origin` ausschließlich `Kaancodm/GeniusNew` auf
`github.com` über HTTPS, `ssh://git@github.com/` oder `git@github.com:`.

## Lokale KI
Das referenzierte Modell kann auf CPUs ausgeführt werden. Die tatsächliche Ausstattung wird privat geprüft.
Die kleine KI ist ein Ersatz für einfache Entwürfe, kein gleichwertiger Ersatz für
Codex/Claude oder ein unabhängiger Sicherheitsprüfer.

- API: nur 127.0.0.1:11434.
- Modell: qwen2.5-coder:3b.
- Limits: 4 CPU-Kerne, 6 GB RAM, 2048 Token Kontext, ein geladenes Modell.
- Benutzerdienst: genius-local-ai.service; Benutzerdienst-Vorlage; Aktivierung braucht eine eigene Freigabe.
- Kein zusätzlicher Cloud-Aufruf für lokale Modellantworten.

Auf dem Server:

~~~bash
systemctl --user status genius-local-ai.service --no-pager
systemctl --user stop genius-local-ai.service
systemctl --user start genius-local-ai.service
~~~

Der bestehende Tailscale-Serve-Endpunkt auf / wird nicht überschrieben.
Ein Chat-UI für Mobilgeräte wäre ein zusätzlicher geprüfter Schritt.

## Prüfgate

Vorbereitete Gumloop-Anweisung: [gumloop-dev-agent.md](examples/gumloop-dev-agent.md).
Testübergabe: [dev-task.json](examples/dev-task.json).
Benutzerdienst: [genius-local-ai.service](examples/genius-local-ai.service).

Syntax, echte Git-Branch-Sperren, Starterargumente, Linkprüfung und git diff --check
werden vor dem Draft-PR geprüft. Die KI muss eine echte Antwort liefern; Installation
oder Prozessstatus allein reicht nicht. Neue Checks ersetzen keine Core-Tests.
Nicht verbundene Cloud-Konten werden ausdrücklich als offen geführt.

## Rückweg
Der neue Worktree und der neue Starter sind getrennt von bestehenden Checkouts.
Den neuen Benutzerdienst stoppen/deaktivieren und den neuen Starter nicht mehr verwenden.
Bestehende genius-dev-Sitzungen, SSH, Firewall, Secrets und produktive DB bleiben unberührt.
Keine bestehenden Tools oder Daten wurden gelöscht.

## Offizielle Quellen
- [Tailscale CLI](https://tailscale.com/docs/reference/tailscale-cli)
- [Ollama Linux](https://docs.ollama.com/linux)
- [Modell](https://ollama.com/library/qwen2.5-coder:3b)
- [Gemini Anmeldung](https://geminicli.com/docs/get-started/authentication/)
- [Gemini Profile](https://geminicli.com/docs/reference/configuration/)
- [Codex-Konfiguration](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Claude Berechtigungen](https://code.claude.com/docs/en/permissions)
- [Warp Agent](https://docs.warp.dev/agents/)
- [Gumloop](https://docs.gumloop.com/)
- [HARPA Browser-Node](https://harpa.ai/grid/browser-automation-node-setup)
- [Framer Server API](https://www.framer.com/developers/server-api-introduction)
- [Replit GitHub](https://docs.replit.com/replit-workspace/workspace-features/version-control)
- [Microsoft Agents](https://support.microsoft.com/en-us/microsoft-365-copilot/get-started-with-agents-in-the-microsoft-365-copilot-app)

## Referenzierte Installationsartefakte
Diese Angaben identifizieren Artefakte und sind kein Nachweis einer aktuellen Installation.
Ollama-Archiv: v0.40.1, SHA-256 a7aebbe3dd76ccf1351a56a3e57218ad4863cb5f9a9938c58de87a37555e355d.
Modell-Digest: f72c60cabf6237b07f6e632b2c48d533cef25eda2efbd34bed21c5e9c01e6225.
Installer-Integrität, Kompatibilität und tatsächliche CLI-Version werden vor Verwendung separat geprüft.
