# GeniusNew — Projektstand

**Stand: 08.10.2026, `main` `7f81e240f6ec4b16d876f081249afb3d47240901`.** Teil der
Wissensdatenbank, geführt von Claude Code (`docs/COLLABORATION.md`); NotebookLM gibt
Auskunft. Entscheidungen stehen in `docs/DECISIONS.md`. Dieses Dokument ist in sich
geschlossen gedacht: als Quelle für NotebookLM, Microsoft 365 Copilot oder jeden anderen
Assistenten, der das Repository nicht selbst lesen kann. Verbindlich bleiben der Code,
`SECURITY.md` und `docs/ROADMAP-V02.md`. Bei Widerspruch gilt das Repository.

**Regelhinweis (10.10.2026):** Die unten beschriebenen Merge-Rechte sind durch Kaans
Ausführungs-Gates vom 06.10.2026 abgelöst (`docs/EXECUTION-GATES.md`). Jeder Merge nach
`main` braucht Kaans ausdrückliches GO; fachliche CI- und Review-Gates bleiben bestehen.

**Sichtbarkeit und Quellenweg (07.10.2026):** Das Repository ist aktuell **privat**
(GitHub-Stand; keine Aussage zur Dauer, Kaan klärt die Regeln); Raw-Links auf `main` liefern HTTP 404. NotebookLM bekommt Quellen
nur über von Kaan hochgeladene, geprüfte Dateien aus einem exakten Commit
(`docs/COLLABORATION.md`, „NotebookLM-Quellenpaket“). **Blocker:** Es gibt noch kein
dokumentiertes, hochgeladenes Quellenpaket; eine NotebookLM-Importprüfung wurde nicht
durchgeführt (UNKNOWN).

## In einem Satz

GeniusNew ist ein Zero-Trust-Agentensystem, in dem kein Bauteil seine eigene
Entscheidung bestätigt: Jeder Schritt vom HTTP-Eingang bis zum angenommenen Ergebnis
wird von einer anderen Instanz geprüft und in einer signierten, verketteten
Audit-Kette festgehalten.

## Der Weg eines Auftrags

1. **HTTP-Eingang:** Ein API-Key wird serverseitig auf einen Principal abgebildet. Der
   Client liefert nur Text; Rechte, Tier und Job-Kennung bestimmt der Server.
2. **Orchestrator:** stellt aus der Policy einen Handoff aus und signiert ihn. Er ist
   die einzige Instanz mit dem Handoff-Signierschlüssel.
3. **Gateway:** prüft den Handoff unabhängig nur mit dem öffentlichen Schlüssel,
   verbraucht bei Bedarf ein einmaliges Approval-Token und mintet einen einmaligen
   `DispatchPermit`.
4. **Worker:** läuft in einem eigenen Prozess ohne Netz, mit Ressourcenlimits und
   ohne Schreibzugriff außerhalb eines temporären Verzeichnisses.
5. **Ergebnisprüfung:** nimmt das signierte Ergebnis an, und zwar nur einmal pro
   Handoff und nur innerhalb der Gültigkeit. Sie hält nur öffentliche Schlüssel und
   könnte kein Ergebnis fälschen.
6. **Audit:** Jede Entscheidung wird ohne Nutzdaten in eine Hash-Kette geschrieben.
   Deren Kopf signiert die Audit-Rolle; ein Anker in eigenem Prozess hält ihn fest,
   sodass eine gekürzte und neu signierte Kette auffällt.

## Stand

**v0.1 ist erreicht** (Tag `v0.1` auf `3a0e1bc`, 26.09.2026). Auf dem Weg zu v0.2
(`docs/ROADMAP-V02.md`, technisch beta-ready) sind seit dem 26.09.2026 gemergt:

| Gate | Inhalt | PR |
| --- | --- | --- |
| A1, A2 | Seccomp gegen Prozessstart; Landlock-Allowlist (Mindestziel). Offen: eigener OS-Nutzer für den Worker | #57, #76 |
| B0–B3 | `docs/DATABASE.md`, PostgreSQL-Fundament, persistentes Job- und Annahme-Ledger | #70, #78, #90, #93 |
| B4, B5 | Wartende Jobs und Approval-Speicher, persistente Audit-Kette. **Ohne** `Claude DB Review: APPROVED` am Head gemergt, Nachholung offen (Entscheidung 6) | #94, #95 |
| B6 | Ledger und Approval-Speicher manipulationssichtbar, Abgleich gegen die verankerte Kette | #97 |
| B7 | Absturztest mit `SIGKILL` an jeder Zustandsgrenze | #106 |
| C1, C2 | `python -m geniusnew serve`; Anker als eigener Dienst mit signierten Antworten | #61, #62 |
| C3 | Rolle „Freigebende“ mit eigener HTTP-Route | #111 |
| C4 | Rate-Limit, Job- und Verbindungsgrenze, nginx-Vorlage | #71 |
| C5 (Teil) | Restore gegen den unabhängigen Anker durchgespielt | #119 |
| D1 (nur Doku) | Portal→Kern-Vertrag, Replay- und Identitäts-Gates; Code fehlt | #120 |
| E1 | Demo mit Persistenz-Angriffen | #112 |
| Werkzeuge | Server-Einrichtung `ops/server/genius-server`, Deck, Reviewkanäle Abacus und Kiro | #110, #114, #116 |

