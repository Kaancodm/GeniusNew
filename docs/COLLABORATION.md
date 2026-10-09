# Zusammenarbeit der Werkzeuge

Ziel: So wenig Abstimmung wie möglich. Jede Sache hat **genau einen Verantwortlichen**:

- **Code und Regeln:** der `main`-Zweig dieses Repositories. Codex setzt um und mergt.
- **Wissen — Projektstand:** `docs/STATUS.md`, geführt von **Claude Code** (seit
  04.10.2026); NotebookLM gibt daraus Auskunft.
- **Wissen — Regeln und Entscheidungen:** `docs/COLLABORATION.md` (dieses Dokument) und
  `docs/DECISIONS.md` gehören **ausschließlich Claude Code**.
- **Datenbank im Code:** **Kaan entscheidet** Ziele, Architektur und offene Fragen;
  **ChatGPT erstellt und pflegt `docs/DATABASE.md`** in seinem Auftrag (Schema,
  Migrationen, Persistenzmodell); **Claude Code** reviewt Entwurf und Code
  sicherheitstechnisch, ohne selbst zu entwerfen; **Codex** implementiert den
  freigegebenen Code.
- **Neue Werkzeuge, Server und Infrastruktur:** **ChatGPT** (siehe eigener Abschnitt
  unten).
- **Ordnung, Struktur und Konflikte zwischen den Plattformen:** **Claude Code**, mit
  Überschreibrecht gegenüber allen Werkzeugen.
- **Entscheidungen:** Kaan. Kaan steht über allen, auch über Claude Code.

