# Zusammenarbeit der Werkzeuge

Ziel: So wenig Abstimmung wie möglich. Jede Sache hat **genau einen Verantwortlichen**:

- **Code und Regeln:** der `main`-Zweig dieses Repositories. Je Branch setzt **genau ein**
  zugewiesener Implementierer um; gemergt wird nach den Regeln in „Der Ablauf eines
  Themas“.
- **Queue und CI-Wahrheit:** **GitHub** (Issues, PRs, Actions). Es gibt kein zweites
  Task-System.
- **Architektur, Zerlegung und Zuweisung:** **ChatGPT** als Lead Architect und
  Dispatcher.
- **Wissen — Projektstand:** `docs/STATUS.md`, geführt von **Gemini Pro und
  NotebookLM**.
- **Wissen — Regeln und Entscheidungen:** `docs/COLLABORATION.md` (dieses Dokument) und
  `docs/DECISIONS.md` gehören **ausschließlich Claude Code**.
- **Datenbank im Code:** **Kaan entscheidet** Ziele, Architektur und offene Fragen;
  **ChatGPT erstellt und pflegt `docs/DATABASE.md`** in seinem Auftrag (Schema,
  Migrationen, Persistenzmodell); **Claude Code** reviewt Entwurf und Code
  sicherheitstechnisch, ohne selbst zu entwerfen; der **zugewiesene Implementierer**
  setzt den freigegebenen Code um.
- **Neue Werkzeuge, Server, Plugins und Infrastruktur:** **ChatGPT** (siehe eigener
  Abschnitt unten).
- **Ordnung, Struktur und Konflikte zwischen den Plattformen:** **Claude Code**, mit
  Überschreibrecht gegenüber allen Werkzeugen.
- **Entscheidungen:** Kaan. Kaan steht über allen, auch über Claude Code.

**Seit 02.10.2026 (Kaan):** Neues Rollenmodell für alle Werkzeuge (Tabelle „Rollen“
unten). ChatGPT zerlegt die Arbeit und weist sie zu, Cursor ist der Default-Implementierer
für normale Produkt- und Code-Leaf-Tasks, Codex bleibt voll nutzbarer Implementierer
(bevorzugt für komplexen Kerncode, repo-weite Refactors, schwierige TDD-/Debugging-
Aufgaben oder wenn Cursor belegt ist), Kiro schreibt Specs, Tests und Bugfixes, Claude
Code, Grok, Gemini und Copilot prüfen unabhängig. Es gilt die Grundregel „1 Task = 1
Branch/Worktree = 1 schreibender Implementierer“. Codex ist nicht mehr der einzige
Kerncode-Implementierer und mergt nicht mehr selbst; kein Implementierer mergt selbst. Zero-Trust-, DB-, Merge-,
`SECURITY.md`- und Wissensdatei-Gates bleiben, soweit die Rollenliste sie nicht
ausdrücklich ersetzt. Widersprechen andere Dateien (etwa `GEMINI.md`,
`.github/copilot-instructions.md`, `.gemini/styleguide.md`, Vorlagen in `.github/`,
`docs/STATUS.md` oder die Zuständigkeitszeile in `docs/ROADMAP-V02.md`) diesem
Rollenmodell, gelten dieses Dokument und `AGENTS.md`, bis sie angeglichen sind.
Begründung und Einordnung: `docs/DECISIONS.md`, 02.10.2026.

