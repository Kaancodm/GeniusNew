# Mitarbeit mit iPad Pro, Laptop und KI-Werkzeugen

GitHub ist die verbindliche Quelle für Code, Aufgaben, PRs und CI. Das iPad Pro
übernimmt KI-Gespräche, Recherche, Projektunterlagen und Reviews. Codex, Codebearbeitung
und lokale Tests laufen auf dem Laptop unter Linux/WSL. Dieses Dokument richtet
keine Konten ein und ersetzt keine Prüfung des jeweiligen Zugangs.

## Geräte und Entlastung

| Gerät | Arbeit | Voraussetzung |
| --- | --- | --- |
| iPad Pro | ChatGPT, Gemini, NotebookLM, GitHub und OneDrive | Bestehende Projekte mit denselben Konten öffnen |
| Laptop | Codex, Git-Checkout, WSL und lokale Prüfungen | Ein aktiver Implementierer je Branch; Tests nacheinander |

Die Entlastung entsteht durch das Auslagern der Apps und Browserarbeit auf das iPad.
Eine SSH-Verbindung führt Befehle weiterhin auf dem Laptop aus. Auf dem iPad die
vorhandenen Projektlinks als Safari-Lesezeichen speichern; OneDrive enthält das
private Startblatt und die Verbindungsanleitung.

Vor dem Schließen von Laptop-Anwendungen die Browser-Sitzungen sichern und laufende
Agenten berücksichtigen. Erst wenn die entsprechenden Zugänge auf dem iPad geprüft
sind, ungenutzte KI-Apps und Browser-Tabs schließen. Codex, WSL und ein Browser für
die gerade benötigte Entwicklungsarbeit bleiben verfügbar. Keine automatische
Beendigung fremder Sitzungen oder pauschale Änderung von WSL-Speicherlimits.

Vorher und nachher dieselbe typische Arbeit jeweils fünf Minuten messen:
verfügbaren RAM und CPU-Auslastung festhalten. Angestrebt sind mindestens 500 MB
mehr verfügbarer RAM ohne dauerhaft höhere CPU-Last; ohne Vergleichsmessung ist die
Entlastung unbestätigt. Einrichtungsstand und Messwerte gehören in die private
Arbeitsablage, nicht in das öffentliche Repository.

## Rollen

| Werkzeug | Aufgabe | Übergabe |
| --- | --- | --- |
| GitHub | Code, Aufgaben, PRs, Checks und Freigaben | Link und voller Commit-SHA |
| ChatGPT / Codex | Planung, begrenzte Umsetzung und lokale Prüfung | Diff, Befehle, Ergebnisse und offene Punkte |
| GitHub Copilot | Begrenzte Implementierungsaufträge in einem eigenen Branch | Draft PR mit prüfbarer Abnahme |
| Gemini | Zweite technische Einschätzung | Befunde mit Datei, Zeile, Reproduktion und Priorität |
| NotebookLM / Gemini Notebook | Fragen anhand ausgewählter Projektquellen | Quellenbezug und Stand der Quelle |
| OneDrive / OneNote | Eigene Arbeitsunterlagen und Entscheidungsnotizen | Verweise auf GitHub; keine zweite Codebasis |

Ein Modellreview ist eine zusätzliche Einschätzung. Sicherheitsbefunde werden am
exakten Diff und mit geeigneten Tests geprüft. Der Implementierer ist bei
sicherheitskritischen Änderungen nicht der alleinige abschließende Prüfer.

## Ein Arbeitsgang

1. Auf GitHub aktuellen `main`-SHA, offene PRs, CI und Abhängigkeiten prüfen.
2. Eine kleine Aufgabe mit Ziel, Baseline, Scope und Abnahme beschreiben. Dafür
   gibt es das Issue-Formular **Bounded collaboration task**.
3. Genau einen Implementierer für denselben Branch einsetzen. Andere Werkzeuge
   erhalten eine Lesekopie oder einen eigenen Branch mit klarem Auftrag.
4. Lokale Checks gemäß `AGENTS.md` durchführen und einen Draft PR liefern.
5. Den aktuellen PR-Head zur unabhängigen Prüfung übergeben. Änderungen nach dem
   Review benötigen neue, zur Änderung passende Evidenz.
6. Der Projektverantwortliche entscheidet über Merge, Release und Deployment.
   Danach Quellenpakete und Entscheidungsnotizen aktualisieren.

### Gemeinsames Übergabeformat

```text
Repository: Kaancodm/GeniusNew
Aufgabe und gewünschtes Ergebnis:
Baseline-SHA:
Branch / PR / aktueller Head-SHA:
Erlaubter Scope und Abhängigkeiten:
Änderung:
Prüfbefehle und tatsächlich beobachtete Ergebnisse:
CI-Link und zugehöriger SHA:
Review-Befunde und verbleibende Grenzen:
Nächste konkrete Entscheidung:
```

