# GeniusNew — Projektstand

**Stand: 20.10.2026, `main` `a7308289977d4017cfc1a521100db3e0d01ccc98`.** Gesamtpflege
über #145 (Stand `dde20f2`): Gates, Messwerte, Risiken, Aufräumen der offenen PRs.
Nachgetragen am 20.10.2026 aus den Wissensblöcken bzw. PR-Texten: #174, #154, #179, #180. Die Abschnitte zu D1
(Entscheidungen vom 18.10.2026, #158) und zum lokalen KI-Pilot (19.10.2026, Issue #166)
stammen aus früheren Pflege-PRs und sind hier nicht neu geprüft; der Portal-DB-Vertrag
aus #176 (D2/D3, HOLD) ist nur als gemergt erfasst. Teil der
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
| B4, B5 | Wartende Jobs und Approval-Speicher, persistente Audit-Kette. **Ohne** `Claude DB Review: APPROVED` am Head gemergt, kein separates Nachholen des `Claude DB Review`, E2 deckt es ab (Entscheidung 6, `docs/DECISIONS.md` 18.10.2026); das Gemini-Sicherheitsreview bleibt offen | #94, #95 |
| B6 | Ledger und Approval-Speicher manipulationssichtbar, Abgleich gegen die verankerte Kette | #97 |
| B7 | Absturztest mit `SIGKILL` an jeder Zustandsgrenze | #106 |
| B (Härtung) | PostgreSQL-Sitzungen: Idle-Grenze 60 s für offene Transaktionen, TCP-Keepalives, `application_name`, zentrale Advisory-Lock-IDs | #137 |
| C1, C2 | `python -m geniusnew serve`; Anker als eigener Dienst mit signierten Antworten | #61, #62 |
| C3 | Rolle „Freigebende“ mit eigener HTTP-Route | #111 |
| C4 | Rate-Limit, Job- und Verbindungsgrenze, nginx-Vorlage | #71 |
| C5 (Teil) | Restore gegen den unabhängigen Anker durchgespielt | #119 |
| D1 (nur Doku) | Portal→Kern-Vertrag, Replay- und Identitäts-Gates; Code fehlt. Security-Review am `3d26d74` (H1, M1–M4); Gates 1–5, 7 und 8 sowie der Annahmepfad von Gate 9 von Kaan am 18.10.2026 entschieden (`docs/DECISIONS.md`); Entwurf danach überarbeitet (#163, gemergt 18.10.2026); der erneute Review am neuen Head (Gate 6) ist UNKNOWN; D1 bleibt HOLD | #120, #158, #163 |
| D1 (Kern) | HTTP-Eingang lehnt doppelte Header vor Body und Mapping ab | #162 |
| D3 (nur Doku) | Entwurf der administrierten Personenbindung mit neuer Envelope-Version; Portal-DB-Wiring D2/D3 mit sicheren Identitätsgrenzen. Beide HOLD, kein Code aktiv | #148, #176 |
| E1 | Demo mit Persistenz-Angriffen | #112 |
| Härtung, E3-Werkzeug, Audit-Delta (Arena 19.10.) | `serve()` verweigert Nicht-Loopback und IPv4-mapped IPv6 selbst; `scripts/check_dependencies.py`, Audit-Workflow nur als Beispiel (`docs/examples/audit-workflow.yml`, nicht aktiv); Benchmark `scripts/bench_audit.py` (Decke 62.137 Records); Fresh-Clone-Runner `scripts/e3_fresh_clone.py` (E3 selbst offen); Worker `TextDigest` in `BUILTIN_WORKERS` mit `tools/genius_client.py`; `AuditAnchor.commit_delta`/`commit2` als Protokoll-Schritt 1, **nicht** im Store-/Serve-Pfad verdrahtet. Unabhängiges Security-Review der Kernänderungen: UNKNOWN | #174 |
| CI | Test-PostgreSQL nur an `127.0.0.1` mit validiertem dynamischem Port; Restore-Drill bindet den Admin-DSN an genau diesen Container | #154 |
| KI-Advisory im Kern | `geniusnew/advisory.py`: standardmäßig aus (`[ai_advisory] enabled = true`), keine Autorität, kein HTTP-Weg; Antwort ist untrusted Text plus SHA-256. Pilot unter `tools/ai_pilot/` (aus #172). Neue Zeile in `SECURITY.md`. Merge auf Kaans Anweisung (`docs/DECISIONS.md` 19.10.2026); Gemini-Pro-Zweitmeinung und unabhängiges Security-Review offen | #179 |
| Client | `tools/genius_client.py`: HTTP nur zu literalem Loopback, keine Redirects, Proxy-Umgehung für Loopback, Steuerzeichen im API-Key verweigert | #180 |
| Werkzeuge | Server-Einrichtung `ops/server/genius-server`, Deck, Reviewkanäle Abacus und Kiro; manueller iPad-Modellclient über Tailscale (`tools/ipad_llm.py`, Live-Test am Gerät UNKNOWN); Refusal-Guard für drei langsame Module fokussiert; Project Deck | #110, #114, #116, #142, #153, #156 |

**Noch offen:** C5 (Betriebsanleitung mit systemd, Rotation und Betriebsabnahme des
Worker-Dienstes aus #140), E3 (frischer Klon auf einem echten Server), D1-Code, D2, D3
und E2 (Security-Review am Head). **D1-Code bleibt auf HOLD** (Entscheidung vom
18.10.2026); D2 und D3 sind nicht aktiv (#151 und #152 offen, #148 und #176 nur Doku). Die
`ROADMAP-V02.md`-Spalte „Stand“ wurde mit #130 und #167 angeglichen; zwei Punkte fehlen
dort noch. Die C3-Zelle nennt `Claude DB Review` UNKNOWN; belegt ist APPROVED am
Merge-Head `cc95586ca27e6f9d76fd737967d87cdb2e8a44d5` (`docs/DECISIONS.md`, 04.10.2026).
Die Zellen A2 und C5 nennen #140 noch nicht (getrennter Worker-Dienst mit
Unit-Vorlagen gemergt, Betriebsabnahme HOLD). Das korrigiert ein eigener Docs-PR.

**Messbar laut CI am Head von #180 (`ea5456c`, Python 3.11, PostgreSQL 17):** 1398 Tests
OK, HTTP-Demo 15/15, Persistenz-Demo 3/3, Restore PASS. Der Refusal-Guard enthält neu
`geniusnew/advisory.py` und `tools/ai_pilot/pilot.py` (#179); die Gesamtzahl der
`GUARDED`-Einträge ist hier nicht neu gezählt: UNKNOWN.

**Messbar (am 19.10.2026 auf dem Stand von `main` `dde20f2` ausgeführt, Python 3.13.16,
lokales PostgreSQL 16):**
- 1244 Tests laufen in etwa 111 Sekunden, Ergebnis `OK`.
- Die Demo endet mit „PASS“, dabei werden **15 von 15 Angriffe** abgelehnt.
- Der Refusal-Guard führt 23 Einträge in `GUARDED`: 20 Kernmodule unter `geniusnew/`
  (neu ist `geniusnew/worker_service.py` aus #140), `tools/control_deck/actions.py`,
  `tools/ipad_llm.py` und das Shell-Skript `ops/server/genius-server` (sein Muster
  steht in `GUARDED_SHELL`). Lokal wurde er in dieser Sitzung **nicht** ausgeführt:
  UNKNOWN.

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
  closed). Gemessen in #137 (PostgreSQL 17) an einer synthetisch befüllten Kette mit
  62 137 Records, der rechnerischen Leseobergrenze: ein (abgelehnter) Append etwa 18,3 s,
  die Audit-Startprüfung etwa 18,3 s. Im Betrieb lehnt die Zustandsprüfung laut #136
  bereits den 61 231. Kopf ab; bei großen Events greift das 64-MiB-Limit früher. Für
  Dauerbetrieb braucht es ein inkrementelles Protokoll (Entwurf in #136, Draft).
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

## Lokaler KI-Pilot (Stand 19.10.2026, Issue #166)

Ein beratender Pilot auf dem Server: Qwen 3.5 4B schlägt einen Plan vor, genau ein
Helfer führt ihn aus, der Server-Code prüft deterministisch. Er ist **nicht** Teil von
v0.2. **Seit #179 (19.10.2026)** liegt der P1-Code unter `tools/ai_pilot/`, und der Kern
hat mit `geniusnew/advisory.py` einen standardmäßig ausgeschalteten Aufrufweg ohne
Autorität; das erweitert die Entscheidung vom 18.10.2026 („ohne Eingriff in
`geniusnew/`“) auf Kaans Anweisung. Betrieb bleibt STOP. Der Sicherheitsvertrag steht als Entwurf von Claude Code in Issue #166 und ist keine
Entscheidung von Kaan; entschieden sind Modellwahl, Wechselbetrieb und der Download
des Helfermodells (`docs/DECISIONS.md`, 18. und 19.10.2026).

**Gerät Server, belegt am 19.10.2026** (Ausgaben aus Kaans Terminal, nur lesend):

- Ollama 0.40.1 läuft als User-Dienst `genius-local-ai.service` unter dem Nutzer `kaan`
  und lauscht nur auf `127.0.0.1:11434`.
- Cgroup des Prozesses: `memory.max` 6442450944 (6 GiB), `memory.high` 5368709120
  (5 GiB). `systemctl show genius-local-ai` ohne `--user` zeigt `infinity` und ist
  dafür der falsche Weg.
- `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=2m`.
- Modelle: `qwen3.5:4b-q4_K_M` 3.3 GB, `qwen3.5:9b` 6.6 GB, `gemma4:e2b` 4.6 GB,
  `qwen2.5-coder:3b` 1.9 GB, dazu `qwen2.5-coder:1.5b` (geladen 1.03 GiB).
- Mini-Inferenz mit `qwen3.5:4b-q4_K_M`: Antwort `OK`, 13 s. Qwen 4B allein erreicht
  `memory.high`; im Test stieg der `high`-Zähler um 1571. Beide Modelle gleichzeitig
  sind nicht gemessen (UNKNOWN), im Wechselbetrieb gab es kein OOM.
- Tailscale läuft; Peers nach Gerätetyp: iOS online ×2, Windows online ×1, Android
  offline ×1, Linux offline ×1.

**Gerät Laptop, von Kaan gemeldet am 19.10.2026** (Zusammenfassung ohne Rohausgabe, die
letzte Zeile kam abgeschnitten an): Ollama 0.40.6, Port 11434, Bindung `127.0.0.1`,
lokale API PASS. `OLLAMA_HOST` ist laut Kaan in den untersuchten Terminalsitzungen nicht
gesetzt; maßgeblich ist die geprüfte Bindung `127.0.0.1` (ein Wert auf Dienst- oder
Systemebene ist damit nicht ausgeschlossen). Es wurden keine Einstellungen verändert.

**Nicht geprüft (UNKNOWN):** Rohbelege vom Laptop, Auth und WLAN-Erreichbarkeit der
iPad-App (ai.local). Das Prüfskript `ai-check.sh` liegt nicht im Repository.

## Nächste Schritte

Reihenfolge laut `docs/ROADMAP-V02.md`; Entscheidungen von Kaan sind dort unter „Offene
Entscheidungen“ gelistet (Portal in der Beta, Portal→Kern-Authentisierung, abgelaufene
Jobs, Annahmen und Policy).

1. Offene PRs, Konflikte und Merge-Gates bereinigen (Abschnitt „Aufräumen“ unten,
   Auftrag vom 09.10.2026); danach Entwicklungspause bis zum neuen v0.2-Master-Prompt.
2. ~~`Claude DB Review` für B4 (#94) und B5 (#95) nachholen~~ — entschieden am 18.10.2026: nicht separat, E2 deckt es ab. Offen bleibt das Gemini-Sicherheitsreview zu B4/B5.
3. Kaan entscheidet: Python-Version der CI (3.13 zusätzlich oder statt 3.11) und ob
   Linter, Typprüfung, Coverage und `pip-audit` als Entwicklungswerkzeuge dazukommen.
4. Server einrichten (`ops/server/genius-server`, Kaan) und Landlock dort prüfen.
5. C5: Betriebsanleitung mit systemd-Units, eigenen OS-Nutzern (Worker, Anker),
   Schlüsselrotation (Entwurf von ChatGPT in #117, Draft).
6. E3: frischer Klon folgt der Anleitung wörtlich bis zum laufenden Dienst, Protokoll
   mit SHA.
7. Audit-Append inkrementell machen: Der Entwurf für `docs/DATABASE.md` liegt als #136
   vor (Draft, ChatGPT im Auftrag von Kaan). Offen sind die Design-Vorprüfung durch
   Gemini, Claudes Sicherheitsreview und Kaans Freigabe; danach folgen Messtest und
   Umsetzung (Codex, `Claude DB Review`, Grenze aus `SECURITY.md` mit Kaans OK).
8. Quellenpaket für NotebookLM nach Verfahren (`docs/COLLABORATION.md`).
9. D1: Der Entwurf ist nach den Entscheidungen vom 18.10.2026 überarbeitet (#163
   gemergt). Offen: `docs/DATABASE.md` §4.9 anpassen (ChatGPT im Auftrag von Kaan, #161
   als Draft; #165 ohne Merge geschlossen); Uhr-Grenze als eigener `SECURITY.md`-PR,
   Zahlen fürs Lastbudget zur Freigabe, erneuter Review am neuen Head (Gate 6); erst
   dann D1-Code (Codex), danach D2 und D3.
10. E2: Security-Review des vollständigen Heads (Claude und Copilot).
11. KI-Pilot P1 (nur Server): Umsetzung unter `tools/` mit den sechs Negativtests und
    Eval-Suite (Codex, eigenes Issue); Tailscale-ACL, `tailscale serve` und
    ai.local-Prüfliste (ChatGPT, eigenes Issue); Laptop- und iPad-Prüfung auf den
    Geräten (Kaan). Security-Review am exakten Head (Claude) plus Gemini-Zweitmeinung
    wegen der neuen Netzgrenze.
12. Nach #179 (Reviews am 20.10.2026: Claude 2 Medium/2 Low, Gemini M1 **High**, M2 Medium;
    #174: Claude 2 Low): (a) M1 sofort beheben, Engine-Exception ohne `__context__` (Codex,
    eigener PR); (b) Pilot in eigenen Prozess unter eigener UID, erst Gemini-Design-
    Vorprüfung, dann Code (Codex, Entscheidung 20.10.2026). `enabled = true` bleibt im
    Betrieb STOP.
13. Roadmap: C3-Zelle auf den Beleg aus `docs/DECISIONS.md` korrigieren und #140 in den
    Zellen A2 und C5 nachtragen (eigener Docs-PR).

## Aufräumen der offenen PRs (Stand 19.10.2026)

Auftrag von Kaan vom 09.10.2026 (`docs/DECISIONS.md`). Am 17.10.2026 hat Claude Code mit
Kaans OK ohne Merge geschlossen:

| PR | Head | Grund |
| --- | --- | --- |
| #101 | `97b400d` | Die drei Tests sind über #97 (`f220e30`) schon auf `main` |
| #109 | `42a5aeb` | Rollenmodell überholt durch die Regeln vom 04.10.2026; Konflikt in allen drei Dateien |
| #134 | `cbf32f6` | Regeländerung ohne belegte Kaan-Entscheidung und ohne die nötigen Reviews; schwächt Schutzregeln ab (Entscheidung 07.10.2026) |
| #118 | `8ab7fca` | Überholt durch #129 und #130; die C3-Entscheidung steht jetzt in `docs/DECISIONS.md` |

Außerdem ohne Merge geschlossen, nicht von Claude: #121 (CI-Runner), #143 und #146
(automatische CodeRabbit-PRs). Am 19.10.2026 sind 27 PRs offen. Offene Fragen:

- **Pause:** Die Entscheidung vom 09.10.2026 ordnet an: erst PRs, Konflikte und
  Merge-Gates bereinigen, dann pausieren. Die Bereinigung ist bei 27 offenen PRs nicht
  abgeschlossen; neue Features starten laut derselben Entscheidung nicht. Seitdem
  wurden aber #140, #142, #148, #162, #163 und #176 gemergt, und #147, #149 bis #152 sowie
  #171 bis #175 sind offen. Am 18.10.2026 hat Kaan das Portal (D1 bis D3) zur Beta
  gezählt (`docs/DECISIONS.md`); ob damit die Pause für das Portal entfällt, ist
  nicht eingetragen: UNKNOWN. Welche der offenen PRs als Aufräumarbeit gelten, entscheidet
  Kaan.
- **Entscheidung vom 18.10.2026 laut #148:** administrierte Core-Personenbindung für
  v0.2 und eine neue Envelope-Version statt einer zweiten Signatur. Sie ist nur als
  Chat-Angabe in der Beschreibung des gemergten Entwurfs belegt und deshalb nicht in
  `docs/DECISIONS.md` eingetragen; Claude Code trägt sie ein, sobald Kaan sie bestätigt.
- **Neue Abhängigkeit in #151:** `argon2-cffi` und `argon2-cffi-bindings` in
  `requirements.txt`. Die Wahl von `argon2-cffi` ist am 26.09.2026 entschieden; der Merge
  einer neuen Abhängigkeit braucht nach `docs/COLLABORATION.md` trotzdem Kaans OK und
  vorher ein Gemini-Pro-Sicherheitsreview im PR.
- **Copilot-Review:** warten oder für die Dauer des Engpasses einen Ersatz festlegen.
  Laut Kommentar in #153 (6244844507) gilt für die Zusammenführung von #145 eine
  einmalige Copilot-Ausnahme von Kaan; sie ersetzt weder das Gate auf `main` noch die
  CI.
- **Regelfassung:** #139 und #141 berufen sich auf zwei verschiedene, nicht
  eingetragene Kaan-Entscheidungen (08.10. und 06.10.2026) und kollidieren miteinander in
  `AGENTS.md`, `docs/COLLABORATION.md` und `.github/copilot-instructions.md`; #139
  kollidiert außerdem mit `main` in `scripts/refusals.py`, und #141 ändert
  `docs/DECISIONS.md` und `docs/STATUS.md`, die nur Claude Code schreibt. #128 berührt
  `docs/DECISIONS.md`. Welche Fassung gilt, entscheidet Kaan schriftlich; Claude Code
  trägt es in `docs/DECISIONS.md` ein.

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
| Claude Code | **Besitzt `docs/COLLABORATION.md`/`docs/DECISIONS.md`, Ordnung, Konfliktlöser mit Überschreibrecht**, Sicherheits-Review von Kaans DB-Entwurf und Codex' DB-Code, Hilfe bei Hilferuf | `AGENTS.md`, `docs/COLLABORATION.md` |

Übergaben zwischen den Werkzeugen laufen über die zwei Prompts in `docs/HANDOVER.md`.

Regel für alle: Jede Änderung läuft über einen PR mit grüner CI. Codex mergt eigene
Kerncode-PRs selbst; ChatGPT- und Claude-eigene PRs mergt Kaan; neue Abhängigkeiten,
geänderte `SECURITY.md`-Grenzen und Tags brauchen Kaans OK.
