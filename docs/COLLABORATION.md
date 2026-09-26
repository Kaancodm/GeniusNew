# Zusammenarbeit der Werkzeuge

Ziel: So wenig Abstimmung wie möglich. Jede Sache hat **genau einen Verantwortlichen**:

- **Code und Regeln:** der `main`-Zweig dieses Repositories. Codex setzt um und mergt.
- **Wissen** (Stand, Entscheidungen, offene Fragen): die **Wissensdatenbank**. Sie wird
  geführt von **Gemini Pro und NotebookLM**.
- **Datenbank im Code:** **Gemini Pro ist Head der Datenbank** (Design, Schema,
  Migrationen, Pflicht-Review); Codex schreibt den Code.
- **Ordnung, Struktur und Konflikte zwischen den Plattformen:** **Claude Code**, mit
  Überschreibrecht gegenüber allen Werkzeugen.
- **Entscheidungen:** Kaan. Kaan steht über allen, auch über Claude Code.

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
| **Claude Code** | **Ordnung, Struktur und Konfliktlöser zwischen den Plattformen, mit Überschreibrecht:** besitzt die Regeln (`AGENTS.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.gemini/*`, `docs/HANDOVER.md`, dieses Dokument); entscheidet Konflikte zwischen Werkzeugen verbindlich; darf jede Datei korrigieren, auch die von Gemini, wenn sie den Regeln oder dem Code widerspricht; **hilft, wenn Codex feststeckt** | Kaans Entscheidungen oder die Ausnahmen überstimmen; Funktionen umsetzen, außer um einen Konflikt aufzulösen |

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

**Pflege nach jedem Merge** (Gemini, mit der Gemini CLI im Repository). Sie läuft
**parallel zur Entwicklung** und hält keinen Folgeauftrag auf. Mehrere Merges eines
Tages dürfen in einem Wissens-PR zusammengefasst werden.
1. Den Wissensblock des gemergten PRs lesen.
2. `docs/STATUS.md` und bei Bedarf `docs/DECISIONS.md` auf einem Branch
   `gemini/wissen-<datum>` aktualisieren und einen PR öffnen. Ein solcher PR ändert nur
   diese zwei Dateien.
3. Gemini mergt ihn selbst, sobald `contracts` grün ist.
4. Die Quellen in NotebookLM aktualisieren und Widersprüche zwischen den Dokumenten als
   Issue melden.

**Drei Zustände.** Jede Aussage in der Wissensdatenbank ist erkennbar als eines davon:

| Zustand | Bedeutung |
| --- | --- |
| **Belegt** | gestützt durch Code, Test, Befehlsausgabe oder PR mit vollem SHA |
| **Entschieden** | von Kaan oder innerhalb einer ausdrücklich vergebenen Befugnis festgelegt, mit Zeile in `docs/DECISIONS.md` |
| **Vorschlag** | mögliche Verbesserung, über die noch nicht entschieden ist |

Ein Vorschlag wird nicht dadurch entschieden, dass er oft wiederholt wird.
`docs/STATUS.md` beschreibt nur, was jetzt gilt; Überholtes fällt dort heraus und bleibt
über Git und `docs/DECISIONS.md` nachvollziehbar. In `docs/DECISIONS.md` bleiben alte
Zeilen stehen, eine überholte Entscheidung verweist auf die neue.

**Aus Wissen wird Regel.** Die stärksten Erkenntnisse landen nicht nur in Notizen: Ein
gefundener Fehler wird zu einem Auftrag für Test und Korrektur (Codex), eine
wiederkehrende Rückfrage zu einem Regelvorschlag an Claude Code.

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

1. **Auftrag:** Gemini bereitet den nächsten Schritt als Issue „Begrenzte Aufgabe“ vor
   (`.github/ISSUE_TEMPLATE/agent-task.yml`, sechs Angaben, siehe unten). Kaan wählt
   ihn aus und gibt ihn Codex mit Prompt 1 aus `docs/HANDOVER.md`; der Link auf das
   Issue ersetzt eine lange Beschreibung.
2. Codex öffnet einen Draft-PR `codex/<thema>` und schreibt den Plan in die
   PR-Beschreibung. Ändert das Thema eine Prozessgrenze oder Kryptografie, fragt Codex
   im PR mit `@gemini-code-assist` nach einer Design-Vorprüfung, bevor Code entsteht.
