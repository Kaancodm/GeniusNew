# GeniusNew — Projektstand

**Stand: 18.10.2026, `main` `9b11f7a89fe948d65d6b2eb3337111a5b11d3a57`.** Teil der
Wissensdatenbank, geführt von Claude Code (`docs/COLLABORATION.md`); NotebookLM gibt
Auskunft. Entscheidungen stehen in `docs/DECISIONS.md`. Dieses Dokument ist in sich
geschlossen gedacht: als Quelle für NotebookLM, Microsoft 365 Copilot oder jeden anderen
Assistenten, der das Repository nicht selbst lesen kann. Verbindlich bleiben der Code,
`SECURITY.md` und `docs/ROADMAP-V02.md`. Bei Widerspruch gilt das Repository.

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
| A1, A2 | Seccomp gegen Prozessstart; Landlock-Allowlist; getrennter Worker-Dienst mit eigener fester UID gemergt. Betriebsabnahme für den Dienst weiterhin HOLD | #57, #76, #140 |
| B0–B3 | `docs/DATABASE.md`, PostgreSQL-Fundament, persistentes Job- und Annahme-Ledger | #70, #78, #90, #93 |
| B4, B5 | Wartende Jobs und Approval-Speicher, persistente Audit-Kette. **Ohne** `Claude DB Review: APPROVED` am Head gemergt, Nachholung offen (Entscheidung 6) | #94, #95 |
| B6 | Ledger und Approval-Speicher manipulationssichtbar, Abgleich gegen die verankerte Kette | #97 |
| B7 | Absturztest mit `SIGKILL` an jeder Zustandsgrenze | #106 |
| B (Härtung) | PostgreSQL-Sitzungen: Idle-Grenze 60 s für offene Transaktionen, TCP-Keepalives, `application_name`, zentrale Advisory-Lock-IDs | #137 |
| C1, C2 | `python -m geniusnew serve`; Anker als eigener Dienst mit signierten Antworten | #61, #62 |
| C3 | Rolle „Freigebende“ mit eigener HTTP-Route | #111 |
| C4 | Rate-Limit, Job- und Verbindungsgrenze, nginx-Vorlage | #71 |
| C5 (Teil) | Restore gegen den unabhängigen Anker durchgespielt | #119 |
| D1 (nur Doku) | Portal→Kern-Vertrag, Replay- und Identitäts-Gates; Code fehlt | #120 |
| E1 | Demo mit Persistenz-Angriffen | #112 |
| Werkzeuge | Server-Einrichtung `ops/server/genius-server`, Deck, Reviewkanäle Abacus und Kiro; manueller iPad-Modellclient über Tailscale (`tools/ipad_llm.py`, Live-Test am Gerät UNKNOWN) | #110, #114, #116, #142 |

**Noch offen:** C5 (Betriebsanleitung mit systemd, Rotation und Betriebsabnahme des
Worker-Dienstes aus #140), E3 (frischer Klon auf einem echten Server), D1-Code, D2,
D3 und E2 (Security-Review am Head). **D1-Code bleibt auf HOLD; D2 und D3 folgen
erst nach einer neuen Entscheidung.** Die
`ROADMAP-V02.md`-Spalte „Stand“ ist seit #130 (`c6a37bb`) angeglichen. Nur die C3-Zelle
nennt `Claude DB Review` dort noch UNKNOWN; belegt ist APPROVED am Merge-Head
`cc95586ca27e6f9d76fd737967d87cdb2e8a44d5`
(`docs/DECISIONS.md`, 04.10.2026). Das korrigiert ein eigener Docs-PR.

**Messbar (am 17.10.2026 auf `4627093` ausgeführt, Python 3.13.16, lokales PostgreSQL 16):**
- 1100 Tests laufen in etwa 129 Sekunden, Ergebnis `OK`.
- Die Demo endet mit „PASS“, dabei werden **15 von 15 Angriffe** abgelehnt.
- Der Refusal-Guard führt 22 Einträge in `GUARDED`: 19 Kernmodule unter `geniusnew/`,
  `tools/control_deck/actions.py`, `tools/ipad_llm.py` und `ops/server/genius-server`,
  dazu eine Shell-Datei (`GUARDED_SHELL`). Er wurde in dieser Sitzung **nicht**
  ausgeführt: UNKNOWN. Laut #137 und #142 lief er für `geniusnew/database.py` (66/66)
  und `tools/ipad_llm.py` (34/34) grün.

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
  closed). Gemessen in #137 (PostgreSQL 17): Bei voller Kette, 62 137 Records, dauern
  ein Append etwa 18,3 s und die Audit-Startprüfung etwa 18,3 s; der 62 138. Kopf wird
  abgelehnt. Für Dauerbetrieb braucht es ein inkrementelles Protokoll (Entwurf in #136,
  Draft).
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