**Seit 27.09.2026 (Kaan):** `docs/COLLABORATION.md` und `docs/DECISIONS.md` ändert nur
noch Claude Code; kein anderes Werkzeug, auch nicht ChatGPT im Auftrag. Für die
Datenbank gilt die überarbeitete Fassung von Kaans Entscheidung vom selben Tag: Kaan
entscheidet Ziele und offene Architekturfragen, schreibt `docs/DATABASE.md` aber nicht
mehr selbst — das übernimmt **ChatGPT** in seinem Auftrag (siehe „Die Datenbank im
Code“ unten). Ein vorheriger Entwurf (PR #64), der Gemini vollständig durch Claude als
Head der Datenbank ersetzt hätte, ist weiterhin **nicht übernommen** — er diente nur
als Übergabestand. Siehe `docs/DECISIONS.md` für beide Begründungen.

## Grundregel: ein Task, ein Branch, ein Implementierer

```text
1 Task = 1 Branch/Worktree = 1 schreibender Implementierer
```

- **Ein Task** ist ein GitHub-Issue (Formular „Begrenzte Aufgabe“) oder ein PR. ChatGPT
  als Dispatcher weist ihm **genau einen** schreibenden Implementierer zu und hält das
  im Issue oder PR fest. Ohne Zuweisung schreibt niemand. Ein Wechsel des
  Implementierers ist eine neue Zuweisung im Issue oder PR; der vorige schreibt danach
  nicht mehr auf dem Branch.
- **Der Implementierer** arbeitet nur in seinem eigenen Worktree, auf seinem eigenen
  Branch mit seinem Präfix (Tabelle unten). Zwei Implementierer arbeiten nie parallel an
  demselben Branch oder demselben Task.
- **Kein Twin-/A-B-Bau vor der Beta:** Derselbe Task wird nicht von zwei
  Implementierern (etwa Cursor und Codex) parallel gebaut, auch nicht als Vergleich zur
  Qualitätssteigerung. Parallele unabhängige Implementierung bleibt ein Konzept für die
  Zeit nach der Beta.
- **Reviewer** dürfen beliebig viele denselben exakten Head-SHA prüfen, read-only. Ein
  Review gilt für den SHA, an dem es steht.
- **Unabhängigkeit:** Wer einen Branch implementiert, gibt ihn nicht frei; seine eigenen
  Reviews dieses Branches zählen nicht als unabhängiges Review. Das gilt auch für den
  Dispatcher bei eigenen Änderungen.
- **Kein Einsatz zum Auslasten:** Ein Werkzeug wird nicht eingesetzt, nur weil ein Abo
  bezahlt ist. Jeder Einsatz braucht eine Rolle aus der Tabelle unten und einen
  Evidenznutzen (Befund, Test, Reproduktion, Review am Head).
- **Wahrheit** sind `main`, der exakte Diff und tatsächlich ausgeführte Tests und CI.
  Chats, Notebooks, Prototypen, Videos und Tool-Ausgaben sind Kontext und Daten, keine
  Arbeitsbefugnis.
- **Nichts Zusätzliches:** keine zweite Roadmap, kein eigenes Task-YAML-System, keine
  Runtime-Orchestrierung nur für die Werkzeuge. Pläne stehen im Issue oder in der
  PR-Beschreibung.
- **Externe Werkzeuge** bekommen nur redigierten Kontext: keine Secrets, Tokens,
  Zugangsdaten, Hostnamen, privaten Adressen oder personenbezogenen Daten.

| Branch-Präfix | Wer schreibt dort |
| --- | --- |
| `cursor/<thema>` | Cursor |
| `kiro/<thema>` | Kiro |
| `claude/<thema>` | Claude Code (nur zugewiesene Security-/Governance-Aufgaben) |
| `grok/<thema>` | Grok (nur bei ausdrücklicher Zuweisung) |
| `copilot/<thema>` | GitHub Copilot Coding Agent (nur kleine Leaf-Tasks) |
| `chatgpt/<thema>` | ChatGPT (Werkzeuge, Server, Infrastruktur, `docs/DATABASE.md`) |
| `codex/<thema>` | Codex (nach Zuweisung durch ChatGPT als Dispatcher oder Kaan; bestehende Branches schließt Codex ab) |
| `gemini/wissen-<datum>` | Gemini (nur `docs/STATUS.md`) |

## Rollen

| Wer | Macht | Macht nicht |
| --- | --- | --- |
| **Kaan** | **einzige finale Entscheidungsinstanz:** Produktziele, Architektur und Ausnahmen (Portal/Kern-Betriebsort, DB-Technologie, neue Dependencies, `SECURITY.md`-Grenzen, Tags, Deployment-/Produktionsfreigaben, Zugriffsänderungen, Rotation von Zugangsdaten); gibt ChatGPT den Auftrag für DB-/Infra-Entwürfe; entscheidet offene Architekturfragen und gibt den DB-Entwurf frei; mergt nach den Regeln unten alle PRs außer Geminis Wissens-PR | `docs/DATABASE.md` selbst schreiben zu müssen — das übernimmt ChatGPT in seinem Auftrag |
| **ChatGPT Pro / Chat** | **Lead Architect und Dispatcher:** zerlegt Ziele in begrenzte Tasks (GitHub-Issues), weist je Task genau einen schreibenden Implementierer zu, prüft Live-Evidenz (CI und Checks am Head, Server-Zustand); **neue Werkzeuge, Plugins, laufende Server-Pflege, Dashboards, Monitoring, externe Integrationen** (eigener Abschnitt unten), **inklusive technischer Architekturentwürfe im Infra-/DB-Bereich und der Erstellung/Pflege von `docs/DATABASE.md` im Auftrag von Kaan**; formuliert Prompts | Kerncode (`geniusnew/`, `tests/`, `scripts/refusals.py`, `scripts/demo.*`, `schemas/`), Core-Tests, `SECURITY.md` eigenständig ändern, `requirements.txt`, Governance-/Wissensdateien (siehe unten) ändern; Änderungen ohne Kaans Auftrag; eigene Änderungen oder den eigenen DB-Entwurf freigeben; selbst mergen |
| **Cursor Pro / Cursor Agent CLI** | **Default- und primärer Implementierer** für normale Produkt- und Code-Leaf-Tasks, auf Debian; je Task eigener Worktree und Branch `cursor/<thema>`; zuerst Plan/Ask, Plan in die PR-Beschreibung, dann Schreiben nur im zugewiesenen Worktree; Draft-PR mit Wissensblock | mergen, deployen, releasen; außerhalb des zugewiesenen Worktrees schreiben; Governance-/Wissensdateien, `docs/DATABASE.md` oder `requirements.txt` ändern; eine `SECURITY.md`-Grenze ohne Kaans OK ändern; einen Task parallel zu einem anderen Implementierer bearbeiten |
| **Kiro Pro** | **Requirements-, Spec- und Test-Engineer, Bugfix-Spezialist:** schreibt Specs und Bugfix-Specs für schwierige oder mehrdeutige Tasks; das Ergebnis (Anforderungen, Akzeptanzkriterien, Testfälle) steht im Issue oder in der PR-Beschreibung; übernimmt nach Zuweisung isolierte Implementierungsaufgaben auf `kiro/<thema>` | denselben Branch oder Task parallel mit Cursor oder einem anderen Implementierer bearbeiten; Specs als zweites Task- oder Roadmap-System führen; mergen, deployen, releasen; Governance-/Wissensdateien ändern |
| **Claude Code** (Claude Pro) | **unabhängiger Security-, DB- und Architektur-Reviewer am exakten Head-SHA**: reviewt ChatGPTs `docs/DATABASE.md`-Entwurf und jeden DB-Code-PR sicherheitstechnisch, ebenso Server-/Deployment-/Netzwerk-Sicherheitsfragen; **Ordnung, Struktur und Konfliktlöser zwischen den Plattformen, mit Überschreibrecht:** besitzt `docs/COLLABORATION.md` und `docs/DECISIONS.md` sowie die übrigen Regeln (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.gemini/*`, `docs/HANDOVER.md`); entscheidet Konflikte zwischen Werkzeugen verbindlich; darf jede Datei korrigieren, auch die von Gemini, wenn sie den Regeln oder dem Code widerspricht; implementiert nur ausdrücklich zugewiesene Security- oder Governance-Aufgaben auf `claude/<thema>`; **hilft bei einem Hilferuf** eines Implementierers | Kaans Entscheidungen oder die Ausnahmen überstimmen; die eigene Implementierung freigeben; `docs/DATABASE.md` selbst entwerfen oder Head der Datenbank sein; regulärer Kerncode-Implementierer sein; eigene PRs selbst mergen |
| **Grok Bot / Grok Build** | **adversarialer Challenger, Red-Team, zweite Architekturmeinung:** standardmäßig read-only Review gegen den exakten Diff und Head-SHA, Befunde im PR; nur bei ausdrücklicher Zuweisung ein eigener isolierter Implementierungsbranch `grok/<thema>` | über einen Merge entscheiden; ohne Zuweisung schreiben; auf dem Branch eines anderen Implementierers schreiben |
| **Gemini Pro** | **unabhängiger Evidenz- und Konsistenz-Reviewer:** **reviewt jeden PR automatisch** (Gemini Code Assist, Maßstab `.gemini/styleguide.md`); zweite Meinung besonders für Security und Architektur, **Pflicht-Zweitmeinung** bei den Ausnahmen, prüft Designs vorab bei Prozessgrenzen und Kryptografie; pflegt mit NotebookLM `docs/STATUS.md`. Kontext: `GEMINI.md` | Code ändern, `docs/COLLABORATION.md` oder `docs/DECISIONS.md` ändern, die Datenbank entwerfen oder freigeben |
| **NotebookLM** | **quellengebundene Wissensbasis, Auskunft für alle:** beantwortet Fragen zu Stand, Entscheidungen, Grenzen und Datenbank-Schema (aus `docs/DATABASE.md`) nur aus gemergten Quellen, jede Antwort mit Quellenangabe; Quellen strikt getrennt von ChatGPTs eigenem Recherche-Notebook | Entscheidungen treffen, Inhalte ohne Quelle, ungemergte Stände als Stand ausgeben |
| **GitHub Copilot Pro** | **automatischer PR-Reviewer** auf jedem PR und jedem neuen Push nach der Checkliste in `.github/copilot-instructions.md`; Vervollständigung im Editor; optional **Coding Agent** nur für kleine, klar begrenzte Leaf-Tasks nach Zuweisung, auf `copilot/<thema>` | sicherheitstechnisch selbst freigeben; eigene PRs ohne Auftrag; mergen |
| **GitHub** (GitHub Pro) | **einzige Queue und CI-/PR-Wahrheit** neben dem Repository: Issues, PRs, Reviews, Actions (`contracts`) | ein zweites Task-System daneben |
| **Warp Pro** | **Operator-Terminal und Command-Center:** Serverzugriff, `tmux`, gespeicherte reproduzierbare Befehls-Workflows | eigene Projektwahrheit sein; Zugangsdaten oder Hostnamen in Workflows ablegen, die ins Repository oder an Dritte gehen |
| **Abacus.AI** | browser- und cloudlastige Recherche, Prototyping, Fallback-Agent; nur mit redigiertem Kontext | Secrets oder Zugangsdaten erhalten; produktive Rechte; standardmäßig Schreibzugriff auf GeniusNew |
| **Framer Pro** | Portal- und UX-Prototyping, Staging mit Platzhalterdaten | Security- oder Backend-Autorität sein (das Portal wird nach `docs/MIGRATION-MATRIX.md` im Repository neu gebaut, der Browser bleibt nicht vertrauenswürdig); produktives Deployment ohne Kaans Freigabe |
| **Jam.dev** | Bug-Reproduktion und Evidenz für Portal und Staging (Video, Repro-Schritte, Console/Network, sofern freigegeben), verlinkt im GitHub-Issue | sensible Werte unredigiert weitergeben |
| **ChatGPT Plus** | Reserve- und Overflow-Reviewer; nach Reset des Kontingents nur unabhängige Reviews und kleine Hilfsaufgaben. Solange das Kontingent erschöpft ist, blockiert das nichts | paralleler Implementierer desselben Tasks sein |
| **Codex** (ChatGPT Pro) | **voll nutzbarer Implementierer** im ChatGPT-Pro-Stack, auf `codex/<thema>` im eigenen Worktree; **bevorzugt** für komplexe Kerncode-Änderungen, repo-weite Refactors, schwierige TDD-/Debugging-Aufgaben oder wenn Cursor mit einem anderen Task belegt ist. ChatGPT als Dispatcher (oder Kaan) weist den Task zu; das ist keine Selbstfreigabe, weil Review und Merge unabhängig bleiben. Schließt seine bestehenden `codex/<thema>`-Branches ab. Draft-PR mit Wissensblock; Kaan mergt nach den Gates unten | die eigene Arbeit reviewen, freigeben oder mergen; `docs/STATUS.md`, Governance-/Wissensdateien und `docs/DATABASE.md` ändern; einen Task parallel zu einem anderen Implementierer bearbeiten; Tags anlegen |
| **Microsoft 365 Copilot** | Berichte, E-Mails, Folien aus dem OneDrive-Ordner `GeniusNew` (von der Neuordnung nicht berührt) | Inhalte erfinden, die dort nicht stehen |

## Die Wissensdatenbank

**`docs/STATUS.md`** (Stand, Grenzen, nächste Schritte) pflegt weiterhin **Gemini**, mit
NotebookLM als Auskunft. **`docs/COLLABORATION.md` und `docs/DECISIONS.md`** (jede
Entscheidung mit Datum, Begründung und Quelle) gehören **Claude Code**. Beide Dateien
zusammen sind das Gedächtnis des Projekts; niemand sonst schreibt sie.

**Quellen in NotebookLM** (Notebook „GeniusNew“): `docs/STATUS.md`,
`docs/DECISIONS.md`, `docs/DATABASE.md` (sobald sie existiert), `SECURITY.md`,
`docs/ROADMAP-V01.md`, `docs/COLLABORATION.md`, `AGENTS.md`. Das Repository ist öffentlich, deshalb können die Quellen als Links auf
die Rohdateien in `main` eingebunden werden, zum Beispiel
`https://raw.githubusercontent.com/Kaancodm/GeniusNew/main/docs/STATUS.md`.

**Wer fragt wen:** Jedes Werkzeug und Kaan fragen bei Wissensfragen zuerst NotebookLM,
also „Was wurde zu X entschieden?“, „Warum ist Y so?“ oder „Was ist der nächste
Schritt?“. Steht es dort nicht, ist es noch nicht entschieden. Dann geht die Frage an
Kaan; Claude Code trägt die Antwort in `docs/DECISIONS.md` ein, Gemini spiegelt sie bei
Bedarf in `docs/STATUS.md`.

**Pflege nach jedem Merge:**
1. **Gemini** liest den Wissensblock des gemergten PRs und aktualisiert `docs/STATUS.md`
   auf einem Branch `gemini/wissen-<datum>`; ein solcher PR ändert nur diese Datei.
   Gemini mergt ihn selbst, sobald `contracts` grün ist.
2. **Claude Code** trägt neue Entscheidungen und Konfliktergebnisse in
   `docs/DECISIONS.md` ein, auf einem eigenen `claude/<thema>`-Branch; Kaan mergt (siehe
   „Konflikte“ unten).
3. Die Quellen in NotebookLM aktualisieren und Widersprüche zwischen den Dokumenten als
   Issue melden.

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
Kaan entscheidet offene Punkte → zugewiesener Implementierer setzt um →
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
5. **Umsetzung (zugewiesener Implementierer), je ein PR, je ein Implementierer:**
   (a) Ledger mit Ablauf, (b) wartende Jobs, (c) Audit-Kette, (d) Anker-Zustand,
   danach die Portal-Tabellen und (e) das Portal selbst — jeweils nach dem
   freigegebenen Entwurf. Jeder DB-Code-PR braucht zusätzlich zur grünen CI ein
   **Claude-DB-Review auf dem exakten aktuellen Head-SHA**: `Claude DB Review:
   APPROVED` oder `Claude DB Review: CHANGES REQUESTED`. `CHANGES REQUESTED` oder eine
   Freigabe für einen älteren Head-SHA blockiert den Merge. Da Claude hier fremden Code
   gegen Kaans freigegebenen Entwurf prüft, nicht die eigene Arbeit, ist das keine
   Selbstfreigabe. Hat Claude den DB-Code selbst implementiert, kann Claude ihn nicht
   freigeben; dann bleibt der Merge gesperrt, bis Kaan ein anderes unabhängiges
   Review bestimmt und es am exakten Head vorliegt.
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

**Ein DB-Code-PR** ist jeder PR, der Speicher-Code, Schema oder Migrationen ändert,
gleich von welchem Implementierer (Cursor, Kiro, Codex, Copilot, Grok, Claude); er
braucht das Gate aus Schritt 5. `docs/DATABASE.md` selbst erstellt und
pflegt ChatGPT im Auftrag von Kaan (Schritt 2); das ist kein DB-Code-PR und braucht
kein `Claude DB Review: APPROVED`, sondern Claudes Sicherheitsreview und Kaans
Freigabe (Schritte 3–4).

## ChatGPT: Architektur, Dispatch, Werkzeuge, Server und Infrastruktur

**Wozu:** Arbeit zerlegen und zuweisen sowie alles rund um Betrieb, Werkzeuge und
externe Integrationen, ohne die Kernlogik zu berühren. Solange kein Server existiert,
ist das Setup-Arbeit; sobald einer läuft, ist es laufende Pflege.

**Aufgaben:**
- **Dispatch:** Ziele in begrenzte Tasks als GitHub-Issues zerlegen, je Task genau
  einen schreibenden Implementierer zuweisen (Grundregel oben) und Live-Evidenz prüfen:
  CI und Checks am exakten Head, Server-Zustand. Die Prüfung der Evidenz ist keine
  Freigabe; Merge und Freigaben folgen „Der Ablauf eines Themas“.
- **Server-Pflege**, laufend: systemd-Units, Reverse-Proxy, TLS, Backups,
  Restore-Abläufe, sobald ein Server existiert.
- **Neue Werkzeuge, Plugins, Dashboards und Monitoring** — eine offene Kategorie, zum
  Beispiel Warp, spätere Monitoring- oder Dashboard-Tools.
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

1. Kaan wählt den nächsten Schritt; die Auskunft dazu gibt NotebookLM. **ChatGPT
   zerlegt** ihn in begrenzte Tasks (GitHub-Issues) und **weist je Task genau einen
   Implementierer zu** (Prompt 1 aus `docs/HANDOVER.md`): normale Produkt- und
   Code-Leaf-Tasks an Cursor; schwierige oder mehrdeutige Tasks zuerst an Kiro für eine
   Spec, Bugfixes an Kiro; kleine, klar begrenzte Leaf-Tasks optional an den Copilot
   Coding Agent; zugewiesene Security- und Governance-Aufgaben an Claude Code;
   Tools/Server/Infrastruktur/DB-Design bleiben bei ChatGPT. Regel- oder
   Konfliktfragen gehen an Claude Code.
2. Der Implementierer klärt zuerst den Plan (Plan/Ask), öffnet dann einen Draft-PR auf
   dem eigenen Branch-Präfix (Tabelle oben) aus seinem eigenen Worktree und schreibt den
   Plan in die PR-Beschreibung. Ändert das Thema eine Prozessgrenze oder Kryptografie,
   fragt der Implementierer im PR mit `@gemini-code-assist` nach einer
   Design-Vorprüfung, bevor Code entsteht.
3. **Gemini und Copilot reviewen automatisch** jeden PR und jeden neuen Push. Ein neues
   Gemini-Review nach Änderungen fordert der Implementierer mit `/gemini review` im PR
   an. Claude Code (Security, DB, Architektur) und Grok (Red-Team, zweite
   Architekturmeinung) prüfen zusätzlich read-only am exakten Head, wenn ChatGPT oder
   Kaan es anfordert oder ein Gate es verlangt. ChatGPT prüft die Live-Evidenz.
4. **Merge.** Voraussetzung immer: CI grün (`contracts`), Wissensblock ausgefüllt, kein
   blockierender Review-Befund offen. Blockierend sind Gemini-Befunde der Stufe
   **Critical** oder **High** und Copilot-Befunde zu den Punkten der Checkliste.
   - **Kaan mergt alle PRs**: von Cursor, Kiro, Copilot Coding Agent, Grok, Codex,
     ChatGPT und Claude. Kein Werkzeug mergt eigene oder zugewiesene PRs selbst; die
     frühere Selbst-Merge-Regel für Codex gilt nicht mehr (`docs/DECISIONS.md`
     02.10.2026).
   - **Einzige Ausnahme:** Gemini mergt seinen Wissens-PR (`gemini/wissen-*`, nur
     `docs/STATUS.md`) selbst bei grüner CI, wie bisher (siehe oben).
   - Kaans ausdrückliches OK braucht es zusätzlich für die Ausnahmen: eine neue
     Abhängigkeit, eine geänderte Grenze aus `SECURITY.md` (offen gehaltener Test
     umgekehrt) und Tags. Bei diesen Ausnahmen muss vorher ein Gemini-Sicherheits-Review
     im PR stehen.
   - **DB-Code-PRs** brauchen zusätzlich `Claude DB Review: APPROVED` am exakten
     Head-SHA (siehe oben), gleich wer implementiert hat.
   - Release, Deployment, Zugriffsrechte und Rotation von Zugangsdaten bleiben Kaans
     ausdrückliche Entscheidung.
5. Gemini überträgt den Wissensblock in `docs/STATUS.md`; Claude Code trägt
   Entscheidungen und Konfliktergebnisse in `docs/DECISIONS.md` ein.

**Kommunikation läuft über den PR**, nicht über Kopieren zwischen Chats. Plan,
Rückfragen an Gemini oder Claude, Reviews, Begründungen und der Wissensblock stehen im
PR. Jedes Werkzeug und Kaan sehen so denselben Stand.

### Wissensblock (Pflicht in jeder PR-Beschreibung eines Implementierers)

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

- Gemini oder Grok lehnt einen PR ab, der Implementierer hält den Befund für falsch;
- ein Dokument widerspricht dem Code oder einem anderen Dokument (etwa `STATUS.md`,
  `DATABASE.md` und `SECURITY.md` untereinander);
- unklar ist, wem eine Datei oder eine Aufgabe gehört.

Jedes Werkzeug meldet ihn mit einem **KONFLIKT**-Block im PR oder als Issue. Kaan gibt
ihn an Claude Code weiter:

```text
KONFLIKT GeniusNew
Wer gegen wen: <z. B. Cursor gegen Gemini>
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
Ausnahmen (neue Abhängigkeit, Grenze aus `SECURITY.md`, Tags, Kaans Architektur- und
Freigabeentscheidungen zur Datenbank). Das bleibt Kaans Sache. Claudes eigene PRs mergt
Kaan.

## Wann ein Implementierer Claude um Hilfe bittet

Ein Implementierer (Cursor, Kiro, Codex, Copilot Coding Agent, Grok) steckt fest, wenn
**eines** davon zutrifft:

- dieselbe CI-Prüfung ist nach zwei eigenen Fixversuchen noch rot,
- ein Test, der Refusal-Guard oder die Demo widerspricht dem Auftrag, und die Regeln
  in `AGENTS.md` lassen keinen Weg offen,
- die Aufgabe berührt eine Grenze aus `SECURITY.md`, und unklar ist, ob sie geschlossen
  werden soll.

Dann schreibt er einen **Hilferuf** in genau dieser Form, und Kaan gibt ihn an
Claude Code weiter:

```text
HILFERUF GeniusNew
PR / Branch: <#nummer, cursor/... | kiro/... | codex/...>   Head-SHA: <sha>
Ziel: <ein Satz>
Was fehlschlägt: <Check-Name oder Befehl>
Fehlermeldung (gekürzt): <max. 30 Zeilen>
Schon versucht: <1–3 Punkte>
Frage an Claude: <konkret>
```

Claude antwortet mit einer Diagnose und einem Vorschlag. Der Implementierer setzt ihn
im eigenen PR um; Claude pusht nur, wenn Kaan es ausdrücklich sagt.

## Geräte

| Gerät | Arbeit |
| --- | --- |
| iPad Pro | ChatGPT, Gemini, NotebookLM, GitHub (auch Copilot-Aufträge über GitHub Mobile), OneDrive/OneNote, Reviews |
| Laptop (Linux/WSL) | Codex, Git-Checkout, lokale Tests; ein Implementierer je Branch, Tests nacheinander |
| Debian | Cursor Agent CLI (primärer Implementierer), weitere zugewiesene Implementierer; je Task ein eigener Worktree, ein Implementierer je Branch, Tests nacheinander; Warp als Operator-Terminal mit `tmux` |
| Server (sobald vorhanden) | ChatGPT pflegt ihn laufend (systemd, Backups, Restore), Zugriff über Warp; Zugangsdaten nur in privater Doku |

Zugriff vom iPad auf Laptop oder Debian über SSH (z. B. mit Tailscale) in eine Sitzung
mit `tmux`. Hostnamen, Zugangsdaten und private Adressen gehören nur in die private
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
eigenen Kopien bearbeiten: Was an `STATUS.md` falsch ist, korrigiert Gemini, was an
`DECISIONS.md` falsch ist, korrigiert Claude Code; danach werden die Dateien neu
abgelegt. Die Git-Arbeitskopie liegt **außerhalb** des synchronisierten
OneDrive-Ordners, damit sich Synchronisation und Git nicht in die Quere kommen. In
OneNote ein Notizbuch `GeniusNew` mit den Abschnitten `Start`, `Entscheidungen`,
`Reviews` und `Ideen`; Aufgaben und Freigaben werden aus GitHub verlinkt. Quellenpakete
für NotebookLM, Gemini oder Claude Code tragen Datum und vollen Commit-SHA; ein Merge
aktualisiert statische Uploads nicht von selbst.
