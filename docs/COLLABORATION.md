# Zusammenarbeit der Werkzeuge

Ziel: So wenig Abstimmung wie möglich. Jede Sache hat **genau einen Verantwortlichen**:

- **Code und Regeln:** der `main`-Zweig dieses Repositories. Codex setzt um und mergt.
- **Wissen** (Stand, Entscheidungen, offene Fragen): die **Wissensdatenbank**.
  **Claude Code** pflegt die repository-seitigen Wissensquellen; **NotebookLM** dient
  als quellengestützte Auskunft.
- **Datenbank im Code:** **Claude Code ist Head der Datenbank** (Design, Schema,
  Migrationen, Pflicht-Review); Codex schreibt den Code.
- **Ordnung, Struktur und Konflikte zwischen den Plattformen:** **Claude Code**, mit
  Überschreibrecht gegenüber allen Werkzeugen.
- **Entscheidungen:** Kaan. Kaan steht über allen, auch über Claude Code.

## Rollen

| Wer | Macht | Macht nicht |
| --- | --- | --- |
| **Kaan** | entscheidet, gibt die Ausnahmen frei (siehe unten), legt Tags an | — |
| **NotebookLM** | **Wissensdatenbank und Auskunft für alle:** beantwortet Fragen zu Stand, Entscheidungen, Grenzen und **Datenbank-Schema** (aus `docs/DATABASE.md`) aus seinen verifizierten Quellen, jede Antwort mit Quellenangabe | Entscheidungen treffen, Inhalte ohne Quelle; einen Quellenstand als aktuell behaupten, wenn er `UNKNOWN` ist |
| **Codex** (ChatGPT Pro) | setzt um und **mergt selbst**: ein Thema pro PR, Branch `codex/<thema>`, mit einem **Wissensblock** in der PR-Beschreibung (siehe unten) | `docs/STATUS.md`, `docs/DECISIONS.md` und `docs/DATABASE.md` ändern, einen DB-PR ohne `Claude DB Review: APPROVED` am exakten Head-SHA mergen, Ausnahmen ohne Kaans OK mergen, Tags anlegen |
| **ChatGPT** (Chat) | plant, formuliert Prompts, erklärt | ins Repo schreiben |
| **GitHub Copilot Pro** | Vervollständigung im Editor; Review jedes PRs nach der Checkliste in `.github/copilot-instructions.md` | eigene PRs ohne Auftrag |
| **Microsoft 365 Copilot** | Berichte, E-Mails, Folien aus dem OneDrive-Ordner `GeniusNew` | Inhalte erfinden, die dort nicht stehen |
| **Claude Code** | **Head der Datenbank im Code, Wissenspfleger, Ordnungs- und Konfliktinstanz:** besitzt `docs/DATABASE.md`, `docs/STATUS.md`, `docs/DECISIONS.md` sowie die Governance-Regeln; führt die in diesem Dokument verlangten Claude-Reviews durch, entscheidet Konflikte zwischen Werkzeugen verbindlich, darf Regel-/Doku-Widersprüche korrigieren und **hilft, wenn Codex feststeckt** | Kaans Entscheidungen oder die Ausnahmen überstimmen; reguläre Feature-Implementierung übernehmen, wenn kein ausdrücklich zugewiesener Konflikt-/Review-Auftrag vorliegt |

## Die Wissensdatenbank

**Inhalt:** `docs/STATUS.md` (Stand, Grenzen, nächste Schritte) und `docs/DECISIONS.md`
(jede Entscheidung mit Datum, Begründung und Quelle). Diese beiden Dateien sind das
Gedächtnis des Projekts. Nur Claude Code schreibt sie.

**Quellen in NotebookLM** (Notebook „GeniusNew“): `docs/STATUS.md`,
`docs/DECISIONS.md`, `docs/DATABASE.md` (sobald sie existiert), `SECURITY.md`,
`docs/ROADMAP-V01.md`, `docs/COLLABORATION.md`, `AGENTS.md`. Das Repository ist öffentlich; Ziel ist die Einbindung als **Raw-GitHub-Links** auf
die Rohdateien in `main`, zum Beispiel
`https://raw.githubusercontent.com/Kaancodm/GeniusNew/main/docs/STATUS.md`.
Die tatsächliche Umstellung in NotebookLM ist ein **offener Setup-Punkt für Kaan**.
Der Aktualisierungsstatus wird je Quelle einzeln belegt; bis zur Verifikation gilt er
als `UNKNOWN`. Bei Widersprüchen ist das Repository am referenzierten Commit-SHA
maßgeblich.

**Wer fragt wen:** Jedes Werkzeug und Kaan fragen bei Wissensfragen zuerst NotebookLM,
also „Was wurde zu X entschieden?“, „Warum ist Y so?“ oder „Was ist der nächste
Schritt?“. Steht es dort nicht, ist es noch nicht entschieden. Dann geht die Frage an
Kaan, und Claude Code trägt die Antwort in `docs/DECISIONS.md` ein.

