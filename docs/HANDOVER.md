# Übergabe zwischen KI-Werkzeugen

## 0. Aktueller Auftrag bis Beta

Dieser Block ist die erste Übergabe an Claude Code, Codex oder Copilot, solange Kaan
nichts anderes sagt:

```text
GeniusNew läuft im temporären Beta-Betriebsmodus von Kaan (27.09.2026).

Lies zuerst den aktuellen Repository-Stand, nicht alte Chat-Zusammenfassungen:
1. git fetch
2. main / voller SHA
3. offene PRs und ihre Basen
4. AGENTS.md und docs/COLLABORATION.md
5. SECURITY.md und die für die Aufgabe relevanten Tests

Vorrangige Regeln:
- Ziel: ohne unnötige Stopps bis technisch beta-ready weiterentwickeln.
- Neuer GitHub-Stand schlägt alte Übergaben oder alte Branches.
- Gemini-Freigaben sind zurzeit kein Blocker.
- Copilot Pro darf als Assistent/Reviewer genutzt werden.
- Claude Code darf bei größeren Phasen, Architektur/Security und Unsicherheit aktiv
  mitarbeiten; nicht nur auf formalen Hilferuf.
- Ein Implementierer pro Branch. Fail closed. Keine Secrets. Keine Tests abschwächen.
- Nur tatsächlich ausgeführte Prüfungen als bestanden melden.
- Öffentlicher Deploy/Release erst mit Kaans ausdrücklichem OK.

Wenn du Claude Code bist:
- Prüfe zuerst, ob dein bisheriger Branch oder deine bisherigen Regeln veraltet sind.
- Arbeite nie von einer älteren Baseline weiter, wenn main inzwischen weiter ist.
- Bei größeren Aufgaben eigenen claude/<thema>-Branch vom aktuellen main verwenden.
- Übergib am Ende PR, Head-SHA, tatsächlich gelaufene Tests/Demo/Refusal-Checks und
  verbleibende Blocker.

Wenn du Codex/ChatGPT bist:
- Kleine klare Schritte direkt erledigen.
- Claude bei größeren zusammenhängenden Phasen oder echter Unsicherheit hinzuziehen.
```


Zwei Prompts zum Kopieren: einer übergibt GeniusNew an ein anderes Werkzeug
(ChatGPT/Codex, GitHub Copilot, Gemini, Microsoft 365 Copilot), der andere verlangt von
einem Werkzeug eine Übergabe, wenn es fertig ist. Die Zahlen und den Stand im ersten
Prompt vor dem Kopieren aus `docs/STATUS.md` aktualisieren.

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

Nächste Aufgabe: <Schritt aus docs/STATUS.md, "Nächste Schritte">

Antworte kurz: erst ein Plan in 5–10 Schritten, dann umsetzen. Vor jedem Push
müssen Tests, Demo und Refusal-Guard der geänderten Module grün sein.
```

## 2. Übergabe verlangen

```text
Erstelle eine Übergabe für GeniusNew (Kaancodm/GeniusNew), kurz und vollständig:

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
Wissensdatenbank (`docs/STATUS.md`, `docs/DECISIONS.md`, NotebookLM), bevor das nächste
Werkzeug Prompt 1 bekommt.
