# Zusammenarbeit der Werkzeuge

Ziel: So wenig Abstimmung wie möglich. Jede Sache hat **genau einen Verantwortlichen**:

- **Code und Regeln:** der `main`-Zweig dieses Repositories. Codex setzt um und mergt.
- **Wissen** (Stand, Entscheidungen, offene Fragen): die **Wissensdatenbank**. Sie wird
  geführt von **Gemini Pro und NotebookLM**.
- **Datenbank im Code:** **Gemini Pro ist Head der Datenbank** (Design, Schema,
  Migrationen, Pflicht-Review); Codex schreibt den Code.
- **Entscheidungen:** Kaan.

## Rollen

| Wer | Macht | Macht nicht |
| --- | --- | --- |
| **Kaan** | entscheidet, gibt die Ausnahmen frei (siehe unten), legt Tags an | — |
| **Gemini Pro** | **Head der Datenbank im Code:** besitzt `docs/DATABASE.md` (Design, Schema, Migrationen), gibt jeden DB-PR frei (Pflicht). **Leitung der Wissensdatenbank:** pflegt `docs/STATUS.md` und `docs/DECISIONS.md`, hält die NotebookLM-Quellen aktuell, meldet Widersprüche zwischen Dokumenten. Dazu **reviewt es jeden PR automatisch** (Gemini Code Assist, Maßstab `.gemini/styleguide.md`), ist **Pflicht-Zweitmeinung** bei den Ausnahmen und prüft Designs vorab bei Prozessgrenzen und Kryptografie. Kontext: `GEMINI.md` | Code ändern (auch DB-Code), Regeln in `AGENTS.md` ändern, die DB-Technik ohne Kaans Entscheidung festlegen |
| **NotebookLM** | **Wissensdatenbank und Auskunft für alle:** beantwortet Fragen zu Stand, Entscheidungen, Grenzen und **Datenbank-Schema** (aus `docs/DATABASE.md`) aus seinen Quellen, jede Antwort mit Quellenangabe | Entscheidungen treffen, Inhalte ohne Quelle |
| **Codex** (ChatGPT Pro) | setzt um und **mergt selbst**: ein Thema pro PR, Branch `codex/<thema>`, mit einem **Wissensblock** in der PR-Beschreibung (siehe unten) | `docs/STATUS.md`, `docs/DECISIONS.md` und `docs/DATABASE.md` ändern, einen DB-PR ohne Gemini-Freigabe mergen, Ausnahmen ohne Kaans OK mergen, Tags anlegen |
| **ChatGPT** (Chat) | plant, formuliert Prompts, erklärt | ins Repo schreiben |
| **GitHub Copilot Pro** | Vervollständigung im Editor; Review jedes PRs nach der Checkliste in `.github/copilot-instructions.md` | eigene PRs ohne Auftrag |
| **Microsoft 365 Copilot** | Berichte, E-Mails, Folien aus dem OneDrive-Ordner `GeniusNew` | Inhalte erfinden, die dort nicht stehen |
| **Claude Code** | nur Ordnung der Regeln: hält `AGENTS.md`, `GEMINI.md`, `docs/HANDOVER.md` und dieses Dokument stimmig, wenn Kaan darum bittet; **Hilfe, wenn Codex feststeckt** | Funktionen umsetzen, eigene Code-PRs, Wissensinhalte pflegen |

## Die Wissensdatenbank

**Inhalt:** `docs/STATUS.md` (Stand, Grenzen, nächste Schritte) und `docs/DECISIONS.md`
(jede Entscheidung mit Datum, Begründung und Quelle). Diese beiden Dateien sind das
Gedächtnis des Projekts. Nur Gemini schreibt sie.

**Quellen in NotebookLM** (Notebook „GeniusNew“): `docs/STATUS.md`,
`docs/DECISIONS.md`, `docs/DATABASE.md` (sobald sie existiert), `SECURITY.md`,
`docs/ROADMAP-V01.md`, `docs/COLLABORATION.md`, `AGENTS.md`. Das Repository ist öffentlich, deshalb können die Quellen als Links auf
die Rohdateien in `main` eingebunden werden, zum Beispiel
`https://raw.githubusercontent.com/Kaancodm/GeniusNew/main/docs/STATUS.md`.