**Pflege nach jedem Merge** (Claude Code im Repository):
1. Den Wissensblock des gemergten PRs lesen.
2. `docs/STATUS.md` und bei Bedarf `docs/DECISIONS.md` auf einem Branch
   `claude/wissen-<datum>` aktualisieren und einen PR öffnen. Ein solcher PR ändert nur
   diese zwei Dateien.
3. Codex mergt ihn, sobald `contracts` grün ist und kein einschlägiges Gate offen ist.
4. NotebookLM-Quellen erst nach nachgewiesener Raw-Link-Aktualisierung als aktuell
   markieren. Bis dahin bleibt der Status der jeweiligen Quelle `UNKNOWN`.
   Widersprüche zwischen Dokumenten werden als Issue gemeldet.

## Die Datenbank im Code (Head: Claude Code)

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
1. **Design (Claude):** Claude Code schreibt `docs/DATABASE.md` auf einem Branch
   `claude/db-design`. Inhalt:
   - **Betriebsort von Portal und Kern** im Vergleich, zum Beispiel Vercel gegen einen
     eigenen Server. Das entscheidet über die Technik: Serverless hat keine dauerhafte
     lokale Datei, und Worker-Isolation und Anker-Prozess brauchen einen echten Host.
   - Die Techniken im Vergleich (mindestens SQLite aus der Standardbibliothek und ein
     gehostetes PostgreSQL), mit Empfehlung.
   - Das Schema je Tabelle, auch für die Portal-Tabellen.
   - Die Migrationen, das Verhalten bei beschädigtem Speicher und der Umgang mit
     Zugangsdaten.
2. **Entscheidung (Kaan):** Kaan wählt im PR Betriebsort und Technik. Claude Code trägt
   die Entscheidung in `docs/DECISIONS.md` ein. Codex mergt den Design-PR erst nach den
   unten definierten Gates. Braucht die Technik eine neue Abhängigkeit, ist das eine
   Ausnahme mit Kaans OK.
3. **Umsetzung (Codex), je ein PR:** (a) Ledger mit Ablauf, (b) wartende Jobs,
   (c) Audit-Kette, (d) Anker-Zustand, danach die Portal-Tabellen und (e) das Portal
   selbst. Jeder DB-PR braucht zusätzlich zur grünen CI einen Claude-DB-Review auf dem
   **exakten aktuellen Head-SHA**. Der Review-Wortlaut ist genau:
   `Claude DB Review: APPROVED` oder `Claude DB Review: CHANGES REQUESTED`.
   **DB-Merge-Gate:** `CI PASS → Claude DB Review: APPROVED am exakten Head-SHA →
   ggf. Kaan-Gates (neue Dependency, SECURITY.md-Grenze, Tags) → Merge durch Codex`.
   `CHANGES REQUESTED` oder eine Freigabe für einen älteren Head-SHA blockiert den Merge.
4. **Doku (NotebookLM):** `docs/DATABASE.md` ist als Raw-GitHub-Quelle vorgesehen;
   bis die Quelle in NotebookLM verifiziert aktualisiert wurde, ist ihr Status
   `UNKNOWN`. Fragen zum Schema gehen an NotebookLM, das Repository bleibt maßgeblich.

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
   PR-Beschreibung. Ändert das Thema eine Prozessgrenze oder Kryptografie, fordert Codex
   vor der Implementierung eine Claude-Design-Vorprüfung im PR an.
3. **Review:** Copilot reviewt nach seiner Checkliste. Claude Code reviewt, wenn dieses
   Dokument es verlangt, insbesondere bei DB-PRs, Kaan-Ausnahmen, Konflikten und
   ausdrücklich zugewiesenen Sicherheits-/Designfragen. Ein verpflichtender
   Claude-Review nennt den **exakten Head-SHA**. Nach einer Änderung am Head ist eine
   ältere Freigabe nicht mehr ausreichend.
4. **Codex mergt selbst**, sobald die CI grün ist (`contracts`), der Wissensblock
   ausgefüllt ist und kein blockierender Review-Befund offen ist. Ein
   `Claude DB Review: CHANGES REQUESTED` blockiert einen DB-PR; Copilot-Befunde zu den
   Punkten seiner Checkliste müssen behoben oder im Thread nachvollziehbar entkräftet
   sein. Kaans ausdrückliches OK braucht es für eine neue Abhängigkeit, eine geänderte
   Grenze aus `SECURITY.md` (offen gehaltener Test umgekehrt) und Tags. Bei diesen
   Ausnahmen muss vorher ein Claude-Sicherheits-Review am exakten Head-SHA im PR stehen.
   Für **DB-PRs** gilt verbindlich:
   `CI PASS → Claude DB Review: APPROVED am exakten Head-SHA → ggf. Kaan-Gates
   (neue Dependency, SECURITY.md-Grenze, Tags) → Merge durch Codex`.