**Noch offen:** C5 (Betriebsanleitung mit systemd, Rotation), E3 (frischer Klon auf einem
echten Server), D1-Code, D2, D3, E2 (Security-Review am Head). Was in der
`ROADMAP-V02.md`-Spalte „Stand“ noch „offen“ steht, aber oben gemergt ist, gleicht ein
eigener Docs-PR an (die Roadmap gehört nicht zu diesem Wissens-PR).

**Messbar (am 08.10.2026 auf `7f81e24` ausgeführt):**
- 1078 Tests laufen in etwa 94 Sekunden, Ergebnis `OK`. Ein zweiter Lauf unter
  Python 3.13.16 mit lokalem PostgreSQL 16 endete ebenfalls `OK` (etwa 104 Sekunden).
- Die Demo endet mit „PASS“, dabei werden **15 von 15 Angriffe** abgelehnt.
- Der Refusal-Guard führt 19 Python-Module in `GUARDED` plus eine Shell-Datei
  (`GUARDED_SHELL`). Er wurde in dieser Sitzung **nicht** ausgeführt: UNKNOWN.

## Bekannte Grenzen

Vollständig in `SECURITY.md` (maßgeblich). Die wichtigsten:

- Außer Worker und Anker laufen alle Instanzen in **einem Prozess**.
- Alle Schlüssel hängen an **einem Root-Secret**, es gibt **einen** Worker-Schlüssel.
- Der Anker läuft ohne eigenen Betriebssystem-Nutzer rückschneidbar; unter eigenem
  Nutzer betrieben (`docs/ANCHOR-SERVICE.md`) kann der Dienstnutzer ihn weder beenden
  noch seine Datei zurückschneiden. Der Anker-Nutzer, `root` und eine zurückgespielte
  Sicherung können es weiterhin. Das ist Sache der Installation und nicht testbar.
- Worker-Isolation ist eine Prozessgrenze, keine microVM; ohne Landlock ABI 4 läuft kein
  Worker (fail closed).
- Der HTTP-Eingang hat kein TLS und keine Sessions (Deployment-Aufgabe, Vorlage in
  `docs/REVERSE-PROXY.md`).
- Gespeicherte Annahmen sind an aktuelle Policy und Schlüssel gebunden (Entscheidung 7).

## Technische Risiken (Bewertung vom 08.10.2026)

Eine externe Bewertung von `7f81e24` hat Claude Code am Code nachgeprüft. Bestätigt:

- **Die Audit-Kette wird mit jedem Event teurer.** Jeder Append liest und prüft die ganze
  gespeicherte Kette (`_append` ruft `_read` in `geniusnew/audit_store.py`), und jeder
  Commit schickt die ganze Kette an den Anker. An der Größengrenze (64 MiB Anfrage,
  16 MiB Ankerzustand, `SECURITY.md`) verweigert der Dienst weitere Events (fail
  closed). Für Dauerbetrieb braucht es ein inkrementelles Protokoll.
- **Eine Datenbankverbindung für alles.** `python -m geniusnew serve` teilt eine
  Verbindung zwischen Job-Ledger, Annahme-Ledger und Audit-Kette
  (`geniusnew/__main__.py`), gewollt für die B6-Atomarität. Der Durchsatz ist damit
  einspurig, ein Verbindungsabbruch braucht einen Neustart (`SECURITY.md`).
- **Die CI testet nur Python 3.11** (`.github/workflows/verify.yml`). Laut Bewertung
  läuft der Server unter 3.13 (hier nicht geprüft, UNKNOWN). Lokal lief die Suite unter
  3.13 grün (Messwerte oben); die CI sichert das nicht ab.
- **Kein Linter, keine Typprüfung, keine Coverage-Messung** in der CI. Jedes Werkzeug
  dafür wäre eine neue Abhängigkeit und damit Kaans Entscheidung.

Nicht übernommen: DB-Tests ohne `GENIUSNEW_TEST_ADMIN_DSN` überspringen.
`tests/postgres_support.py` wertet fehlende Infrastruktur bewusst als Fehler, und kein
Test wird übersprungen (`AGENTS.md`, Regel 4).

## Nächste Schritte

Reihenfolge laut `docs/ROADMAP-V02.md`; Entscheidungen von Kaan sind dort unter „Offene
Entscheidungen“ gelistet (Portal in der Beta, Portal→Kern-Authentisierung, abgelaufene
Jobs, Annahmen und Policy).