Die Roadmap-Reihenfolge gilt vorbehaltlich der jüngeren Entscheidung vom 09.10.2026:
Zunächst werden offene PRs, Konflikte und Merge-Gates bereinigt; danach pausiert die
Entwicklung bis zum neuen v0.2-Master-Prompt. Die übrigen Punkte sind ein Rückstand,
kein Auftrag zum Start neuer Features. Offene Architekturentscheidungen stehen in
`docs/ROADMAP-V02.md` (Portal in der Beta, Portal→Kern-Authentisierung, abgelaufene
Jobs, Annahmen und Policy).

1. Offene PRs, Konflikte und Merge-Gates fertig bereinigen (Abschnitt unten, Auftrag
   vom 09.10.2026); danach Entwicklungspause bis zum neuen v0.2-Master-Prompt.
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
9. D1-Code bleibt auf HOLD; D2 und D3 erst nach einer neuen Entscheidung zu D1
   einplanen. Der Vertrag aus #120 ist nur Dokumentation.
10. E2: Security-Review des vollständigen Heads (Claude und Copilot).
11. Roadmap: C3-Zelle auf den Beleg aus `docs/DECISIONS.md` korrigieren (eigener Docs-PR).

## Aufräumen der offenen PRs (Stand 18.10.2026)

Auftrag von Kaan vom 09.10.2026 (`docs/DECISIONS.md`). Am 17.10.2026 hat Claude Code mit
Kaans OK ohne Merge geschlossen:

| PR | Head | Grund |
| --- | --- | --- |
| #101 | `97b400d` | Die drei Tests sind über #97 (`f220e30`) schon auf `main` |
| #109 | `42a5aeb` | Rollenmodell überholt durch die Regeln vom 04.10.2026; Konflikt in allen drei Dateien |
| #134 | `cbf32f6` | Regeländerung ohne belegte Kaan-Entscheidung und ohne die nötigen Reviews; schwächt Schutzregeln ab (Entscheidung 07.10.2026) |
| #118 | `8ab7fca` | Überholt durch #129 und #130; die C3-Entscheidung steht jetzt in `docs/DECISIONS.md` |

Nach diesen Schließungen waren 16 PRs offen. Am 18.10.2026 sind 20 PRs offen;
#140 wurde am 17.10.2026 um 21:33:51 UTC als
`9b11f7a89fe948d65d6b2eb3337111a5b11d3a57` gemergt. Die Betriebsabnahme
bleibt laut #140 auf HOLD; #144 ist noch offen. Offene Fragen:

- **Pause:** Die Entscheidung vom 09.10.2026 gilt als dokumentierte Reihenfolge:
  erst PRs, Konflikte und Merge-Gates bereinigen, dann pausieren. Die Bereinigung ist
  bei 20 offenen PRs noch nicht abgeschlossen; die Pause steht daher noch aus. Bis
  dahin starten laut derselben Entscheidung keine neuen Features. Am 18.10.2026 wurden
  dennoch #147 (D1-Codec, in keinen aktiven Pfad eingebunden), #148 (D3-Vertragsentwurf)
  und #149 (Kimi und Grok im Laya-Shadow-Vergleich) geöffnet. Ob sie als Aufräumarbeit
  gelten oder bis zum Master-Prompt warten, entscheidet Kaan.
- **Entscheidung vom 18.10.2026 laut #148:** administrierte Core-Personenbindung für
  v0.2 und eine neue Envelope-Version statt einer zweiten Signatur. Sie ist bisher nur
  als Chat-Angabe in der PR-Beschreibung belegt und deshalb nicht in
  `docs/DECISIONS.md` eingetragen; Claude Code trägt sie ein, sobald Kaan sie bestätigt.
- **Copilot-Review:** warten oder für die Dauer des Engpasses einen Ersatz festlegen.
- **Regelfassung:** Die weiterhin offenen #139 und #141 berufen sich auf zwei
  verschiedene, nicht eingetragene Kaan-Entscheidungen (08.10. und 06.10.2026) und
  widersprechen sich in `AGENTS.md` und `docs/COLLABORATION.md`; #128 berührt dieselben
  Regeldateien. Welche gilt, entscheidet Kaan schriftlich; Claude Code trägt es in
  `docs/DECISIONS.md` ein.

## Zusammenarbeit der Werkzeuge

Ab 27.09.2026 gilt `docs/COLLABORATION.md` in aktualisierter Fassung. **Codex setzt
Kerncode um und mergt.** **Seit 04.10.2026 pflegt Claude Code `docs/STATUS.md`**
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
| Claude Code | **Besitzt `docs/COLLABORATION.md`/`docs/DECISIONS.md`, Ordnung, Konfliktlöser mit Überschreibrecht**, Sicherheits-Review von ChatGPTs DB-Entwurf und Codex' DB-Code, Hilfe bei Hilferuf | `AGENTS.md`, `docs/COLLABORATION.md` |

Übergaben zwischen den Werkzeugen laufen über die zwei Prompts in `docs/HANDOVER.md`.

Regel für alle: Jede Änderung läuft über einen PR mit grüner CI. Codex mergt eigene
Kerncode-PRs selbst; ChatGPT- und Claude-eigene PRs mergt Kaan; neue Abhängigkeiten,
geänderte `SECURITY.md`-Grenzen und Tags brauchen Kaans OK.
