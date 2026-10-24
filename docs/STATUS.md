# GeniusNew — Projektstand

**Stand: 21.10.2026, `main` `9385f5bd9dc43f277af8f3b0265c7b4d45cfbc71`.** Der Abschnitt
„Nachtrag 21.10.2026“ unten korrigiert, wo es ausdrücklich genannt ist, die Stände zu
#150–#152, #177, #183, #184 und #192; alle übrigen Abschnitte tragen weiter den Stand
vom 20.10.2026 (`main` `0e6e0dbf5aefa4169c47ae73f8b39ff0cd94f235`). Gesamtpflege
über #145 (Stand `dde20f2`): Gates, Messwerte, Risiken, Aufräumen der offenen PRs.
Nachgetragen am 20.10.2026 aus den Wissensblöcken bzw. PR-Texten: #174, #154, #179, #180,
#188, dazu Kaans Entscheidungen vom 20.10.2026 (Pause, D3, #151, CI-Werkzeuge,
Gemini statt Copilot) und die Reviews aus Kaans Sammelauftrag vom selben Tag. Die Abschnitte zu D1
(Entscheidungen vom 18.10.2026, #158) und zum lokalen KI-Pilot (19.10.2026, Issue #166)
stammen aus früheren Pflege-PRs und sind hier nicht neu geprüft; der Portal-DB-Vertrag
aus #176 (D2/D3, HOLD) ist nur als gemergt erfasst. Teil der
Wissensdatenbank, geführt von Claude Code (`docs/COLLABORATION.md`); NotebookLM gibt
Auskunft. Entscheidungen stehen in `docs/DECISIONS.md`. Dieses Dokument ist in sich
geschlossen gedacht: als Quelle für NotebookLM, Microsoft 365 Copilot oder jeden anderen
Assistenten, der das Repository nicht selbst lesen kann. Verbindlich bleiben der Code,
`SECURITY.md` und `docs/ROADMAP-V02.md`. Bei Widerspruch gilt das Repository.

**Sichtbarkeit und Quellenweg (geprüft 20.10.2026):** GitHub meldet für
`Kaancodm/GeniusNew` **public**, passend zu `README.md` und `SECURITY.md`; ein Raw-Link
auf `docs/STATUS.md` liefert ohne Anmeldung HTTP 200. Die Aussage „privat, HTTP 404“ vom
07.10.2026 ist überholt; wann sich das geändert hat, ist UNKNOWN (zuerst festgestellt
von ChatGPT in #190). Die Sichtbarkeit wurde nicht geändert. Öffentlich ist der
GeniusNew-Kern; private Nachbarprojekte und Betriebsdetails (Hostnamen, Netzinventar,
Zugangsdaten) gehören nicht in dieses Repository. NotebookLM bekommt Quellen weiter nur
über von Kaan hochgeladene, geprüfte Dateien aus einem exakten Commit
(`docs/COLLABORATION.md`, „NotebookLM-Quellenpaket“); HTTP 200 belegt keinen Import.
**Offen:** Es gibt noch kein dokumentiertes, hochgeladenes Quellenpaket; eine
NotebookLM-Importprüfung wurde nicht durchgeführt (UNKNOWN).

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
| D1 (nur Doku) | Portal→Kern-Vertrag, Replay- und Identitäts-Gates; Code fehlt. Security-Review am `3d26d74` (H1, M1–M4); Gates 1–5, 7 und 8 sowie der Annahmepfad von Gate 9 von Kaan am 18.10.2026 entschieden (`docs/DECISIONS.md`); Entwurf danach überarbeitet (#163, gemergt 18.10.2026); der erneute Review (Gate 6) ist am 20.10.2026 an `main` `0e6e0db` bestanden (Issue #159); D1 bleibt HOLD | #120, #158, #163 |
| D1 (Kern) | HTTP-Eingang lehnt doppelte Header vor Body und Mapping ab | #162 |
| D2 (Bausteine) | Argon2id-Passwortgrenze und Session-/CSRF-/Cookie-Helfer gemergt (21.10.2026); keine Login-Route, keine Session-DB, von keinem Aufrufer benutzt. Gate offen | #151, #152 |
| D3 (nur Doku) | Entwurf der administrierten Personenbindung mit neuer Envelope-Version; Portal-DB-Wiring D2/D3 mit sicheren Identitätsgrenzen. Beide HOLD, kein Code aktiv | #148, #176 |
| E1 | Demo mit Persistenz-Angriffen | #112 |
| Härtung, E3-Werkzeug, Audit-Delta (Arena 19.10.) | `serve()` verweigert Nicht-Loopback und IPv4-mapped IPv6 selbst; `scripts/check_dependencies.py`, Audit-Workflow nur als Beispiel (`docs/examples/audit-workflow.yml`, nicht aktiv); Benchmark `scripts/bench_audit.py` (Decke 62.137 Records); Fresh-Clone-Runner `scripts/e3_fresh_clone.py` (E3 selbst offen); Worker `TextDigest` in `BUILTIN_WORKERS` mit `tools/genius_client.py`; `AuditAnchor.commit_delta`/`commit2` als Protokoll-Schritt 1, **nicht** im Store-/Serve-Pfad verdrahtet. Unabhängiges Security-Review der Kernänderungen: UNKNOWN | #174 |
| CI | Test-PostgreSQL nur an `127.0.0.1` mit validiertem dynamischem Port; Restore-Drill bindet den Admin-DSN an genau diesen Container | #154 |
| KI-Advisory im Kern | `geniusnew/advisory.py`: standardmäßig aus (`[ai_advisory] enabled = true`), keine Autorität, kein HTTP-Weg; Antwort ist untrusted Text plus SHA-256. Pilot unter `tools/ai_pilot/` (aus #172). Neue Zeile in `SECURITY.md`. Merge auf Kaans Anweisung (`docs/DECISIONS.md` 19.10.2026); Gemini-Pro-Zweitmeinung und unabhängiges Security-Review offen | #179 |
| Client | `tools/genius_client.py`: HTTP nur zu literalem Loopback, keine Redirects, Proxy-Umgehung für Loopback, Steuerzeichen im API-Key verweigert | #180 |
| Werkzeuge | Server-Einrichtung `ops/server/genius-server`, Deck, Reviewkanäle Abacus und Kiro; manueller iPad-Modellclient über Tailscale (`tools/ipad_llm.py`, Live-Test am Gerät UNKNOWN); Refusal-Guard für drei langsame Module fokussiert; Project Deck | #110, #114, #116, #142, #153, #156 |
| Werkzeuge (20.10.) | Agentenauswahl (Laya-Shadow) und Control Deck nach Kaans Verfügbarkeitsmeldung: Codex, Gemini, Antigravity getrennt; Copilot, Kiro, Abacus pausiert bis zur Wiedervorlage am 03.11.2026, ohne Starter. Antigravity ist ein Client, kein zusätzliches Gemini-Kontingent. 1399 Tests, Demo 15/15 laut PR | #188 |

**Noch offen:** C5 (Betriebsanleitung mit systemd, Rotation und Betriebsabnahme des
Worker-Dienstes aus #140), E3 (frischer Klon auf einem echten Server), D1-Code, D2, D3
und E2 (Security-Review am Head). **D1-Code bleibt auf HOLD** (Entscheidung vom
18.10.2026); D2 und D3 sind nicht aktiv. #151 und #152 sind seit 21.10.2026 als inaktive
Bausteine gemergt (Nachtrag 21.10.2026); #148 und #176 sind nur Doku. Die
`ROADMAP-V02.md`-Spalte „Stand“ wurde mit #130 und #167 angeglichen; die Zellen C3, A2,
C5 und E2 (C3: `Claude DB Review` APPROVED am Merge-Head
`cc95586ca27e6f9d76fd737967d87cdb2e8a44d5`, `docs/DECISIONS.md` 04.10.2026; A2/C5:
getrennter Worker-Dienst aus #140, Betriebsabnahme HOLD) hat #177 am 21.10.2026 korrigiert.

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

## Nachtrag 21.10.2026

Geprüft gegen `main` `9385f5bd9dc43f277af8f3b0265c7b4d45cfbc71` (`git log`, GitHub-Abfrage
am 21.10.2026). Unterschieden werden vier Stufen: **gemergt** (Code oder Doku auf
`main`), **aktiv** (von einem Aufrufer im Dienst benutzt), **deployt** (auf dem Server
umgestellt) und **betrieblich abgenommen**. Für nichts in dieser Tabelle ist die dritte
oder vierte Stufe belegt.

| PR | Gemergt am | Inhalt | Stufe |
| --- | --- | --- | --- |
| #191 | 20.10.2026 | Regeltexte: Gemini statt Copilot | Regeln gemergt |
| #185 | 20.10.2026 | Wissenspflege 20.10.2026 | Doku gemergt |
| #177 | 21.10.2026 | Roadmap-Zellen C3, A2, C5, E2 korrigiert | Doku gemergt |
| #192 | 21.10.2026 | Control-Deck-Reparatur | gemergt; Dienst laut PR nicht umgestellt |
| #184 | 21.10.2026 | Audit-Delta: Sender prüft den eigenen Delta-Start | gemergt |
| #183 | 21.10.2026 | Text-Digest: Fallback begrenzt | gemergt |
| #150 | 21.10.2026 | Werkzeug für die Worker-Abnahme | gemergt; **Liveabnahme nicht belegt** (braucht Kaans GO) |
| #151 | 21.10.2026 | D2: Argon2id-Passwortgrenze (`portal_passwords.py`), Merge `1f1c5d9096ee4a47f3bc7880e8a8e463f67c2250` | **inaktiver Baustein** |
| #152 | 21.10.2026 | D2: Session-/CSRF-Token und Cookie-Helfer (`portal_session_tokens.py`), Merge `9385f5bd9dc43f277af8f3b0265c7b4d45cfbc71` | **inaktiver Baustein** |
| #197 | 21.10.2026 | Roadmap-Zeile D2 und Abgleichsabsatz, Merge `e37eb22ac643f92c9097e0dc03d20f3dfdf497d8` | Doku gemergt auf Kaans Anweisung; **ohne Gemini-Review am Head** |

**D2 im Einzelnen (Code geprüft an `9385f5b`):** Argon2id mit m=65536, t=3, p=4, Passwort
1 bis 1024 Byte; Token mit SHA-256-Digest und sessiongebundenem CSRF-Vergleich; Cookie
`__Host-`, `Secure`, `HttpOnly`, `Path=/`, `SameSite=Lax`, Max-Age 8 h. Beide Module
stehen in `GUARDED` mit eigenen Testmodulen. **Außer diesen Tests importiert sie kein
Code** in `geniusnew/`, `tools/` oder `scripts/`; es gibt keine Login-Route und keine
Session-Datenbank. Die Bausteine
sind also gemergt, aber nicht aktiv. Vor einem Login bleiben zu schließen: Mindestlänge
und Dummy-Hash, Login-Budgets, serverseitiger Ablauf, Rotation und Widerruf der Session,
Origin-/CSRF-Prüfung einschließlich des Bootstraps vor dem Login, ein eigener CSRF-Schlüssel
und die Entscheidung zum Cookie-Profil (Lax im Code gegenüber Strict im Vorschlag von
`docs/DATABASE.md`). **D1, D2 und D3 bleiben Pflicht der technischen Beta; #151 und #152 ergeben
keinen D2-PASS.** Die Roadmap-Zeile D2 ist mit #197 gemergt (21.10.2026, Merge-Commit
`e37eb22ac643f92c9097e0dc03d20f3dfdf497d8`); #198 (D1/D3-Zeilen, Abgleichsdokument) ist offen.

**Offene PRs mit Bezug zu Gates.** Die Angaben stammen aus den PR-Beschreibungen zum
genannten Head und wurden, außer wo „geprüft“ steht, nicht neu abgerufen:

| PR | Head | Stand |
| --- | --- | --- |
| #187 Advisory-Fehlerketten | `be4611c63894c4b04320e7386c3b9f403f30b6df` | Gemini-Flash-Review ohne Befund, `contracts` SUCCESS laut PR; Draft. Betrieb von `[ai_advisory]` bleibt STOP. Unabhängiges Claude-Review nach Übergabe |
| #194 B4-Negativtests | `2b5a6e525272cff17cfaa57771add50314a2e18e` | Gemini-Flash-Review am früheren SHA `7acb9e6` ohne Befund, CI am Head laut PR ausstehend; reiner Test-Diff, keine neue DB-Code-Freigabe |
| #181 Dependency-Checker | `35699c907419960304813097fd01e24b3a201c5c` | Gemini-Review A–E PASS laut PR, CI am Head laut PR zum Zeitpunkt offen |
| #182 E3-Runner | `a3338ac66aa6cd3b5465c68885bbb37616479c28` | Gemini-Flash A–C PASS, `contracts` grün laut PR; Pro-Modell-Review und Live-E3 offen |
| #195 Entscheidungsdesk | `dbe4c3c29af0eee58deec88d6c0ea74d16831093` | Codex-Folgecommit (Archivierung des Journals, Befund 7). Laut PR und Check-Runs: `contracts` und Refusal-Guard SUCCESS. Claude-Review am vollen Head ohne Critical/High/Medium (PR-Kommentar). Gemini-Review am Head ausstehend. Technischer Owner nach Übernahme: Codex |
| #196 E2-Checkliste | `531b8f0d642b9183c80add3972026170d07affaf` | Claude-Review ohne Critical/High (Kommentar im PR); Gemini und CI offen. Die Checkliste ist kein E2-Abschluss |
| #197 Roadmap D2 | `3e083ee2421b57130b2f310542b00a53e7d017f7` | **gemergt am 21.10.2026** auf Kaans Anweisung (Merge-Commit `e37eb22ac643f92c9097e0dc03d20f3dfdf497d8`); CI SUCCESS, **kein Gemini-Review am Head** (nachzuholen) |
| #198 Portal-Doku | `b066b5509b0c93f89202b58494ba6cbb16f5ade4` | offen, Draft. Roadmap-Konflikt mit dem gemergten #197 vom Branch-Owner (Codex) gelöst; der STATUS-Hunk ist entfernt, der Diff umfasst laut PR nur `docs/PORTAL-GATE-STATUS-20261021.md` und `docs/ROADMAP-V02.md`. CI und Gemini-Review am Head ausstehend |
| #200 CI-Matrix, Ruff, Audit | `ac709dde7fab708f6f4a3832d3fc8f61fcfbaeac` | offen, Draft. Neue Dev-Abhängigkeit Ruff (`requirements-dev.txt`): Gemini-Pro-Sicherheitsreview und Kaans OK fehlen. Laut PR GitHub-CI bestanden |
| #201 Gemini über Antigravity | `8a75a7387ab96944a025ed1ad4ea732e3a85ecfd` | offen, Draft. Ändert auch die Wissensdateien; Reihenfolge laut Koordination: #199 vor #201, danach bereinigt der Branch-Owner die abgedeckten Hunks. Gemini-Review am Head ausstehend |
| #161 D1-Datenbankentwurf | `06394d2e94cd1f0ca3cbcbe5fe96766a7e3a2025` | HOLD-Entwurf von ChatGPT. Claude-Security-Review der Nachbesserung ohne Critical/High; N-A (Core-Tagesgrenze für Ablehnungen) bleibt als Gate offen. Freigabe nur durch Kaan |

**Messwerte:** Für `main` `9385f5b` liegt keine neue Testzahl vor: UNKNOWN. Zahlen aus
den PR-Beschreibungen gelten nur für deren Heads (z. B. #195: 1480 Tests, Demo 15/15 am
Head `2884be5`, gemessen auf dem Stand der Basis `f297bc3`; für den Folgecommit `dbe4c3c` nennt der PR 1611 Tests laut GitHub-CI).

**Offene PRs:** am 21.10.2026 sind 32 offen (GitHub-Abfrage).

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
- **Die CI testet nur Python 3.11** (`.github/workflows/verify.yml`). Entschieden am
  20.10.2026: Matrix 3.11 und 3.13; umgesetzt ist das noch nicht.
- **Kein Linter, keine Typprüfung, keine Coverage-Messung** in der CI. Entschieden am
  20.10.2026: `ruff` (nur `E9`/`F`) und `pip-audit` ja, Typprüfung vorerst nein,
  Coverage nein (`docs/DECISIONS.md`); umgesetzt ist das noch nicht.

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

- Ollama 0.40.1 läuft als User-Dienst `genius-local-ai.service` und lauscht nur auf
  `127.0.0.1:11434`.
- Cgroup des Prozesses: `memory.max` 6442450944 (6 GiB), `memory.high` 5368709120
  (5 GiB). `systemctl show genius-local-ai` ohne `--user` zeigt `infinity` und ist
  dafür der falsche Weg.
- `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=2m`.
- Modelle: `qwen3.5:4b-q4_K_M` 3.3 GB, `qwen3.5:9b` 6.6 GB, `gemma4:e2b` 4.6 GB,
  `qwen2.5-coder:3b` 1.9 GB, dazu `qwen2.5-coder:1.5b` (geladen 1.03 GiB).
- Mini-Inferenz mit `qwen3.5:4b-q4_K_M`: Antwort `OK`, 13 s. Qwen 4B allein erreicht
  `memory.high`; im Test stieg der `high`-Zähler um 1571. Beide Modelle gleichzeitig
  sind nicht gemessen (UNKNOWN), im Wechselbetrieb gab es kein OOM.
- Tailscale läuft. (Das Peer-Inventar stand hier bis 20.10.2026; es ist aus diesem
  öffentlichen Dokument entfernt, weil privates Netzinventar nicht hierher gehört.)

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

1. Offene PRs, Konflikte und Merge-Gates bereinigen (Abschnitt „Aufräumen“ unten),
   parallel zur Weiterentwicklung; die Pause entfällt (Entscheidung 20.10.2026).
2. ~~`Claude DB Review` für B4 (#94) und B5 (#95) nachholen~~ — entschieden am 18.10.2026: nicht separat, E2 deckt es ab. Das Gemini-Sicherheitsreview zu B4/B5 ist am 20.10.2026 nachgeholt (B5 ohne Befund; B4: Test-PR für Negativzweige, Codex).
3. CI nach der Entscheidung vom 20.10.2026: Matrix 3.11/3.13 und `ruff`-Job mit
   `requirements-dev.txt` (Codex, ein PR); `pip-audit`-Vorlage nach
   `.github/workflows/audit.yml` verschieben (Kaan, braucht Workflow-Rechte).
   #151 ist seit 21.10.2026 gemergt.
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
   als Draft, mit `main` konfliktbehaftet; #165 ohne Merge geschlossen); Uhr-Grenze als
   eigener `SECURITY.md`-PR, Zahlen fürs Lastbudget zur Freigabe, Gate 10. **Gate 6 am
   20.10.2026 bestanden** (Claude, an `main` `0e6e0db`, Kommentar in Issue #159: kein
   Critical/High, 1 Medium zur Audit-Kapazität, 2 Low). Erst danach D1-Code (Codex),
   dann D3. D2-Bausteine (#151, #152) sind seit 21.10.2026 als inaktive Bausteine
   gemergt; Login-Integration und Auflagen bleiben offen (Nachtrag 21.10.2026).
10. E2: Security-Review des vollständigen Heads (Claude und Gemini; seit 20.10.2026
    Gemini statt Copilot).
11. KI-Pilot: P1 ist mit #179 auf `main` (identisch mit #172, das damit überholt ist).
    Tailscale-Entwurf #171 (ChatGPT) muss B3 = TLS (Kaan, 19.10.2026) übernehmen; ACL,
    `tailscale serve`, Zertifikat und Firewall brauchen je Kaans GO. Laptop- und
    iPad-Prüfung auf den Geräten (Kaan).
12. Nach #179: (a) M1 ist in #187 behoben; Claude und Gemini fanden am Head zwei weitere
    Stellen derselben Klasse (UnicodeError-Kontext, `BaseException`), Gemini stuft sie
    High ein → Codex behebt in #187. (b) Prozessgrenze: Entwurf #186 hat am 20.10.2026
    den Gemini-Design-PASS (alle Bereiche PASS, 1 Medium zur HOLD-Regel, deckungsgleich
    mit Claudes Review). Vor dem Code-PR: Kaans OK zur ersetzten `SECURITY.md`-Zeile und
    zur Frage, ob Gemini 4 Argon (High) als Pro-Modell gilt. `enabled = true` bleibt im
    Betrieb STOP.
13. Roadmap: C3, A2, C5 und E2 korrigiert in #177 (gemergt 21.10.2026).

## Reviews und Gate-Stand vom 20.10.2026 (Sammelauftrag)

Ausgeführt von Claude Code. Claude-Reviews mit lokaler Reproduktion; Gemini-Reviews über
die Antigravity-CLI (`gemini-4-argon-high`, nur lesend, Ausgabe unverändert im PR). Jede
Zeile gilt nur für den genannten Head.

| PR | Head | Claude | Gemini | Nächster Schritt |
| --- | --- | --- | --- | --- |
| #187 Advisory-Exception | `66cd1c2` | 2 Low (UnicodeError-Kontext trägt die Modellantwort in `__context__.object`; `BaseException` der Engine) | dieselben 2 als **High** | Codex behebt in #187 |
| #181 Dependency-Checker | `8848429` | 1 Medium (Versions-Token und Antwort nicht an den Pin gebunden, reproduziert: Exit 0 für fremden Release), 1 Low | 4 **High** (davon 2 deckungsgleich; neu: ungetestete Ablehnung Zeile 121–122, ungeprüfte `id`/`summary`-Typen) | ChatGPT behebt in #181 |
| #182 E3-Runner | `0976fbf` | 1 Medium (keine Bindung an den freigegebenen Kandidaten-SHA), 1 Low | Bindung **High**, Markdown-Ausbruch Medium, 1 Low | ChatGPT behebt in #182 |
| #183 Text-Digest | `44312c8` | keine (Suche bis 15 517 von 16 384 Byte ohne Ablehnung) | 1 Medium (Kern-Sätze nach Zeichen) | nicht blockierend |
| #184 Audit-Delta | `baf9b83` | keine | keine | Kaan mergt (ChatGPT-PR) |
| #186 Advisory-Prozess (Entwurf) | `81cee08` | 1 Medium (HOLD nach jedem REFUSE), 2 Low | Design-PASS, dasselbe Medium | Kaans OK (`SECURITY.md`-Zeile), dann Code-PR |
| #189 Harness² | `758ad49` | M1-Rest Medium (manipuliertes Loose Object wird ausgeführt, reproduziert), FIFO-Hänger und fehlende rlimits Low | dieselben 3 als **High**, 1 Medium | ChatGPT behebt in #189 |
| #150 Worker-Abnahme-Werkzeug | `0a837c7` | keine (Signal-/`/proc`-Pfad) | keine | Codex darf mergen; Liveabnahme braucht Kaans GO (Ablauf im PR) |
| #191 Gemini statt Copilot | `daa0566` | eigener PR | keine | Kaan mergt |
| D1-Entwurf (Gate 6) | `main` `0e6e0db` | kein Critical/High; 1 Medium (Audit-Kapazität als Aktivierungsgate), 2 Low | — | Kommentar in Issue #159 |

Weitere Ergebnisse: #117 (C5) — die Core-Unit erzeugt mit `ProtectSystem`/`ProtectHome`/
`PrivateTmp` eine eigene Mount-Namespace, die der Worker-Hostnachweis aus #140
(`_HOST_NAMESPACES`) ablehnt; Lösung braucht Kaans Entscheidung (Empfehlung im PR:
Unit ohne Mount-Namespace-Optionen, Ausgleich über seccomp-basierte Optionen und
Dateirechte). #151 und #152: einziger Konflikt war `scripts/refusals.py`; beide sind am
21.10.2026 gemergt, der Auftrag ist erledigt. #147: Copilot-Review am älteren Head gilt weiter
(Codec und Tests seitdem byte-gleich), Merge aber HOLD wie D1-Code.

**Gates bis v0.2 (Stand 20.10.2026):**

| Gate | Stand | Quelle |
| --- | --- | --- |
| A1 | PASS | #57 |
| A2 | Code PASS (#76, #140); Betriebsabnahme HOLD; synthetische VM-Probe PASS (#173, nur synthetisch, Security-Review dort CHANGES REQUIRED) | #140, #150, #173 |
| B0–B3, B6 | PASS | `docs/ROADMAP-V02.md` |
| B4, B5 | gemergt; `Claude DB Review` durch E2 abgedeckt (Entscheidung 18.10.2026). Gemini-Sicherheitsreview am 20.10.2026 nachgeholt: B5 ohne Befund; B4 mit Testlücken in den Negativzweigen von `reserve_admitted`/`dispatch_available` und `_expire` (Folgeauftrag an Codex in #94). Pro-Modell-Frage offen | #94, #95 |
| B7 | gemergt; unabhängiges Review UNKNOWN | #106 |
| C1, C2, C4 | PASS | #61, #62, #71 |
| C3 | PASS | #111 |
| C5 | BLOCKED: Core-Unit gegen Worker-Hostnachweis (Kaans Entscheidung), Schlüsselrotation, Weg nach Restore | #117 |
| D1 | HOLD (Entwurf Gate 6 PASS; Gates 2, 4, 5, 10 und DB-Vertrag #161 offen) | Issue #159 |
| D2 | offen; #151 und #152 sind seit 21.10.2026 inaktive Bausteine auf `main`, keine Login-Route, keine Session-DB (Nachtrag 21.10.2026) | #151, #152 |
| D3 | HOLD (Entwürfe #148, #176) | `docs/DECISIONS.md` 20.10.2026 |
| E1 | PASS | #112 |
| E2 | offen | — |
| E3 | offen; Runner #182 braucht Kandidatenbindung; kein Lauf auf frischem Host | #182 |

## Aufräumen der offenen PRs (Stand 20.10.2026)

Auftrag von Kaan vom 09.10.2026 (`docs/DECISIONS.md`). Am 17.10.2026 hat Claude Code mit
Kaans OK ohne Merge geschlossen:

| PR | Head | Grund |
| --- | --- | --- |
| #101 | `97b400d` | Die drei Tests sind über #97 (`f220e30`) schon auf `main` |
| #109 | `42a5aeb` | Rollenmodell überholt durch die Regeln vom 04.10.2026; Konflikt in allen drei Dateien |
| #134 | `cbf32f6` | Regeländerung ohne belegte Kaan-Entscheidung und ohne die nötigen Reviews; schwächt Schutzregeln ab (Entscheidung 07.10.2026) |
| #118 | `8ab7fca` | Überholt durch #129 und #130; die C3-Entscheidung steht jetzt in `docs/DECISIONS.md` |

Außerdem ohne Merge geschlossen, nicht von Claude: #121 (CI-Runner), #143 und #146
(automatische CodeRabbit-PRs). Am 20.10.2026 sind 36 PRs offen. Zuordnung der übrigen
(Claude Code, 20.10.2026; geschlossen wird nur mit Kaans OK):

| PR | Zuordnung |
| --- | --- |
| #172 | überholt: alle Dateien sind über #179 byte-gleich auf `main`; ohne Merge schließen |
| #175 | Entscheidungszeile in #185 übernommen; ohne Merge schließen |
| #190 | Inhalt in #185 (`STATUS.md`) und #191 (`COLLABORATION.md`) übernommen; ohne Merge schließen |
| #144, #173 | synthetische OS-Probe (eigener Code bzw. Workflow); keine Betriebsabnahme. #173: Security-Review CHANGES REQUIRED an `030b5c7` noch offen (ChatGPT). #144 konfliktbehaftet, braucht unabhängiges Review; Wert nach der Liveabnahme aus #150 neu bewerten |
| #155 | noch erforderlich (echte Angriffstests); Codex-Review ohne Befund, Gemini-Review angefordert |
| #161 | noch erforderlich (DB-Vertrag D1, Issue #160); Konflikt mit `main` in `docs/DATABASE.md`, ChatGPT |
| #171 | noch erforderlich; B3 = TLS nachtragen (ChatGPT) |
| #136 | noch erforderlich (inkrementelles Audit, Kapazitätsrisiko); Konflikt, Gemini-Design-Vorprüfung, Claude-Review, Kaans Freigabe offen |
| #125 | blockiert: LLM-Worker ist laut Roadmap nicht in v0.2; ändert `SECURITY.md` |
| #99, #131, #133, #138, #149, #157, #115 | Zusatzwerkzeuge und Forschung; später. #115 und #149 teils durch #188 überholt; #133 und #149 mit roter CI |
| #128, #139, #141 | Regel-/Strukturentwürfe; warten auf Kaans schriftliche Entscheidung (unten) |

Offene Fragen:

- **Erledigt am 20.10.2026** (`docs/DECISIONS.md`): Die Pause entfällt; die
  D3-Entscheidung aus #148 ist bestätigt und eingetragen; #151 hat Kaans GO für die neue
  Abhängigkeit (Merge nach Konfliktauflösung und grüner CI; #151 am 21.10.2026 gemergt).
- **Erledigt am 20.10.2026** (`docs/DECISIONS.md`): Gemini übernimmt projektweit die
  Copilot-Reviewrolle, auch für offene PRs; Regeltexte in #191. Frühere Einzelausnahmen
  (#145 laut #153, Kommentar 6244844507; #188) sind damit allgemein abgelöst.
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
Infrastruktur. **Seit 20.10.2026 reviewt Gemini jeden PR** (statt Copilot). Microsoft 365
Copilot liest aus OneDrive. **Claude Code besitzt `docs/COLLABORATION.md` und
`docs/DECISIONS.md`**, sorgt für Ordnung und Struktur, löst Konflikte zwischen den
Plattformen mit Überschreibrecht und hilft, wenn Codex feststeckt.

| Werkzeug | Rolle | Liest |
| --- | --- | --- |
| Gemini Pro | Review jedes PRs (seit 20.10.2026, statt Copilot) über Antigravity-, Abacus- oder Gemini-CLI; Pflicht-Zweitmeinung bei Ausnahmen, Design-Vorprüfung | `GEMINI.md`, `.gemini/styleguide.md` |
| NotebookLM | **Wissensdatenbank**: Auskunft für alle, mit Quelle | dieses Dokument, `docs/DECISIONS.md`, `SECURITY.md`, `docs/ROADMAP-V01.md`, `docs/COLLABORATION.md`, `AGENTS.md` |
| ChatGPT Pro / Codex | **Kerncode-Umsetzung**, ein Thema pro PR (`codex/<thema>`), mit Wissensblock | `AGENTS.md`, `docs/COLLABORATION.md` |
| ChatGPT (Chat) | **Neue Werkzeuge, Server-Pflege, Infrastruktur, Integrationen** (`chatgpt/<thema>`), NotebookLM + eigenes Notebook; Kaan mergt | `AGENTS.md`, `docs/COLLABORATION.md` |
| GitHub Copilot Pro | Editor; seit 20.10.2026 kein Pflicht-Reviewer mehr (laut Kaan bis 03.11.2026 nicht nutzbar) | `.github/copilot-instructions.md` |
| Kiro-CLI | Unabhängige Zweitprüfung für Claudes eigenen Code, nur lesend; laut Kaan bis 03.11.2026 pausiert (Abacus ebenso) | `docs/COLLABORATION.md` |
| Microsoft 365 Copilot | Berichte, E-Mails, Folien | OneDrive-Ordner `GeniusNew` |
| Claude Code | **Besitzt `docs/COLLABORATION.md`/`docs/DECISIONS.md`, Ordnung, Konfliktlöser mit Überschreibrecht**, Sicherheits-Review von Kaans DB-Entwurf und Codex' DB-Code, Hilfe bei Hilferuf | `AGENTS.md`, `docs/COLLABORATION.md` |

Übergaben zwischen den Werkzeugen laufen über die zwei Prompts in `docs/HANDOVER.md`.

Regel für alle: Jede Änderung läuft über einen PR mit grüner CI. Codex mergt eigene
Kerncode-PRs selbst; ChatGPT- und Claude-eigene PRs mergt Kaan; neue Abhängigkeiten,
geänderte `SECURITY.md`-Grenzen und Tags brauchen Kaans OK.
