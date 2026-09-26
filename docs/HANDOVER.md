# Übergabe zwischen KI-Werkzeugen

Vier Prompts zum Kopieren: Prompt 1 übergibt GeniusNew an ein anderes Werkzeug
(ChatGPT/Codex, GitHub Copilot, Gemini, Microsoft 365 Copilot), Prompt 2 verlangt von
einem Werkzeug eine Übergabe, wenn es fertig ist. Prompt 3 und 4 sind für GitHub
Copilot in VS Code: Umsetzung eines Auftrags und Review. Die Zahlen und den Stand im
ersten Prompt vor dem Kopieren aus `docs/STATUS.md` aktualisieren.

## 1. Übergabe geben

```text
Du übernimmst die Mitarbeit am Projekt GeniusNew (GitHub: Kaancodm/GeniusNew).

Lies zuerst, in dieser Reihenfolge: AGENTS.md, docs/COLLABORATION.md, docs/STATUS.md,
docs/DECISIONS.md, SECURITY.md, docs/ROADMAP-V01.md. Ohne Zugriff aufs Repo: docs/STATUS.md als Quelle nutzen.

Stand: siehe docs/STATUS.md (Datum oben im Dokument).

Regeln (verbindlich):
- Fail closed, keine Secrets, keine Tests abschwächen oder überspringen.
- Jede Ablehnung braucht einen Test, der ihr Fehlen bemerkt (scripts/refusals.py).
- Nur signierende Rollen halten private Schlüssel.
- Ein Thema pro PR, als Draft. Wer mergen darf und wann: docs/COLLABORATION.md.
- Doku Deutsch, Code und Kommentare Englisch.

Nächste Aufgabe: <Link auf das Issue „Begrenzte Aufgabe“; ohne Issue: Schritt aus
docs/STATUS.md, "Nächste Schritte">

Antworte kurz: erst ein Plan in 5–10 Schritten, dann umsetzen. Vor jedem Push
müssen Tests, Demo und Refusal-Guard der geänderten Module grün sein.
```

## 2. Übergabe verlangen

```text
Erstelle eine Übergabe für GeniusNew (Kaancodm/GeniusNew), kurz und vollständig.

Beginne mit vier Zeilen für Kaan:
Fertig: <Ergebnis mit PR-Link>
Geprüft: <Tests und Reviews auf dem aktuellen Head-SHA>
Blockiert: <keiner | Grund, und wer ihn lösen kann>
Deine Entscheidung: <keine | Auswahl mit Empfehlung>

Danach:

1. Was hast du geändert? Pro PR/Branch: Nummer, Head-SHA, Status (offen/gemergt), CI-Ergebnis.
2. Was ist lokal fertig, aber noch nicht gepusht?
3. Welche Tests, welche Demo und welcher Refusal-Guard sind gelaufen, mit welchem Ergebnis?
4. Offene Probleme, Blocker, Rückfragen an Kaan.
5. Welche Grenzen aus SECURITY.md hast du berührt, welche offen gehaltenen Tests geändert?
6. Die nächsten 5–10 Schritte, nach Priorität.
7. Welche Annahmen hast du getroffen, die nicht im Repo stehen?

Keine Secrets, keine Tokens. Nur Aussagen, die du belegen kannst (SHA, PR-Link,
Befehlsausgabe).
```

Die Antwort auf Prompt 2 geht an Gemini Pro. Gemini überträgt sie in die
Wissensdatenbank (`docs/STATUS.md`, `docs/DECISIONS.md`, NotebookLM). Das läuft
parallel: Ein bereits klarer Folgeauftrag bekommt Prompt 1 sofort. Er wartet nur auf
echte Voraussetzungen (`docs/COLLABORATION.md`, „Was wirklich wartet“).

## 3. Copilot in VS Code: Umsetzung

Im Chat-Modus „Agent“, im Repository über WSL geöffnet. VS Code lädt
`.github/copilot-instructions.md` automatisch. Copilot setzt nur mit einem Auftrag um
(Issue „Begrenzte Aufgabe“) und mergt nur mit Kaans ausdrücklichem OK.

```text
Projekt GeniusNew (Kaancodm/GeniusNew). Du arbeitest als GitHub Copilot im Agent-Modus.

Lies zuerst: AGENTS.md, .github/copilot-instructions.md, docs/COLLABORATION.md.
Auftrag: <Link auf Issue „Begrenzte Aufgabe“>. Nur dieser Umfang, nichts darüber hinaus.

Vorher prüfen und nennen: Branch, voller HEAD-SHA, git status.
Neuer Branch copilot/<thema> von main. Nie auf main pushen, nicht mergen.
Du bist der einzige Implementierer auf diesem Branch.

Regeln:
- Fail closed: Unklares wird mit ContractError abgelehnt, nie still weiter.
- Keine Secrets. Tests nie überspringen, deaktivieren oder abschwächen.
- Jede neue Ablehnung braucht einen Test; ein neues Modul kommt in GUARDED (scripts/refusals.py).
- STOPP und frag mich bei: neuer Abhängigkeit, Grenze aus SECURITY.md, Signaturrollen.
- docs/STATUS.md, docs/DECISIONS.md, docs/DATABASE.md nicht ändern.
- Doku Deutsch; Code, Kommentare und Commit-Betreff Englisch.

Ablauf:
1. Plan in 5–10 Schritten, dann auf mein OK warten.
2. In kleinen Schritten umsetzen.
3. Im WSL-Terminal nacheinander ausführen:
   python3 -m venv .venv && . .venv/bin/activate
   python3 -m pip install --require-hashes -r requirements.txt
   python3 -W error::ResourceWarning -m unittest discover -s tests
   ./scripts/demo.sh
   python3 scripts/refusals.py geniusnew/<geändertes_modul>.py
   git diff --check
4. Draft-PR mit .github/PULL_REQUEST_TEMPLATE.md, inklusive Wissensblock.
5. Zum Schluss vier Zeilen:
   Fertig: <PR-Link>
   Geprüft: <Tests, Demo, Guard auf Head-SHA>
   Blockiert: <keiner | Grund, wer löst>
   Deine Entscheidung: <keine | Auswahl mit Empfehlung>

Nur belegbare Aussagen; was du nicht geprüft hast, als UNKNOWN kennzeichnen.
```

## 4. Copilot in VS Code: Review

Im Chat-Modus „Ask“; der Prompt ändert nichts.

```text
Prüfe den Diff von <Branch oder PR-Nummer> gegen main in GeniusNew nach der
Checkliste in .github/copilot-instructions.md (Punkte 1–5). Keine Änderungen.

Pro Befund: Datei:Zeile, Punkt der Checkliste, blockierend ja/nein, Vorschlag.
Keine Befunde → „keine Befunde“.
Am Ende: geprüfter voller Head-SHA (git rev-parse HEAD).
```