**Wer fragt wen:** Jedes Werkzeug und Kaan fragen bei Wissensfragen zuerst NotebookLM,
also „Was wurde zu X entschieden?“, „Warum ist Y so?“ oder „Was ist der nächste
Schritt?“. Steht es dort nicht, ist es noch nicht entschieden. Dann geht die Frage an
Kaan, und Gemini trägt die Antwort in `docs/DECISIONS.md` ein.

**Pflege nach jedem Merge** (Gemini, mit der Gemini CLI im Repository):
1. Den Wissensblock des gemergten PRs lesen.
2. `docs/STATUS.md` und bei Bedarf `docs/DECISIONS.md` auf einem Branch
   `gemini/wissen-<datum>` aktualisieren und einen PR öffnen. Ein solcher PR ändert nur
   diese zwei Dateien.
3. Gemini mergt ihn selbst, sobald `contracts` grün ist.
4. Die Quellen in NotebookLM aktualisieren und Widersprüche zwischen den Dokumenten als
   Issue melden.

## Die Datenbank im Code (Head: Gemini Pro)

**Wozu:** Die Datenbank ist die **Grundlage für das Portal**. Das Portal ist die
Web-Oberfläche, über die Nutzer Aufträge stellen, ihren Verlauf sehen und Freigebende
Approvals erteilen. Das alte `bürgerbüro/portal` ist nach `docs/MIGRATION-MATRIX.md`
nur Lesequelle (REBUILD): Es wird neu gebaut, nicht kopiert.

**Was hineinkommt:** Job- und Annahme-Ledger, wartende Approval-Jobs, die Audit-Kette
und der Anker-Zustand. Damit übersteht der Dienst einen Neustart, ohne etwas doppelt
anzunehmen oder zu vergessen. Für das Portal kommen hinzu: Nutzer und ihre Zuordnung zu
Principals, API-Key-Digests, Sitzungen, der Auftragsverlauf je Nutzer, die Rolle der
Freigebenden und Quoten.

**Reihenfolge:**
1. **Design (Gemini):** Gemini schreibt `docs/DATABASE.md` auf einem Branch
   `gemini/db-design`. Inhalt:
   - **Betriebsort von Portal und Kern** im Vergleich, zum Beispiel Vercel gegen einen
     eigenen Server. Das entscheidet über die Technik: Serverless hat keine dauerhafte
     lokale Datei, und Worker-Isolation und Anker-Prozess brauchen einen echten Host.
   - Die Techniken im Vergleich (mindestens SQLite aus der Standardbibliothek und ein
     gehostetes PostgreSQL), mit Empfehlung.
   - Das Schema je Tabelle, auch für die Portal-Tabellen.
   - Die Migrationen, das Verhalten bei beschädigtem Speicher und der Umgang mit
     Zugangsdaten.
2. **Entscheidung (Kaan):** Kaan wählt im PR Betriebsort und Technik. Gemini trägt sie in
   `docs/DECISIONS.md` ein und mergt den Design-PR. Braucht die Technik eine neue
   Abhängigkeit, ist das eine Ausnahme mit Kaans OK.
3. **Umsetzung (Codex), je ein PR:** (a) Ledger mit Ablauf, (b) wartende Jobs,
   (c) Audit-Kette, (d) Anker-Zustand, danach die Portal-Tabellen und (e) das Portal
   selbst. Jeder DB-PR braucht eine **ausdrückliche
   Gemini-Freigabe im PR** zusätzlich zur grünen CI. Ohne Freigabe mergt Codex nicht.
4. **Doku (NotebookLM):** `docs/DATABASE.md` wird Quelle im Notebook; Fragen zum Schema
   gehen an NotebookLM.

**Harte Vorgaben für das Design:**
- **Der Anker liegt nicht in derselben Datenbank wie die Audit-Kette** und nicht unter
  denselben Zugangsdaten. Sonst kann der Schreiber beide gemeinsam zurücksetzen, und der
  Anker ist wertlos. Der Anker-Prozess hat seinen eigenen Speicher.