Vor einer Umsetzung Remote, Branch, Arbeitsbaum und aktuellen GitHub-Stand prüfen.
Bei einem reinen Quellenexport ohne Repository-Zugriff nur analysieren und einen
Auftrag vorbereiten; Umsetzung, CI und Freigaben als `UNKNOWN` behandeln. Ein datierter
Statusbericht allein belegt keinen aktuellen Branch. Der gleiche Auftrag wird nicht
gleichzeitig von mehreren Werkzeugen im selben Checkout bearbeitet.

## Microsoft 365 Personal/Family

Ein eigener OneDrive-Ordner `GeniusNew` kann Startblatt, Projektstand, Prompts,
Quellenexporte und Entscheidungen enthalten. Die laufende Git-Arbeitskopie liegt
außerhalb des synchronisierten Ordners. So konkurriert Dateisynchronisation nicht
mit Git, temporären Dateien und anderen Agenten.

In OneNote ein Notizbuch `GeniusNew` auf dem eigenen OneDrive anlegen. Sinnvolle
Abschnitte sind `Start`, `Entscheidungen`, `Reviews` und `Ideen`. Aufgabenstatus und
Code-Freigaben werden aus GitHub verlinkt. Die Erstellung eines lokalen Ordners
beweist weder die Cloud-Synchronisation noch die Anmeldung auf dem iPad.

## ChatGPT und Codex

Ein ChatGPT-Projekt bündelt Anweisungen, Quellen und Gespräche. Die Dateien aus
einem lokalen Verzeichnis sind dort erst nach Upload oder unterstützter Verbindung
verfügbar. Eine erfolgreiche Codex-CLI-Anmeldung bestätigt nicht automatisch eine
eingerichtete Codex-Cloud-Umgebung.

Für Codex Cloud das Repository gezielt auswählen und die Abhängigkeiten mit
`python3 -m pip install --require-hashes -r requirements.txt` installieren. Keine
produktiven Secrets für die Demo hinterlegen. Unter WSL Codex im richtigen
GeniusNew-Checkout starten und den Branch vor Änderungen prüfen.

## Copilot

Die Vorlage [`setup/copilot-setup-steps.yml`](setup/copilot-setup-steps.yml) bereitet
Python 3.11 und die gepinnten Abhängigkeiten für Copilot vor. Zur Aktivierung muss
sie als `.github/workflows/copilot-setup-steps.yml` auf den Default-Branch übernommen
werden. Dafür braucht der ausführende GitHub-Zugang Workflow-Schreibrechte. Die
Vorlage unter `docs/setup` ist noch kein aktiver Workflow. Die vorhandene
Contract-CI bleibt der maßgebliche Testpfad.

GitHub Mobile unterstützt das Starten und Verfolgen von Copilot-Cloud-Aufträgen
für unterstützte bezahlte Copilot-Pläne. Die tatsächliche Lizenz und die Freigabe
des Repositories müssen im verwendeten Konto geprüft werden. Ein Auftrag soll
explizit einen Draft PR als Ergebnis nennen. Automatisches Beauftragen ist nicht
Teil dieses Setups.

## Gemini und NotebookLM

Quellenpakete erhalten Datum, vollständigen Commit-SHA und Original-Links. Statische
Uploads sind Momentaufnahmen; ein Merge in GitHub aktualisiert sie nicht von
selbst. Live-Verbindungen einzelner Produkte sind separat zu prüfen.

Für eine Zweitprüfung Gemini den exakten PR-Diff, die Baseline, `SECURITY.md` und
Testbefunde geben. NotebookLM mit ausgewählten Architektur- und Vertragsdokumenten
nutzen. Eine Zusammenfassung allein ist kein vollständiger Code-Review.

## iPad-Pro-Zugang

Safari-Lesezeichen für Repository, PRs, Actions, ChatGPT, Gemini, NotebookLM und
OneDrive anlegen. In OneNote das Startblatt verlinken. Auf dem iPad eine SSH-App
und Tailscale für den Zugriff auf den eigenen Rechner verwenden, wenn lokal
gearbeitet werden soll. Hostname, Zugangsdaten und private Netzadressen gehören
nur in die eigene Zugangsdokumentation, nicht in dieses öffentliche Repository.

Die SSH-Verbindung führt zu Windows; von dort kann eine WSL-Sitzung mit `tmux`
geöffnet werden. Der Rechner muss eingeschaltet und erreichbar sein. Eine
erfolgreiche lokale Portprüfung ersetzt den echten Anmeldetest vom iPad nicht.

## Quellen

- [Microsoft: OneNote-Notizbuch auf dem iPad erstellen](https://support.microsoft.com/en-us/onenote/onenote-for-ipad-or-iphone-help-and-learning/create-pages-sections-or-notebooks-in-onenote-for-ipad-or-iphone)
- [OpenAI: Projekte und Chats](https://learn.chatgpt.com/docs/projects)
- [OpenAI: Codex Cloud einrichten](https://learn.chatgpt.com/docs/cloud)
- [GitHub: Copilot über GitHub Mobile](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-mobile)
- [GitHub: Copilot-Umgebung konfigurieren](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-environment)
- [Google: Quellen für das Notebook](https://support.google.com/gemininotebook/answer/16215270?co=GENIE.Platform%3DiOS&hl=en)
