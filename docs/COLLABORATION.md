# Zusammenarbeit der Werkzeuge

Ziel: So wenig Abstimmung wie möglich. Es gibt **eine Quelle der Wahrheit** (den
`main`-Zweig dieses Repositories), **einen Umsetzer, der auch mergt** (Codex) und
**eine Person, die entscheidet** (Kaan). Alle anderen Werkzeuge lesen nur mit oder prüfen.

## Rollen

| Wer | Macht | Macht nicht |
| --- | --- | --- |
| **Kaan** | entscheidet, gibt die Ausnahmen frei (siehe unten), legt Tags an, legt `docs/STATUS.md` in OneDrive und NotebookLM ab | — |
| **Codex** (ChatGPT Pro) | setzt um und **mergt selbst**: ein Thema pro PR, Branch `codex/<thema>`, aktualisiert `docs/STATUS.md` im selben PR | Ausnahmen ohne Kaans OK mergen, Tags anlegen |
| **ChatGPT** (Chat) | plant, formuliert Prompts, erklärt | ins Repo schreiben |
| **GitHub Copilot Pro** | Vervollständigung im Editor; Review jedes PRs nach der Checkliste in `.github/copilot-instructions.md` | eigene PRs ohne Auftrag |
| **Gemini** | **reviewt jeden PR automatisch** (Gemini Code Assist, Maßstab `.gemini/styleguide.md`); **Pflicht-Zweitmeinung** bei den Ausnahmen; Design-Vorprüfung bei Prozessgrenzen und Kryptografie; Recherche. Kontext: `GEMINI.md` | ins Repo schreiben, mergen |
| **NotebookLM** | beantwortet Fragen zum Stand aus `docs/STATUS.md`, `SECURITY.md`, `docs/ROADMAP-V01.md` | Entscheidungen treffen |
| **Microsoft 365 Copilot** | Berichte, E-Mails, Folien aus `docs/STATUS.md` im OneDrive-Ordner `GeniusNew` | Inhalte erfinden, die nicht in `STATUS.md` stehen |
| **Claude Code** | nur Ordnung und Kommunikation: hält `STATUS.md`, `AGENTS.md`, `HANDOVER.md` und dieses Dokument stimmig, wenn Kaan darum bittet; **Hilfe, wenn Codex feststeckt** | Funktionen umsetzen, eigene Code-PRs |

## Der Ablauf eines Themas

1. Kaan wählt den nächsten Schritt aus `docs/STATUS.md` und gibt ihn Codex, mit Prompt 1
   aus `docs/HANDOVER.md`.
2. Codex öffnet einen Draft-PR `codex/<thema>` und schreibt den Plan in die
   PR-Beschreibung. Ändert das Thema eine Prozessgrenze oder Kryptografie, fragt Codex
   im PR mit `@gemini-code-assist` nach einer Design-Vorprüfung, bevor Code entsteht.
   `docs/STATUS.md` wird im selben PR aktualisiert (Abschnitte „Seit v0.1 gemergt“ und
   „Nächste Schritte“).
3. **Gemini reviewt automatisch**, Copilot zusätzlich. Ein neues Review nach
   Änderungen fordert Codex mit `/gemini review` im PR an.
4. **Codex mergt selbst**, sobald die CI grün ist (`contracts`) und kein blockierender
   Review-Befund offen ist. Blockierend sind Gemini-Befunde der Stufe **Critical** oder
   **High** und Copilot-Befunde zu den Punkten der Checkliste. Codex behebt sie oder
   begründet im Thread, warum sie nicht zutreffen. Kaans ausdrückliches OK braucht es
   nur für die Ausnahmen: eine neue Abhängigkeit, eine geänderte Grenze aus
   `SECURITY.md` (offen gehaltener Test umgekehrt) und Tags. Bei diesen Ausnahmen muss
   vorher ein Gemini-Sicherheits-Review im PR stehen.
5. Nach dem Merge legt Kaan die neue `docs/STATUS.md` in den OneDrive-Ordner
   `GeniusNew` und aktualisiert die Quelle in NotebookLM.

Ein Thema ist erst fertig, wenn `STATUS.md` auf `main` es richtig beschreibt.

**Kommunikation läuft über den PR**, nicht über Kopieren zwischen Chats. Plan,
Rückfragen an Gemini (`@gemini-code-assist …`), Reviews und Begründungen stehen im
PR, so dass jedes Werkzeug und Kaan denselben Stand sehen.

## Wann Codex Claude um Hilfe bittet

Codex steckt fest, wenn **eines** davon zutrifft:

- dieselbe CI-Prüfung ist nach zwei eigenen Fixversuchen noch rot,
- ein Test, der Refusal-Guard oder die Demo widerspricht dem Auftrag, und die Regeln
  in `AGENTS.md` lassen keinen Weg offen,
- die Aufgabe berührt eine Grenze aus `SECURITY.md`, und unklar ist, ob sie geschlossen
  werden soll.

Dann schreibt Codex einen **Hilferuf** in genau dieser Form, und Kaan gibt ihn an
Claude Code weiter:

```text
HILFERUF GeniusNew
PR / Branch: <#nummer, codex/...>   Head-SHA: <sha>
Ziel: <ein Satz>
Was fehlschlägt: <Check-Name oder Befehl>
Fehlermeldung (gekürzt): <max. 30 Zeilen>
Schon versucht: <1–3 Punkte>
Frage an Claude: <konkret>
```

Claude antwortet mit einer Diagnose und einem Vorschlag. Codex setzt ihn im eigenen PR
um; Claude pusht nur, wenn Kaan es ausdrücklich sagt.

## Ablage in OneDrive

Ordner `GeniusNew` mit genau drei Dateien, die nach jedem Merge ersetzt werden:
`STATUS.md`, `SECURITY.md`, `ROADMAP-V01.md`. Keine eigenen Kopien bearbeiten: Was dort
falsch ist, wird im Repository korrigiert und neu abgelegt.