3. **Gemini reviewt automatisch**, Copilot zusätzlich. Ein Review gilt für den Head-SHA,
   den es geprüft hat. Ändert Codex danach Code, fordert er vor dem Merge ein neues
   Review an (`/gemini review`, bei Copilot erneut anfordern). Befunde sammeln sich im
   PR.
4. **Codex mergt selbst**, sobald die CI grün ist (`contracts`), der Wissensblock
   ausgefüllt ist und kein blockierender Review-Befund offen ist. CI und Reviews
   beziehen sich dabei auf denselben, aktuellen Head. Blockierend sind
   Gemini-Befunde der Stufe **Critical** oder **High** und Copilot-Befunde zu den
   Punkten der Checkliste. Codex behebt sie oder begründet im Thread, warum sie nicht
   zutreffen. Kaans ausdrückliches OK braucht es nur für die Ausnahmen: eine neue
   Abhängigkeit, eine geänderte Grenze aus `SECURITY.md` (offen gehaltener Test
   umgekehrt) und Tags. Bei diesen Ausnahmen muss vorher ein
   Gemini-Sicherheits-Review im PR stehen. **DB-PRs** brauchen zusätzlich eine
   ausdrückliche Gemini-Freigabe.
5. Gemini überträgt den Wissensblock in die Wissensdatenbank (siehe oben), parallel
   zum nächsten Auftrag.

**Kommunikation läuft über den PR**, nicht über Kopieren zwischen Chats. Plan,
Rückfragen an Gemini, Reviews, Begründungen und der Wissensblock stehen im PR. Jedes
Werkzeug und Kaan sehen so denselben Stand.

### Der Auftrag: sechs Angaben

| Angabe | Inhalt |
| --- | --- |
| **Ziel** | Was nachher konkret funktioniert |
| **Ausgangslage** | Baseline-SHA, relevante Dateien, Entscheidungen und Befunde |
| **Umfang** | Was zu diesem Auftrag gehört und was ausdrücklich nicht |
| **Abnahme** | Woran man die Erledigung erkennt: Prüfbefehle, erwartete Ablehnungen |
| **Abhängigkeiten** | Was vorher vorliegen muss (siehe „Was wirklich wartet“) |
| **Verantwortung und Freigaben** | Wer umsetzt (genau einer), wer prüft, welche Entscheidung Kaans noch offen ist |

Bereits Entschiedenes steht im Auftrag als entschieden, Offenes als offene Entscheidung.
Beispiel: „Portal auf Vercel, Kern auf eigenem Server, PostgreSQL“ (entschieden,
`docs/DECISIONS.md`); „Zugriffsrechte je Nutzergruppe im Portal“ (offen, Kaan). So
fragt kein Werkzeug Kaan zweimal dasselbe.

### Was wirklich wartet

Ein klarer Folgeauftrag beginnt sofort, auch wenn die Wissenspflege zum letzten Merge
noch läuft. Er wartet nur auf echte Voraussetzungen:

- **DB-Code:** das gemergte `docs/DATABASE.md` mit Kaans Technikentscheidung, und vor
  dem Merge die Gemini-Freigabe im PR.
- **Ausnahmen:** vor dem Merge Gemini-Sicherheits-Review und Kaans OK.
- **Aufbau auf einem offenen PR:** dessen Merge, eingetragen unter „Abhängigkeiten“.

Richtwert für den Anfang: je Implementierer ein Auftrag in Umsetzung und ein
vorbereiteter Folgeauftrag; Prüfungen laufen am offenen PR.

### Rückmeldung an Kaan

Nach jedem Arbeitsblock bekommt Kaan vier Zeilen, am Anfang der Übergabe (Prompt 2 in
`docs/HANDOVER.md`):

```text
Fertig: <Ergebnis mit PR-Link>
Geprüft: <Tests und Reviews auf dem aktuellen Head-SHA>
Blockiert: <keiner | Grund, und wer ihn lösen kann>
Deine Entscheidung: <keine | Auswahl mit Empfehlung>
```

Freigegebene Arbeit läuft innerhalb ihres Umfangs weiter. Eine Rückfrage an Kaan kommt
nur, wenn eine Entscheidung fehlt oder der Umfang sich ändern muss. Die Ausnahmen und
die Freigaben für Merge und Deployment bleiben dabei unverändert.