**Seit 27.09.2026 (Kaan):** `docs/COLLABORATION.md` und `docs/DECISIONS.md` ändert nur
noch Claude Code; kein anderes Werkzeug, auch nicht ChatGPT im Auftrag. Für die
Datenbank gilt die überarbeitete Fassung von Kaans Entscheidung vom selben Tag: Kaan
entscheidet Ziele und offene Architekturfragen, schreibt `docs/DATABASE.md` aber nicht
mehr selbst — das übernimmt **ChatGPT** in seinem Auftrag (siehe „Die Datenbank im
Code“ unten). Ein vorheriger Entwurf (PR #64), der Gemini vollständig durch Claude als
Head der Datenbank ersetzt hätte, ist weiterhin **nicht übernommen** — er diente nur
als Übergabestand. Siehe `docs/DECISIONS.md` für beide Begründungen.

**Seit 04.10.2026 (Kaan), schlankere Abläufe:** `docs/STATUS.md` führt Claude Code statt
Gemini. Gemini prüft nur noch, über die **Antigravity-CLI**, die **Abacus-CLI** oder die
Gemini CLI (Format „Gemini-Review“ unten), weil die GitHub-App Gemini Code Assist in diesem Repository nie
ein Review abgegeben hat. **Keine Stapel-PRs:** Jeder PR basiert auf `main`. Begründung:
`docs/DECISIONS.md`.

## Rollen

| Wer | Macht | Macht nicht |
| --- | --- | --- |
| **Kaan** | entscheidet Produktziele, Architektur und Ausnahmen (Portal/Kern-Betriebsort, DB-Technologie, neue Dependencies, `SECURITY.md`-Grenzen, Tags, Deployment-/Produktionsfreigaben); gibt ChatGPT den Auftrag für DB-/Infra-Entwürfe; entscheidet offene Architekturfragen und gibt den DB-Entwurf frei; mergt Claudes und ChatGPTs eigene PRs | `docs/DATABASE.md` selbst schreiben zu müssen — das übernimmt ChatGPT in seinem Auftrag |
| **Gemini Pro** | **Prüfer:** ist **Pflicht-Zweitmeinung** bei den Ausnahmen und prüft Designs vorab bei Prozessgrenzen und Kryptografie, auf Anforderung auch andere PRs. Läuft über die **Antigravity-CLI**, die **Abacus-CLI** (im Terminal, per Termius auch vom iPhone) oder die Gemini CLI; das Ergebnis steht als PR-Kommentar im Format „Gemini-Review“ (unten), Maßstab `.gemini/styleguide.md`. Kontext: `GEMINI.md` | irgendeine Datei im Repository ändern (auch `docs/STATUS.md` nicht mehr), die Datenbank entwerfen oder freigeben |
| **NotebookLM** | **Auskunft für alle:** beantwortet Fragen zu Stand, Entscheidungen, Grenzen und Datenbank-Schema (aus `docs/DATABASE.md`) aus seinen Quellen, jede Antwort mit Quellenangabe; Quellen strikt getrennt von ChatGPTs eigenem Recherche-Notebook | Entscheidungen treffen, Inhalte ohne Quelle |
| **Codex** (ChatGPT Pro) | setzt **Kerncode** um, implementiert den von Kaan freigegebenen DB-Code, und **mergt selbst**: ein Thema pro PR, Branch `codex/<thema>`, mit einem **Wissensblock** in der PR-Beschreibung (siehe unten) | `docs/STATUS.md`, `docs/COLLABORATION.md`, `docs/DECISIONS.md` und `docs/DATABASE.md` ändern, einen DB-Code-PR ohne `Claude DB Review: APPROVED` mergen, Ausnahmen ohne Kaans OK mergen, Tags anlegen |
| **ChatGPT** (Chat) | **neue Werkzeuge, laufende Server-Pflege, Dashboards, Monitoring, externe Integrationen** (eigener Abschnitt unten), **inklusive technischer Architekturentwürfe im Infra-/DB-Bereich und der Erstellung/Pflege von `docs/DATABASE.md` im Auftrag von Kaan**; plant, formuliert Prompts, erklärt | Kerncode (`geniusnew/`, `tests/`, `scripts/refusals.py`, `scripts/demo.*`, `schemas/`), Core-Tests, `SECURITY.md` eigenständig ändern, `requirements.txt`, Governance-/Wissensdateien (siehe unten) ändern; Änderungen ohne Kaans Auftrag; den eigenen DB-Entwurf sicherheitstechnisch freigeben; selbst mergen |
| **GitHub Copilot Pro** | Vervollständigung im Editor; Review jedes PRs nach der Checkliste in `.github/copilot-instructions.md` | eigene PRs ohne Auftrag |
| **Microsoft 365 Copilot** | Berichte, E-Mails, Folien aus dem OneDrive-Ordner `GeniusNew` | Inhalte erfinden, die dort nicht stehen |
| **Kiro-CLI** | **unabhängige Zweitprüfung für Claudes eigenen Code**: prüft Claude-PRs, die Code oder Skripte ändern, nur lesend im Projektverzeichnis, auf Anforderung von Kaan; Ergebnis als PR-Kommentar im Format „Review GeniusNew“ (unten) | Dateien ändern, Befehle mit Schreibwirkung ausführen, ein Gemini-Pflichtreview oder `Claude DB Review` ersetzen |
| **Claude Code** | **Ordnung, Struktur und Konfliktlöser zwischen den Plattformen, mit Überschreibrecht:** besitzt `docs/COLLABORATION.md`, `docs/DECISIONS.md` und `docs/STATUS.md` sowie die übrigen Regeln (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.gemini/*`, `docs/HANDOVER.md`); entscheidet Konflikte zwischen Werkzeugen verbindlich; **unabhängiger Security-Reviewer**: reviewt ChatGPTs `docs/DATABASE.md`-Entwurf und Codex' DB-Code-PRs sicherheitstechnisch, ebenso Server-/Deployment-/Netzwerk-Sicherheitsfragen; darf jede Datei korrigieren, auch die anderer Werkzeuge, wenn sie den Regeln oder dem Code widerspricht; **hilft, wenn Codex feststeckt** | Kaans Entscheidungen oder die Ausnahmen überstimmen; die eigene Arbeit selbst freigeben; `docs/DATABASE.md` selbst entwerfen oder Head der Datenbank sein; regulärer Kerncode-Implementierer sein; eigene PRs selbst mergen |

## Die Wissensdatenbank

**`docs/STATUS.md`** (Stand, Grenzen, nächste Schritte), **`docs/COLLABORATION.md`** und
**`docs/DECISIONS.md`** (jede Entscheidung mit Datum, Begründung und Quelle) gehören
**Claude Code**; NotebookLM gibt daraus Auskunft. Zusammen sind sie das Gedächtnis des
Projekts; niemand sonst schreibt sie.

**Quellen in NotebookLM** (Notebook „GeniusNew“): `docs/STATUS.md`,
`docs/DECISIONS.md`, `docs/DATABASE.md`, `SECURITY.md`, `docs/ROADMAP-V01.md`,
`docs/COLLABORATION.md`, `AGENTS.md`. Das Repository ist aktuell (GitHub-Stand 07.10.2026) **privat**: Raw-Links auf
`raw.githubusercontent.com` liefern ohne Anmeldung HTTP 404 und sind **kein** Verfahren.
Der Weg sind ausdrücklich ausgewählte Datei-Uploads (nächster Abschnitt).

### NotebookLM-Quellenpaket (Verfahren für das private Repository)

Die Beschreibung autorisiert keinen tatsächlichen Upload; jeder Upload ist Kaans Handlung.

1. Mit dem vorhandenen, autorisierten GitHub-Zugang arbeiten; kein neuer Zugang.
2. Einen exakten Commit auf `main` wählen (voller SHA) und **ausschließlich** diese sieben
   Dateien aus ihm exportieren: `docs/STATUS.md`, `docs/DECISIONS.md`,
   `docs/DATABASE.md`, `SECURITY.md`, `docs/ROADMAP-V01.md`, `docs/COLLABORATION.md`,
   `AGENTS.md`. Nie das vollständige Repository, `.git`, Secrets, Logs oder DB-Dateien.
3. Vor der Übertragung jede Datei lesen. Bei sensiblem oder unklarem Inhalt stoppen und
   Kaan fragen. Jede Redaktion ausdrücklich im Paketprotokoll kennzeichnen.
4. Pro Paket dokumentieren (im PR oder der Übergabe, nie mit Zugangsdaten):

   | Datum | Voller Commit-SHA | Originalpfad | SHA-256 der hochzuladenden Datei | Redaktion |
   | --- | --- | --- | --- | --- |

   Der SHA-256 gilt für die Datei, wie sie tatsächlich hochgeladen wird (`sha256sum`).
5. Kaan lädt die geprüften MD-/TXT-Dateien über „Quellen hinzufügen“ in das
   Projekt-Notebook hoch. Das Notebook wird nicht öffentlich geteilt; bestehende
   Berechtigungen werden nicht erweitert.
6. Nach relevanten Merges neu exportieren, neu prüfen und die betroffenen Quellen
   ersetzen. Statische Uploads aktualisieren sich nicht durch Git.
7. Das Repository bleibt maßgeblich. Eine fehlende Aussage im Notebook beweist nicht,
   dass eine Entscheidung nie getroffen wurde.

**Verboten:** Tokens in URLs, temporäre Raw-Token-Links, öffentliche Mirrors oder Gists,
Freigabelinks und Authentifizierungs-Proxys.

**Wer fragt wen:** Jedes Werkzeug und Kaan fragen bei Wissensfragen zuerst NotebookLM,
also „Was wurde zu X entschieden?“, „Warum ist Y so?“ oder „Was ist der nächste
Schritt?“. Steht es dort nicht, ist es noch nicht entschieden. Dann geht die Frage an
Kaan; Claude Code trägt die Antwort in `docs/DECISIONS.md` ein und spiegelt sie bei
Bedarf in `docs/STATUS.md`.

**Pflege nach Merges** (gesammelt, höchstens ein PR am Tag):
1. **Claude Code** überträgt die Wissensblöcke gemergter PRs nach `docs/STATUS.md` und
   neue Entscheidungen und Konfliktergebnisse nach `docs/DECISIONS.md`, auf einem Branch
   `claude/wissen-<datum>`; ein solcher PR ändert nur diese beiden Dateien. Zahlen
   (Tests, Angriffe der Demo, Module im Refusal-Guard) kommen nur aus einem Wissensblock
   oder einer Befehlsausgabe, nie geschätzt. Kaan mergt (siehe „Konflikte“ unten).
2. Kaan aktualisiert danach die Quellen in NotebookLM. Widersprüche zwischen den
   Dokumenten meldet jedes Werkzeug als Issue.

## Die Datenbank im Code (Entwurf: ChatGPT im Auftrag von Kaan)

**Wozu:** Die Datenbank ist die **Grundlage für das Portal**. Das Portal ist die
Web-Oberfläche, über die Nutzer Aufträge stellen, ihren Verlauf sehen und Freigebende
Approvals erteilen. Das alte `bürgerbüro/portal` ist nach `docs/MIGRATION-MATRIX.md`
nur Lesequelle (REBUILD): Es wird neu gebaut, nicht kopiert.

**Was hineinkommt:** Job- und Annahme-Ledger, wartende Approval-Jobs, die Audit-Kette
und der Anker-Zustand. Damit übersteht der Dienst einen Neustart, ohne etwas doppelt
anzunehmen oder zu vergessen. Für das Portal kommen hinzu: Nutzer und ihre Zuordnung zu
Principals, API-Key-Digests, Sitzungen, der Auftragsverlauf je Nutzer, die Rolle der
Freigebenden und Quoten.

**Grundsatz:** Wer entwirft, gibt nicht selbst frei. Wer implementiert, entscheidet
nicht selbst über die Architektur. Kaan bleibt die Entscheidungsinstanz.

**Workflow:**

```text
Kaan entscheidet Ziel → ChatGPT entwirft → Claude Security Review →
Kaan entscheidet offene Punkte → Codex implementiert →
Claude DB Review am exakten Head → CI → ggf. Kaan-Gates → Merge
```

**Reihenfolge:**
1. **Ziele (Kaan):** Kaan gibt Ziele und verbindliche Entscheidungen vor — Betriebsort
   von Portal und Kern (bereits entschieden: Portal auf Vercel, Kern auf eigenem
   Server, `docs/DECISIONS.md` 26.09.2026), Datenbank-Technologie (bereits entschieden:
   PostgreSQL mit `psycopg`, `docs/DECISIONS.md` 26.09.2026) und beauftragt ChatGPT mit
   dem Entwurf.
2. **Entwurf (ChatGPT):** ChatGPT schreibt `docs/DATABASE.md` auf einem Branch
   `chatgpt/db-design`, als Draft-PR. Inhalt: Schema und Tabellen (auch für das
   Portal), Migrationen, Persistenzmodell, Fehler-/Recovery-Verhalten bei
   beschädigtem Speicher, Verbindungskonzept, die Trennung von Audit-Kette und
   Anker-Speicher, Portal- versus Core-Datenhaltung, Backup-/Restore-Design und
   Betriebsanforderungen.
3. **Sicherheits-Review (Claude Code):** Claude führt einen unabhängigen
   Sicherheitsreview des Entwurfs durch, gegen die harten Vorgaben unten
   (Anker-Trennung, fail closed, keine Zugangsdaten im Repo), und meldet Befunde im
   PR. Claude entwirft dabei nichts selbst und gibt auch nichts frei — das bleibt
   Kaans Schritt.
4. **Freigabe (Kaan):** Kaan entscheidet offene Architekturfragen und gibt den Entwurf
   frei; er mergt diesen ChatGPT-PR selbst (ChatGPT mergt nicht selbst, siehe unten).
   Eine AI-Freigabe braucht der Entwurf-PR dafür nicht, anders als die
   Umsetzungs-PRs unten.
5. **Umsetzung (Codex), je ein PR:** (a) Ledger mit Ablauf, (b) wartende Jobs,
   (c) Audit-Kette, (d) Anker-Zustand, danach die Portal-Tabellen und (e) das Portal
   selbst — jeweils nach dem freigegebenen Entwurf. Jeder DB-Code-PR braucht
   zusätzlich zur grünen CI ein **Claude-DB-Review auf dem exakten aktuellen
   Head-SHA**: `Claude DB Review: APPROVED` oder `Claude DB Review: CHANGES
   REQUESTED`. `CHANGES REQUESTED` oder eine Freigabe für einen älteren Head-SHA
   blockiert den Merge. Da Claude hier Codex' Code gegen Kaans freigegebenen Entwurf
   prüft, nicht die eigene Arbeit, ist das keine Selbstfreigabe.
6. **Doku (NotebookLM):** `docs/DATABASE.md` wird Quelle im Notebook, getrennt von
   ChatGPTs eigenem Recherche-Notebook; Fragen zum Schema gehen an NotebookLM.

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

**Ein DB-Code-PR** ist jeder PR von Codex, der Speicher-Code, Schema oder Migrationen
ändert; er braucht das Gate aus Schritt 5. `docs/DATABASE.md` selbst erstellt und
pflegt ChatGPT im Auftrag von Kaan (Schritt 2); das ist kein DB-Code-PR und braucht
kein `Claude DB Review: APPROVED`, sondern Claudes Sicherheitsreview und Kaans
Freigabe (Schritte 3–4).

## ChatGPT: neue Werkzeuge, Server und Infrastruktur

**Wozu:** Alles rund um Betrieb, Werkzeuge und externe Integrationen, ohne die
Kernlogik zu berühren. Solange kein Server existiert, ist das Setup-Arbeit; sobald
einer läuft, ist es laufende Pflege.

**Aufgaben:**
- **Server-Pflege**, laufend: systemd-Units, Reverse-Proxy, TLS, Backups,
  Restore-Abläufe, sobald ein Server existiert.
- **Neue Werkzeuge, Dashboards und Monitoring** — eine offene Kategorie, zum Beispiel
  Warp, spätere Monitoring- oder Dashboard-Tools.
- **Externe Integrationen:** PostgreSQL-/DB-Infrastruktur, Vercel-/Hosting-Integration,
  OneDrive-Ablage, NotebookLM-Setup.
- **Technische Architekturentwürfe im Infra-/DB-Bereich, inklusive `docs/DATABASE.md`**
  im Auftrag von Kaan (siehe „Die Datenbank im Code“ oben): Schema, Migrationen,
  Persistenzmodell, Fehler-/Recovery-Verhalten, Verbindungskonzept, Backup-/
  Restore-Design, Betriebsanforderungen. ChatGPT entwirft; freigeben tut Kaan, nach
  Claudes Sicherheitsreview — ChatGPT gibt den eigenen Entwurf nicht selbst frei.
- **NotebookLM-Lesezugriff** wie die anderen Werkzeuge, dazu ein **eigenes
  Recherche-Notebook** nur für ChatGPT — strikt getrennt von den projektweiten
  NotebookLM-Quellen und kein Governance-Dokument.

**Branch und Merge:** eigener Branch `chatgpt/<thema>` (für die Datenbank:
`chatgpt/db-design`), Draft-PR, mit Wissensblock. ChatGPT mergt nicht selbst; Kaan
mergt, nach Copilot-Review und, bei Deploy-, Netz- oder DB-Design-Themen, nach Claudes
Sicherheitsreview.

**Darf nicht ändern:** `geniusnew/` (Kerncode), Core-Tests, `scripts/refusals.py`,
`scripts/demo.*`, `SECURITY.md` eigenständig, `requirements.txt`, sowie alle
Governance- und Wissensdateien (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.gemini/*`,
`.github/copilot-instructions.md`, `docs/COLLABORATION.md`, `docs/DECISIONS.md`,
`docs/STATUS.md`, `docs/HANDOVER.md`). ChatGPT gibt außerdem nie den eigenen
DB-Entwurf sicherheitstechnisch frei und mergt nie eigene PRs.

## Der Ablauf eines Themas

1. Kaan wählt den nächsten Schritt; die Auskunft dazu gibt NotebookLM. Kerncode geht an
   Codex (Prompt 1 aus `docs/HANDOVER.md`), Tools/Server/Infrastruktur/DB-Design an
   ChatGPT, Regel- oder Konfliktfragen an Claude Code.
2. Der Umsetzer öffnet einen Draft-PR auf dem eigenen Branch-Präfix
   (`codex/<thema>`, `chatgpt/<thema>` oder `claude/<thema>`) und schreibt den Plan in
   die PR-Beschreibung. **Der PR basiert auf `main`, nie auf einem anderen PR-Branch:**
   Braucht das Thema einen noch offenen PR, wartet es auf dessen Merge oder gehört in
   denselben PR. Ändert das Thema eine Prozessgrenze oder Kryptografie, fordert Codex
   im PR eine Design-Vorprüfung durch Gemini an, bevor Code entsteht.
3. **Copilot reviewt jeden PR.** Ein **Gemini-Review** gibt es dort, wo es Pflicht ist
   (Ausnahmen, Design-Vorprüfung), oder wenn Kaan es anfordert: Der Umsetzer fragt es im
   PR an, Kaan (oder ChatGPT in seinem Auftrag) startet es über die Antigravity-CLI oder
   die Abacus-CLI und stellt das Ergebnis als PR-Kommentar ein.
4. **Codex mergt eigene PRs selbst**, sobald die CI grün ist (`contracts`), der
   Wissensblock ausgefüllt ist und kein blockierender Review-Befund offen ist.
   Blockierend sind Gemini-Befunde der Stufe **Critical** oder **High** und
   Copilot-Befunde zu den Punkten der Checkliste. Kaans ausdrückliches OK braucht es
   nur für die Ausnahmen: eine geänderte Grenze aus `SECURITY.md` (offen gehaltener
   Test umgekehrt) und Tags. Neue Abhängigkeiten wählt der Implementierer begründet
   selbst und hält die Hash-Pins ein. Bei den Ausnahmen muss
   vorher ein Gemini-Sicherheits-Review im PR stehen. **DB-Code-PRs** brauchen
   zusätzlich `Claude DB Review: APPROVED` am exakten Head-SHA (siehe oben). **PRs von
   ChatGPT und Claude mergt Kaan**, keines der beiden mergt selbst. **Claude-PRs, die Code
   oder Skripte ändern,** brauchen vorher ein unabhängiges Review ohne offenen Befund der
   Stufe Critical/High: ein Codex-Review oder ein Kiro- oder Gemini-Review im Format unten.
   Claude gibt eigenen Code nie selbst frei.
5. Claude Code überträgt den Wissensblock in `docs/STATUS.md` und trägt Entscheidungen
   und Konfliktergebnisse in `docs/DECISIONS.md` ein (gesammelt, siehe „Pflege nach
   Merges“).

**Kommunikation läuft über den PR**, nicht über Kopieren zwischen Chats. Plan,
Rückfragen an Gemini oder Claude, Reviews, Begründungen und der Wissensblock stehen im
PR. Jedes Werkzeug und Kaan sehen so denselben Stand.

### Wissensblock (Pflicht in jeder PR-Beschreibung von Codex, ChatGPT und Claude)

```text
## Für die Wissensdatenbank
- Was ist jetzt anders: <1–3 Sätze>
- Entscheidungen: <keine | Entscheidung, Begründung>
- Geänderte Grenzen (SECURITY.md): <keine | welche>
- Messwerte: <Anzahl Tests, Ergebnis der Demo>
- Nächster Schritt: <Vorschlag>
```

### Gemini-Review (Format, seit 04.10.2026)

Ein Gemini-Review zählt, wenn es als PR-Kommentar so aussieht. Ein Kiro-Review nutzt
dasselbe Format mit der Kopfzeile `Review GeniusNew`. Es gilt für genau den
genannten Head-SHA; ein späterer Push, der die geprüften Bereiche ändert, braucht ein
neues Review. Ein reiner Merge von `main` braucht keins, wenn der Umsetzer im PR belegt,
dass der Diff gegenüber `main` in diesen Bereichen gleich geblieben ist.

```text
Gemini-Review GeniusNew
Werkzeug und Modell: <z. B. Antigravity-CLI, gemini-3.1-pro-high>
PR und Head-SHA: <#nummer, voller SHA>
Bereiche: <A: … PASS|FAIL; B: … PASS|FAIL>
Befunde: <keine | Schweregrad, Datei:Zeile, Beleg>
```

Critical oder High blockiert den Merge (`.gemini/styleguide.md`). Installiert Kaan
später die GitHub-App Gemini Code Assist, zählen auch ihre Reviews.

**Modell:** Das Pflicht-Sicherheitsreview bei den Ausnahmen (`SECURITY.md`-Grenze,
Signaturrollen) braucht ein Gemini-**Pro**-Modell, gleich über
welches Werkzeug. Ein Flash-Modell reicht für die Design-Vorprüfung und freiwillige
Reviews. Das Werkzeug arbeitet nur lesend im Projektverzeichnis (Plan-Modus, kein Zugriff
außerhalb des Checkouts); Zugangsschlüssel von Abacus oder Antigravity liegen nur auf
dem Gerät, nie im Repository.

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
verbindlich; Claude Code trägt sie selbst in `docs/DECISIONS.md` ein. Wo eine Datei
korrigiert werden muss, darf Claude Code sie selbst ändern, auch die von Gemini
(Überschreibrecht). Nicht überschreiben darf Claude Code Kaans Entscheidungen und die
Ausnahmen (Grenze aus `SECURITY.md`, Tags, Kaans Architektur- und
Freigabeentscheidungen zur Datenbank). Das bleibt Kaans Sache. Claudes eigene PRs mergt
Kaan.

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
| iPad Pro | ChatGPT, Gemini, NotebookLM, GitHub (auch Copilot-Aufträge über GitHub Mobile), OneDrive/OneNote, Reviews |
| Laptop (Linux/WSL) | Codex, Git-Checkout, lokale Tests; ein Implementierer je Branch, Tests nacheinander |
| Server (sobald vorhanden) | ChatGPT pflegt ihn laufend (systemd, Backups, Restore); Zugangsdaten nur in privater Doku |

Zugriff vom iPad auf den Laptop über SSH (z. B. mit Tailscale) in eine WSL-Sitzung mit
`tmux`. Hostnamen, Zugangsdaten und private Adressen gehören nur in die private
Zugangsdokumentation, nie in dieses Repository.

**Aufgaben** entstehen über das Issue-Formular „Begrenzte Aufgabe“
(`.github/ISSUE_TEMPLATE/agent-task.yml`), **PRs** über
`.github/PULL_REQUEST_TEMPLATE.md` (mit Wissensblock). Für Copilot-Cloud-Aufträge
liegt eine Umgebungsvorlage in `docs/setup/copilot-setup-steps.yml`. Aktiv wird sie
erst als `.github/workflows/copilot-setup-steps.yml` auf `main`; das ist Kaans
Schritt, weil es Workflow-Schreibrechte braucht.

## Ablage in OneDrive (für Microsoft 365 Copilot)

Ordner `GeniusNew` mit genau vier Dateien, die nach jeder Pflege der Wissensdatenbank
ersetzt werden: `STATUS.md`, `DECISIONS.md`, `SECURITY.md`, `ROADMAP-V01.md`. Keine
eigenen Kopien bearbeiten: Was an `STATUS.md` oder
`DECISIONS.md` falsch ist, korrigiert Claude Code; danach werden die Dateien neu
abgelegt. Die Git-Arbeitskopie liegt **außerhalb** des synchronisierten
OneDrive-Ordners, damit sich Synchronisation und Git nicht in die Quere kommen. In
OneNote ein Notizbuch `GeniusNew` mit den Abschnitten `Start`, `Entscheidungen`,
`Reviews` und `Ideen`; Aufgaben und Freigaben werden aus GitHub verlinkt. Quellenpakete
für NotebookLM, Gemini oder Claude Code tragen Datum und vollen Commit-SHA; ein Merge
aktualisiert statische Uploads nicht von selbst.
