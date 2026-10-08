# GeniusNew: Entwicklungsumgebung
Stand: 08.10.2026. Dieser Entwurf ergänzt den bestehenden Regel-PR #134.
Basis: main 62b6d1966da5e30fe71f230629a48dea369ae62c.
Alle Aussagen „geprüft“ beziehen sich auf die hier genannten Prüfungen, nicht auf frühere Chats.

## Arbeitsteilung und Anschlüsse
| Werkzeug | Aufgabe | Nachgewiesener Stand | Noch nötig |
| --- | --- | --- | --- |
| Debian-Server | Repo, Tests, KI-Sitzungen, lokale KI | Desktop Commander erreichbar; 8 CPUs, 16 GB RAM | Regel-/Starter-PR prüfen |
| Tailscale | Privater Gerätezugang | Server läuft; Laptop und iPhone im Status online | iPad verbinden, A53 im selben Tailnet anmelden |
| Lokale KI | Kleine Texte, Codeentwürfe, Offline-Ersatz | Ollama 0.40.1; qwen2.5-coder:3b; echte Antwort LOCAL_AI_OK | Qualität je Aufgabe separat prüfen |
| ChatGPT / Codex | Planung und Umsetzung | Codex 0.157.1; Anmeldung erkannt | Kontingent und reale Projektaufträge separat prüfen |
| Claude | Doku und unabhängiger Review | Claude Code 2.1.287; Anmeldung erkannt | Kontingent separat prüfen |
| Gemini A | Review | Gemini CLI 0.61.0; bestehende OAuth-Datei | Pro-Berechtigung/Modell und Kontingent prüfen |
| Gemini B | Recherche, zweite lizenzierte Sitzung | Eigenes Profil vorbereitet; keine Credentials kopiert | Zweites Google-Konto persönlich anmelden |
| Cursor | Editor/Agent für Entwicklung | Server-Agent vorhanden und Anmeldung erkannt | Laptop-Verbindung separat prüfen |
| Warp | Terminal und Agent | CLI v0.2026.09.30.08.29.stable_01 installiert; --version/--help PASS | Eigene Warp-Anmeldung |
| Gumloop Pro | Cloud-Koordination und Trigger | Abo von Kaan genannt; Zugang hier nicht geprüft | Konto verbinden, einen Test-Agenten und Test-Trigger prüfen |
| HARPA | Browser-Aufgaben | API-/Browser-Mechanismus in offizieller Doku bestätigt | Desktop-Browser und aktueller Schlüssel, ein harmloser Test |
| Framer | Landingpage und CMS | Server API offiziell vorhanden | Projekt wählen und Projektzugang prüfen |
| Replit | Prototypen/Cloud-Arbeitsbranch | GitHub-Integration offiziell dokumentiert | GitHub mit Replit verbinden, Arbeitsbranch wählen |
| Microsoft 365 Premium | Dokumente, Tabellen, Copilot | Abo von Kaan genannt | Konto/OneDrive verbinden; Agent-Funktionen je Konto prüfen |
| CodeRabbit | PR-Review | In bestehendem PR #134 aktiv | Neuen PR prüfen lassen |

Die Abo-Existenz bestätigt keinen API-Zugang, kein Kontingent und keine Verbindung.
Gemini CLI nutzt den offiziellen Google-Login; kein OAuth-Token wird für einen Drittanbieter
exportiert. Gemini API und deren Abrechnung sind gesondert zu prüfen.
Gumloop erhält zunächst GitHub-/Connector-basierte Aufgaben. Ein Cloud-Dienst erreicht
die private Tailscale-Adresse nicht allein durch eine Webhook-URL.
Produktiv-Publishing von Framer/Replit ist ein eigener Freigabeschritt.

## Serverbefehle
Diese Befehle auf dem Debian-Server im Terminal/Termius ausführen.
Der Server bleibt der Arbeitsort; das iPad ist Steuergerät, der Laptop ist optionaler Editor.

~~~bash
genius-workflow status
genius-workflow ai "Erkläre kurz, was ein Git-Branch ist."
genius-workflow new meine-aufgabe
~~~

Der letzte Befehl gibt den vollständigen Worktree-Pfad aus. Beispiel:

~~~bash
genius-workflow start codex /home/kaan/tasks/geniusnew/workflow/meine-aufgabe
genius-workflow attach codex /home/kaan/tasks/geniusnew/workflow/meine-aufgabe
~~~

Claude startet mit automatischer Annahme von Dateibearbeitungen. Cursor verwendet
Auto-review; Codex kann im Arbeitsverzeichnis schreiben. Routinearbeit hat eine
dauerhafte Aufgabenfreigabe; Produktion/Zugänge/Secrets/main brauchen gezieltes GO.
Der alte Befehl genius-dev wird nicht überschrieben.

Review oder zweites Gemini-Konto:

~~~bash
genius-workflow start claude /home/kaan/tasks/geniusnew/workflow/meine-aufgabe --review
genius-workflow attach claude /home/kaan/tasks/geniusnew/workflow/meine-aufgabe --review
genius-workflow start gemini-b /home/kaan/tasks/geniusnew/workflow/meine-aufgabe
genius-workflow attach gemini-b /home/kaan/tasks/geniusnew/workflow/meine-aufgabe
~~~

Gemini A verwendet das vorhandene Profil. B verwendet GEMINI_CLI_HOME unter
~/.config/genius-workflow/gemini-b und benötigt die persönliche Google-Anmeldung.
Ein Terminal-Auftrag erzeugt keine automatische Freigabe von Websites/Produktionsdaten.
Es läuft jeweils ein Implementierer pro Worktree; Reviews können parallel lesen.

## Lokale KI
Das Modell wird auf CPUs ausgeführt. Eine GPU wurde auf diesem Server nicht nachgewiesen.
Die kleine KI ist ein Ersatz für einfache Entwürfe, kein gleichwertiger Ersatz für
Codex/Claude oder ein unabhängiger Sicherheitsprüfer.

- API: nur 127.0.0.1:11434.
- Modell: qwen2.5-coder:3b.
- Limits: 4 CPU-Kerne, 6 GB RAM, 2048 Token Kontext, ein geladenes Modell.
- Benutzerdienst: genius-local-ai.service; Autostart für Benutzer kaan.
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

## Installationsnachweis
Ollama-Archiv: v0.40.1, SHA-256 a7aebbe3dd76ccf1351a56a3e57218ad4863cb5f9a9938c58de87a37555e355d, vor Ausführung geprüft.
Modell-Digest: f72c60cabf6237b07f6e632b2c48d533cef25eda2efbd34bed21c5e9c01e6225.
Echte Testantwort LOCAL_AI_OK in 8,1 Sekunden; kein Qualitätsbenchmark.
Warp: offizieller Benutzer-Installer geprüft; installierte CLI-Version v0.2026.09.30.08.29.stable_01.