1. Roadmap-Spalte „Stand“ an die gemergten PRs angleichen (Docs-PR #130).
2. `Claude DB Review` für B4 (#94) und B5 (#95) am gemergten Stand nachholen.
3. Kaan entscheidet: Python-Version der CI (3.13 zusätzlich oder statt 3.11) und ob
   Linter, Typprüfung, Coverage und `pip-audit` als Entwicklungswerkzeuge dazukommen.
4. Server einrichten (`ops/server/genius-server`, Kaan) und Landlock dort prüfen.
5. C5: Betriebsanleitung mit systemd-Units, eigenen OS-Nutzern (Worker, Anker),
   Schlüsselrotation (Entwurf von ChatGPT in #117, Draft).
6. E3: frischer Klon folgt der Anleitung wörtlich bis zum laufenden Dienst, Protokoll
   mit SHA.
7. Audit-Append inkrementell machen: erst ein Messtest zum Wachstum (Codex), dann der
   Entwurf in `docs/DATABASE.md` (ChatGPT im Auftrag von Kaan, Design-Vorprüfung durch
   Gemini, Freigabe Kaan), dann die Umsetzung (Codex, `Claude DB Review`, Grenze aus
   `SECURITY.md` mit Kaans OK).
8. Quellenpaket für NotebookLM nach Verfahren (`docs/COLLABORATION.md`).
9. D1-Code (Codex) nach dem Vertrag aus #120, danach D2 und D3.
10. E2: Security-Review des vollständigen Heads (Claude und Copilot).

## Zusammenarbeit der Werkzeuge

Ab 27.09.2026 gilt `docs/COLLABORATION.md` in aktualisierter Fassung. **Codex setzt
Kerncode um und mergt nach Kaans GO.** **Seit 04.10.2026 pflegt Claude Code `docs/STATUS.md`**
(dieses Dokument); Gemini prüft über die Antigravity- oder Abacus-CLI (`docs/COLLABORATION.md`). **Kaan entscheidet
Ziele und offene Fragen zur Datenbank; ChatGPT erstellt `docs/DATABASE.md` in seinem
Auftrag.** **ChatGPT** übernimmt außerdem neue Werkzeuge, Server-Pflege und
Infrastruktur. Copilot prüft zusätzlich. Microsoft 365
Copilot liest aus OneDrive. **Claude Code besitzt `docs/COLLABORATION.md` und
`docs/DECISIONS.md`**, sorgt für Ordnung und Struktur, löst Konflikte zwischen den
Plattformen mit Überschreibrecht und hilft, wenn Codex feststeckt.

| Werkzeug | Rolle | Liest |
| --- | --- | --- |
| Gemini Pro | Review über die Antigravity- oder Abacus-CLI: Pflicht-Zweitmeinung bei Ausnahmen, Design-Vorprüfung, sonst auf Anforderung | `GEMINI.md`, `.gemini/styleguide.md` |
| NotebookLM | **Wissensdatenbank**: Auskunft für alle, mit Quelle | dieses Dokument, `docs/DECISIONS.md`, `SECURITY.md`, `docs/ROADMAP-V01.md`, `docs/COLLABORATION.md`, `AGENTS.md` |
| ChatGPT Pro / Codex | **Kerncode-Umsetzung**, ein Thema pro PR (`codex/<thema>`), mit Wissensblock | `AGENTS.md`, `docs/COLLABORATION.md` |
| ChatGPT (Chat) | **Neue Werkzeuge, Server-Pflege, Infrastruktur, Integrationen** (`chatgpt/<thema>`), NotebookLM + eigenes Notebook; Kaan mergt | `AGENTS.md`, `docs/COLLABORATION.md` |
| GitHub Copilot Pro | Editor und Review jedes PRs | `.github/copilot-instructions.md` |
| Kiro-CLI | Unabhängige Zweitprüfung für Claudes eigenen Code, nur lesend | `docs/COLLABORATION.md` |
| Microsoft 365 Copilot | Berichte, E-Mails, Folien | OneDrive-Ordner `GeniusNew` |
| Claude Code | **Besitzt `docs/COLLABORATION.md`/`docs/DECISIONS.md`, Ordnung, Konfliktlöser mit Überschreibrecht**, Sicherheits-Review von Kaans DB-Entwurf und Codex' DB-Code, Hilfe bei Hilferuf | `AGENTS.md`, `docs/COLLABORATION.md` |

Übergaben zwischen den Werkzeugen laufen über die zwei Prompts in `docs/HANDOVER.md`.

Regel für alle: Jede Änderung läuft über einen PR mit grüner CI. Codex mergt eigene
Kerncode-PRs erst nach Kaans ausdrücklichem GO; ChatGPT- und Claude-eigene PRs mergt
Kaan. Neue Abhängigkeiten, geänderte `SECURITY.md`-Grenzen und Tags brauchen
zusätzliche fachliche Freigaben und Reviews.