- Fail closed: Ein beschädigter, fehlender oder nicht lesbarer Speicher verhindert den
  Start. Es gibt keinen stillen Neustart bei null.
- Keine Zugangsdaten und keine Datenbankdateien im Repository. Die Verbindung kommt aus
  der serverseitigen Laufzeitkonfiguration.
- Jede neue Ablehnung im DB-Code braucht einen Test; das Modul kommt in `GUARDED`.
- **Portal:** Der Browser ist nicht vertrauenswürdig. Identität, Rechte, Tier und
  Job-Kennung bestimmt der Server, so wie heute beim HTTP-Eingang. Das Portal spricht
  mit dem Kern nur über dessen HTTP-Eingang und nie direkt mit der Datenbank des
  Kerns.
- Grenzen aus `SECURITY.md`, die sich dadurch ändern (prozesslokale Ledger,
  Anker-Rückschnitt), werden je PR mit umgekehrtem Test und angepasster Tabelle
  geschlossen.

**Ein DB-PR** ist jeder PR, der Speicher-Code, Schema, Migrationen oder
`docs/DATABASE.md` ändert.

## Der Ablauf eines Themas

1. Kaan wählt den nächsten Schritt; die Auskunft dazu gibt NotebookLM. Kaan gibt ihn
   Codex mit Prompt 1 aus `docs/HANDOVER.md`.
2. Codex öffnet einen Draft-PR `codex/<thema>` und schreibt den Plan in die
   PR-Beschreibung. Ändert das Thema eine Prozessgrenze oder Kryptografie, fragt Codex
   im PR mit `@gemini-code-assist` nach einer Design-Vorprüfung, bevor Code entsteht.
3. **Gemini reviewt automatisch**, Copilot zusätzlich. Ein neues Review nach
   Änderungen fordert Codex mit `/gemini review` im PR an.
4. **Codex mergt selbst**, sobald die CI grün ist (`contracts`), der Wissensblock
   ausgefüllt ist und kein blockierender Review-Befund offen ist. Blockierend sind
   Gemini-Befunde der Stufe **Critical** oder **High** und Copilot-Befunde zu den
   Punkten der Checkliste. Codex behebt sie oder begründet im Thread, warum sie nicht
   zutreffen. Kaans ausdrückliches OK braucht es nur für die Ausnahmen: eine neue
   Abhängigkeit, eine geänderte Grenze aus `SECURITY.md` (offen gehaltener Test
   umgekehrt) und Tags. Bei diesen Ausnahmen muss vorher ein
   Gemini-Sicherheits-Review im PR stehen. **DB-PRs** brauchen zusätzlich eine
   ausdrückliche Gemini-Freigabe.
5. Gemini überträgt den Wissensblock in die Wissensdatenbank (siehe oben).

**Kommunikation läuft über den PR**, nicht über Kopieren zwischen Chats. Plan,
Rückfragen an Gemini, Reviews, Begründungen und der Wissensblock stehen im PR. Jedes
Werkzeug und Kaan sehen so denselben Stand.

### Wissensblock (Pflicht in jeder PR-Beschreibung von Codex)

```text
## Für die Wissensdatenbank
- Was ist jetzt anders: <1–3 Sätze>
- Entscheidungen: <keine | Entscheidung, Begründung>
- Geänderte Grenzen (SECURITY.md): <keine | welche>
- Messwerte: <Anzahl Tests, Ergebnis der Demo>
- Nächster Schritt: <Vorschlag>
```

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

## Ablage in OneDrive (für Microsoft 365 Copilot)

Ordner `GeniusNew` mit genau vier Dateien, die nach jeder Pflege der Wissensdatenbank
ersetzt werden: `STATUS.md`, `DECISIONS.md`, `SECURITY.md`, `ROADMAP-V01.md`. Keine
eigenen Kopien bearbeiten: Was dort falsch ist, korrigiert Gemini in der
Wissensdatenbank, danach werden die Dateien neu abgelegt.