5. Claude Code überträgt den Wissensblock in die repository-seitige Wissensbasis
   (siehe oben); der NotebookLM-Quellenstatus bleibt bis zur verifizierten
   Raw-Link-Aktualisierung ggf. `UNKNOWN`.

**Kommunikation läuft über den PR**, nicht über Kopieren zwischen Chats. Plan,
Rückfragen an Claude Code, Reviews, Begründungen und der Wissensblock stehen im PR. Jedes
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

## Konflikte zwischen den Plattformen (Claude Code entscheidet)

Ein Konflikt liegt vor, wenn zwei Werkzeuge sich widersprechen, zum Beispiel:

- Copilot meldet einen blockierenden Befund, Codex hält ihn für falsch;
- ein Dokument widerspricht dem Code oder einem anderen Dokument (etwa `STATUS.md`,
  `DATABASE.md` und `SECURITY.md` untereinander);
- unklar ist, wem eine Datei oder eine Aufgabe gehört.

Jedes Werkzeug meldet ihn mit einem **KONFLIKT**-Block im PR oder als Issue. Kaan gibt
ihn an Claude Code weiter:

```text
KONFLIKT GeniusNew
Wer gegen wen: <z. B. Codex gegen Copilot>
Wo: <PR/Issue/Datei:Zeile>
Position A: <1–3 Sätze mit Beleg>
Position B: <1–3 Sätze mit Beleg>
Was blockiert ist: <PR, Merge, Aufgabe>
```

Claude Code entscheidet anhand von Code, Tests, `AGENTS.md` und `docs/DECISIONS.md` und
begründet die Entscheidung im PR oder Issue. **Jede Entscheidung endet mit einem fertigen
Prompt für jedes betroffene Werkzeug**, den Kaan nur noch kopiert: was zu tun ist, in
welcher Datei, bis wann es als erledigt gilt. Die Entscheidung ist für alle Werkzeuge
verbindlich; Claude Code trägt sie in `docs/DECISIONS.md` ein. Wo eine Datei korrigiert
werden muss, darf Claude Code sie selbst ändern, auch Dateien anderer Werkzeuge
(Überschreibrecht). Nicht
überschreiben darf Claude Code Kaans Entscheidungen und die Ausnahmen (neue
Abhängigkeit, Grenze aus `SECURITY.md`, Tags). Das bleibt Kaans Sache. Claudes eigene
PRs mergt Kaan.

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

## Geräte

| Gerät | Arbeit |
| --- | --- |
| iPad Pro | ChatGPT, Claude, NotebookLM, GitHub (auch Copilot-Aufträge über GitHub Mobile), OneDrive/OneNote, Reviews |
| Laptop (Linux/WSL) | Codex, Git-Checkout, lokale Tests; ein Implementierer je Branch, Tests nacheinander |

Zugriff vom iPad auf den Laptop über SSH (z. B. mit Tailscale) in eine WSL-Sitzung mit
`tmux`. Hostnamen, Zugangsdaten und private Adressen gehören nur in die private
Zugangsdokumentation, nie in dieses öffentliche Repository.

**Aufgaben** entstehen über das Issue-Formular „Begrenzte Aufgabe“
(`.github/ISSUE_TEMPLATE/agent-task.yml`), **PRs** über
`.github/PULL_REQUEST_TEMPLATE.md` (mit Wissensblock). Für Copilot-Cloud-Aufträge
liegt eine Umgebungsvorlage in `docs/setup/copilot-setup-steps.yml`. Aktiv wird sie
erst als `.github/workflows/copilot-setup-steps.yml` auf `main`; das ist Kaans
Schritt, weil es Workflow-Schreibrechte braucht.

## Ablage in OneDrive (für Microsoft 365 Copilot)

Ordner `GeniusNew` mit genau vier Dateien, die nach jeder Pflege der Wissensdatenbank
ersetzt werden: `STATUS.md`, `DECISIONS.md`, `SECURITY.md`, `ROADMAP-V01.md`. Keine
eigenen Kopien bearbeiten: Was dort falsch ist, korrigiert Claude Code in der
Wissensdatenbank, danach werden die Dateien neu abgelegt. Die Git-Arbeitskopie liegt
**außerhalb** des synchronisierten OneDrive-Ordners, damit sich Synchronisation und Git
nicht in die Quere kommen. In OneNote ein Notizbuch `GeniusNew` mit den Abschnitten
`Start`, `Entscheidungen`, `Reviews` und `Ideen`; Aufgaben und Freigaben werden aus
GitHub verlinkt. Quellenpakete für NotebookLM oder Claude Code tragen Datum und vollen
Commit-SHA; ein Merge aktualisiert statische Uploads nicht von selbst.


## Scope dieser Governance-Migration

Die Umstellung auf Claude betrifft die Governance- und Review-Zuständigkeiten.
`README.md` ist bewusst **nicht** Teil dieser Migration und wird in einem separaten
Doku-PR erst nach dieser Governance-Änderung und nach PR #63 behandelt. PR #63 selbst
wird durch diese Migration nicht verändert.