### Wissensblock (Pflicht in jeder PR-Beschreibung von Codex)

```text
## Für die Wissensdatenbank
- Was ist jetzt anders: <1–3 Sätze>
- Was haben wir gelernt: <nichts Neues | 1–2 Sätze>
- Beleg: <Head-SHA, Testname oder Befehl mit Ergebnis>
- Entscheidungen: <keine | Entscheidung, Begründung, wer>
- Geänderte Grenzen (SECURITY.md): <keine | welche>
- Messwerte: <Anzahl Tests, Ergebnis der Demo>
- Was bleibt offen: <nichts | Punkt, Zustand Vorschlag oder offene Entscheidung>
- Nächster Schritt: <Vorschlag>
```

## Konflikte zwischen den Plattformen (Claude Code entscheidet)

Ein Konflikt liegt vor, wenn zwei Werkzeuge sich widersprechen, zum Beispiel:

- Gemini lehnt einen PR ab, Codex hält den Befund für falsch;
- ein Dokument widerspricht dem Code oder einem anderen Dokument (etwa `STATUS.md`,
  `DATABASE.md` und `SECURITY.md` untereinander);
- unklar ist, wem eine Datei oder eine Aufgabe gehört.

Jedes Werkzeug meldet ihn mit einem **KONFLIKT**-Block im PR oder als Issue. Kaan gibt
ihn an Claude Code weiter:

```text
KONFLIKT GeniusNew
Wer gegen wen: <z. B. Codex gegen Gemini>
Wo: <PR/Issue/Datei:Zeile>
Position A: <1–3 Sätze mit Beleg>
Position B: <1–3 Sätze mit Beleg>
Was blockiert ist: <PR, Merge, Aufgabe>
```

Claude Code entscheidet anhand von Code, Tests, `AGENTS.md` und `docs/DECISIONS.md` und
begründet die Entscheidung im PR oder Issue. **Jede Entscheidung endet mit einem fertigen
Prompt für jedes betroffene Werkzeug**, den Kaan nur noch kopiert: was zu tun ist, in
welcher Datei, bis wann es als erledigt gilt. Die Entscheidung ist für alle Werkzeuge
verbindlich; Gemini trägt sie in `docs/DECISIONS.md` ein. Wo eine Datei korrigiert werden
muss, darf Claude Code sie selbst ändern, auch die von Gemini (Überschreibrecht). Nicht
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

**Beratung ohne Feststecken.** Auch ohne Hilferuf kann jedes Werkzeug oder Kaan Claude
Code zu einem konkreten offenen Punkt fragen: zwei vorgeschlagene Strukturen haben
unterschiedliche Folgen, oder eine Änderung berührt eine Prozess- oder
Sicherheitsgrenze. Widersprechen sich zwei Reviews, ist das ein KONFLIKT (oben). Die
Anfrage enthält immer eine konkrete Frage; die Umsetzung bleibt beim Verantwortlichen
und läuft danach weiter.

```text
BERATUNG GeniusNew
Wo: <Issue/PR>   Head-SHA: <sha>
Optionen: <A und B, je 1–2 Sätze mit Folgen>
Anforderungen: <was gilt, mit Quelle>
Frage an Claude: <z. B. „Welche Option passt zu unseren Anforderungen, und warum?“>
```

## Geräte

| Gerät | Arbeit |
| --- | --- |
| iPad Pro | ChatGPT, Gemini, NotebookLM, GitHub (auch Copilot-Aufträge über GitHub Mobile), OneDrive/OneNote, Reviews |
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
eigenen Kopien bearbeiten: Was dort falsch ist, korrigiert Gemini in der
Wissensdatenbank, danach werden die Dateien neu abgelegt. Die Git-Arbeitskopie liegt
**außerhalb** des synchronisierten OneDrive-Ordners, damit sich Synchronisation und Git
nicht in die Quere kommen. In OneNote ein Notizbuch `GeniusNew` mit den Abschnitten
`Start`, `Entscheidungen`, `Reviews` und `Ideen`; Aufgaben und Freigaben werden aus
GitHub verlinkt. Quellenpakete für NotebookLM oder Gemini tragen Datum und vollen
Commit-SHA; ein Merge aktualisiert statische Uploads nicht von selbst.
