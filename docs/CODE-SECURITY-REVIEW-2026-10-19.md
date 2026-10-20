# Code- und Security-Review 2026-10-19 (extern, Arena)

**Geprüfter Stand:** `main` @ `e8a00bf267723bc9f187e4b2df535b3569190b0b` (Merge PR #167)
**Datum:** 19.10.2026 · **Art:** lesendes Review von Code, Tests, CI und Doku — **kein** Pentest,
**keine** Änderung am Kerncode. Grenzen schließen ist laut `AGENTS.md`/`SECURITY.md` je ein
eigener PR mit Kaans OK; dieses Dokument ist die Entscheidungsvorlage dafür.

**Nicht geprüft (UNKNOWN):** kein Testlauf in dieser Umgebung (Deps/DB nicht installiert,
nur `compileall` OK), CI auf `main` lief zum Prüfzeitpunkt noch, Branch-Protection per API
nicht lesbar (403), kein Live-Test von Landlock/systemd auf dem echten Server (E3 offen).

---

## 1. Gesamturteil

GeniusNew ist ein **außergewöhnlich sauber gebauter Zero-Trust-Kern** — und gleichzeitig
**noch kein Produkt und nicht produktionsreif**. Beides ist kein Widerspruch: Das Projekt
hat bisher fast seine ganze Energie in die Vertrauensgrenzen gesteckt (Signaturen,
Ablehnungen, Audit-Kette, Isolation) und fast keine in Durchsatz, Betrieb und Nutzen
hinter dem Gate. Bildlich: **Das Schloss ist exzellent. Das Haus dahinter fehlt noch.**

| Bereich | Note (0–10) | Kurzgrund |
| --- | ---: | --- |
| Architektur / Security-Design | **9** | §8-Rollentrennung, Default-Deny, keine Selbstbestätigung, ehrliche `SECURITY.md` |
| Codequalität Kern | **8,5** | Strikte Verträge, Fail-Closed überall, Kommentare erklären das *Warum* |
| Tests / Nachweis | **9,5** | 1 244 Tests, ~19 200 Testzeilen vs. ~9 900 Kernzeilen, Refusal-Guard (Mutation) |
| Security im Betrieb (Reife) | **6** | Ein Root-Secret, ein Worker-Schlüssel, Anker rückschneidbar, C5/E2/E3 offen |
| Performance / Skalierung | **4** | Audit-Append O(n), eine DB-Verbindung, harte Lebensdauer-Obergrenze der Kette |
| Wartbarkeit | **7** | Klare Module, aber 4 Dateien > 800 Zeilen, keine Typ-/Lint-Prüfung |
| DX / Onboarding | **6** | Quickstart ehrlich, aber DB-Pflicht für Tests, kein `pyproject.toml`, kein Diagramm |
| Dokumentation | **8** | Sehr ehrlich und tief — aber zu viel: ~6 000 Zeilen, 4 DRAFTs, STATUS altert schnell |
| Produktionsreife gesamt | **5** | „Technisch beta-ready" (v0.2) ist glaubwürdig nah, Produktion ist es nicht |
| **Gesamt** | **7,5** | **Exzellenter Sicherheits-Prototyp auf dem Weg zur Beta — nicht mehr, nicht weniger** |

**Meine Meinung in einem Satz:** Für ein Ein-Personen-Projekt mit KI-Werkzeugen ist die
Sicherheitsdisziplin (Ablehnungstests, Hash-Pinning, offene Grenzen als Tests) besser als
in den meisten Firmen-Codebasen, die ich kenne — das größte Risiko ist nicht ein Loch im
Kern, sondern **Scope-Zersplitterung** (Portal, KI-Pilot, Control Deck, A2/C5 parallel),
**Betriebslücken** (Rotation, Observability, Last) und die **Lebensdauer-Grenze der
Audit-Kette**, die einen Dauerbetrieb heute physikalisch beendet.

---

## 2. Was nachweislich gut ist (Stärken mit Beleg)

1. **Fail Closed ist System, kein Slogan.** Geschlossene Key-Sets, strikte Typen
   (`type(x) is int`, Bool ausgeschlossen), Größen-/Tiefenlimits, kanonisches JSON
   (`geniusnew/contracts.py`). Keine `eval`/`exec`/`pickle`/`shell=True` im Kern (grep).
2. **Echte Schlüsseltrennung.** Ed25519 überall; Prüfer halten nur Public Keys und
   verweigern Konstruktion mit der privaten Hälfte (`contracts.py`, `results.py`, `audit.py`).
3. **Identität nie aus dem Request.** API-Key → Digest-Lookup (kein Timing-Loop, kein
   Klartext), Job-ID aus `secrets`, Körper mit genau einem Feld, einheitliches
   `409 REJECTED` ohne Existenz-Orakel (`geniusnew/http_entry.py`).
4. **Vergleich in konstanter Zeit** (`hmac.compare_digest`) an allen sicherheitsrelevanten
   Stellen; SQL ausschließlich parametrisiert (`%s`), Konstanten-Konkatenation nur für
   feste Spaltenlisten (`geniusnew/database.py`).
5. **Worker-Isolation in Schichten:** Audit-Hook + Seccomp-BPF (Kill bei
   fork/exec/clone ohne Thread) + Landlock ABI ≥ 4 (FS-Allowlist, kein TCP) + rlimits +
   `no_new_privs` — und **fail closed** ohne diese Voraussetzungen (`isolation.py`).
6. **Audit-Kette + externer Anker** mit signiertem Kopf, Nonce-gebundenen Antworten,
   Persistenz- und Restore-Angriffsdemos in CI (`audit_chain.py`, `anchor_process.py`,
   `scripts/demo_persistence.py`, `scripts/demo_restore.py`).
7. **Der Refusal-Guard ist das Highlight.** `scripts/refusals.py` schaltet jede Ablehnung
   einzeln ab und verlangt ein rotes Testergebnis — das ist Mutation-Testing für
   Sicherheitsentscheidungen und branchenweit selten so konsequent (CI-Matrix pro Modul).
8. **Supply Chain im Kern vorbildlich:** nur 2 direkte Deps (`cryptography`, `psycopg`),
   alles hash-gepinnt (`--require-hashes`), Actions per SHA gepinnt, `permissions: contents: read`.
9. **Konfiguration als Angriffsfläche ernst genommen:** geschlossene TOML-Keys, nur
   Loopback, Secret-Datei `0600`, veröffentlichtes Demo-Secret wird verweigert (`config.py`).
10. **Ehrlichkeit als Prozess:** `SECURITY.md` listet Grenzen samt Tests, die sie „offen
    halten" (`…_and_this_is_the_boundary`). Wer eine Grenze schließt, muss den Test umkehren.

---

## 3. Schwachstellen-Übersicht

Schwere: **P0** = vor Beta/Produktion fixen · **P1** = in v0.2 fixen · **P2** = geplant danach ·
**P3** = Hygiene. Aufwand: S ≤ 1 Tag, M ≤ 1 Woche, L ≤ 1 Monat, XL > 1 Monat.

| ID | Schwere | Befund | Hauptdatei |
| --- | --- | --- | --- |
| S-01 | P0 | Alle Schlüssel aus **einem** Root-Secret; **ein** Worker-Schlüssel für alle Worker | `keys.py`, `results.py` |
| S-02 | P1 | Kerninstanzen in **einem Prozess** — Trennung nur logisch | `wiring.py` |
| S-03 | P1 | Anker-Zustand auf älteren gültigen Kopf **rückschneidbar**; Anker = Single Point of Failure | `anchor_process.py` |
| S-04 | P1 | Worker-Isolation: Seccomp ist Deny-Liste, UDP/Unix nur per Hook, Runtime lesbar | `isolation.py` |
| S-05 | P1 | API-Keys: min. 16 Byte, SHA-256 ungesalzen, kein Ablauf/Widerruf/Rotation | `http_entry.py` |
| S-06 | P1 | **Uhr ist Vertrauensbasis** (TTL ohne Skew-Toleranz/NTP-Anforderung) | `contracts.py` |
| S-07 | P1 | `http_entry.serve()` erlaubt programmatisch Nicht-Loopback (nur Config verweigert) | `http_entry.py` |
| S-08 | P1 | Control Deck: **kein Login**, startet Befehle, CSP `unsafe-inline`, Tailscale-CGNAT-Annahme | `tools/control_deck/` |
| S-09 | P2 | Kein TLS im Kern = Deployment-Fußangel; Proxy-Vorlage gut, aber ohne mTLS/Client-Identität | `docs/REVERSE-PROXY.md` |
| S-10 | P2 | CI: `postgres:17` nicht per Digest gepinnt, `trust`-Auth, kein SAST/`pip-audit`/Dependabot | `.github/workflows/verify.yml` |
| P-01 | P0 | Audit-Append **O(n)**: jeder Append liest/prüft ganze Kette + ganze Kette zum Anker | `audit_store.py` |
| P-02 | P0 | **Lebensdauer-Decke:** Anker 16 MiB ⇒ grob ≤ ~65 000 Köpfe, danach Totalverweigerung | `anchor_process.py` |
| P-03 | P1 | **Eine** DB-Verbindung für alles (B6-Atomarität) ⇒ einspuriger Durchsatz, Abbruch = Neustart | `__main__.py`, `wiring.py` |
| P-04 | P1 | Synchrones Job-Modell: HTTP wartet bis 30 s auf Worker; kein Queue-/Status-API | `http_entry.py`, `orchestrator.py` |
| O-01 | P1 | **Keine Observability:** kein Logging-Framework, keine Metriken, kein Health/Readiness | gesamter Kern |
| O-02 | P1 | Kein belegter Betrieb: C5 (systemd/Rotation), E2 (Review am Head), E3 (Frischklon) offen | `docs/ROADMAP-V02.md` |
| M-01 | P2 | Monolithen: `worker_service.py` 1 552, `wiring.py`/`database.py`/`anchor_process.py` ~830 Zeilen | s. links |
| M-02 | P2 | Kein `pyproject.toml`, kein Linter/Typcheck/Coverage in CI (bewusst vertagt, STATUS Punkt 3) | Repo-Root |
| M-03 | P2 | CI testet nur Python 3.11; lokal läuft 3.13 — CI sichert das nicht ab | `verify.yml` |
| M-04 | P2 | Testsuite braucht echten PostgreSQL-Cluster → hohe Einstiegshürde, langsame Feedback-Schleife | `tests/postgres_support.py` |
| G-01 | P2 | **Keine LICENSE** trotz Public Repo; keine Repo-Description, kein `CODEOWNERS` | GitHub |
| G-02 | P2 | Scope-Zersplitterung: Portal (D), KI-Pilot (#166/#172), Deck, A2/C5 — 10 offene PRs parallel | GitHub/STATUS |
| G-03 | P3 | Wissensbasis teils extern (NotebookLM/OneDrive) + Bus-Faktor 1 trotz vieler KI-Rollen | `docs/COLLABORATION.md` |

---

## 4. Schwachstellen im Detail — mit Fix und Abnahmetest

### S-01 · P0 · Ein Root-Secret signiert alles, ein Worker-Schlüssel für alle

**Befund:** `keys.py` leitet Handoff-, Ergebnis- und Audit-Schlüssel per HMAC-Label aus
einem ≥ 32-Byte-Root-Secret ab; `results.py` nutzt einen Ergebnis-Schlüssel für alle
Worker. Wer das Root-Secret liest, kann Handoffs, Ergebnisse und Audit-Köpfe fälschen —
die §8-Trennung kollabiert auf eine Datei. Ein kompromittierter Worker kann Ergebnisse
*fremder* Worker signieren. (In `SECURITY.md` korrekt als Grenze benannt.)
**Fix (L):** (1) Pro Rolle einen unabhängig erzeugten Schlüssel (Ed25519-Keygen statt
Ableitung), Ablage je Rolle getrennt (eigene Datei/eigener OS-Nutzer, `0600`; perspektivisch
systemd-Credentials/KMS). (2) Pro `worker_agent_id` ein eigener Ergebnis-Schlüssel;
Verifier lernt `worker_agent_id → Public Key`. (3) Rotationsablauf als Teil von Gate C5:
neue Schlüsselgeneration + alte nur noch zum Prüfen historischer Annahmen (löst zugleich
die Policy-/Schlüsselbindung gespeicherter Annahmen, `SECURITY.md`).
**Abnahme:** Test „Ergebnis von Worker A mit Schlüssel B signiert → abgelehnt" (neu);
bestehender Boundary-Test wird umgekehrt; Demo bleibt PASS mit getrennten Schlüsseldateien.

### S-02 · P1 · Ein Prozess für fast alle Instanzen

**Befund:** Außer Worker und Anker laufen Orchestrator, Gateway, Verifier und HTTP in
einem Speicher (`wiring.py`). Ein Speicher-/Logikfehler oder eine kompromittierte
Abhängigkeit besitzt alle Rollen zugleich. Für v0.1 explizit entschieden, für Beta das
größte Architektur-Restrisiko.
**Fix (XL, nach v0.2):** Wie beim Anker (C2) vorgehen: Gateway und Verifier als eigene
Prozesse/Nutzer über Unix-Socket mit signierten, nonce-gebundenen Nachrichten. Kleinster
erster Schritt (M): Verifier auslagern (hält nur Public Keys, kleinste Angriffsfläche).
**Abnahme:** E2E-Test läuft mit 4 getrennten Prozessen; Kill des Gateway-Prozesses ⇒
Dienst verweigert (fail closed), kein Fallback in den Kernprozess.

### S-03 · P1 · Anker rückschneidbar + Single Point of Failure

**Befund:** Wer die `anchor.state` schreiben kann (Ankernutzer, `root`, zurückgespieltes
Backup), kann auf einen älteren, gültig signierten Kopf zurückrollen — per Test offen
gehalten. Zusätzlich: Fällt der Anker aus, steht der Dienst (gewollt fail closed, aber
ohne Betriebsantwort).
**Fix (L):** Externer Zeuge: letzter Kopf periodisch signiert an eine zweite, unabhängige
Stelle spiegeln (z. B. zweiter Host append-only, signierter Git-Commit, Transparency-Log
nach Sigsum-Muster). Start verweigert, wenn lokaler Kopf < Zeugenkopf. Betriebsseitig:
Anker unter eigenem Nutzer Pflicht im `serve`-Pfad dokumentieren + Monitoring/Alarm.
**Abnahme:** Boundary-Test umkehren: zurückgeschnittene Datei + neuerer Zeugenkopf ⇒
Start verweigert; Restore-Demo (`demo_restore.py`) deckt den Fall ab.

### S-04 · P1 · Worker-Isolation ist stark, aber keine Sandbox im engeren Sinn

**Befund:** Seccomp tötet Prozessstart (Deny-Liste bekannter Syscalls), Landlock sperrt FS
+ TCP. ABER: UDP/Unix-Sockets blockt nur der Python-Audit-Hook; bereits geladener nativer
Code kann rohe Syscalls absetzen (Hook sieht sie nicht); Seccomp erlaubt standardmäßig
alles Nichtgelistete; der Worker darf Python-Runtime, `geniusnew/` und das Worker-Modul-
Verzeichnis lesen (Code-Offenlegung; Secrets *neben* dem Modul wären lesbar, A2-Doku).
**Fix (M–XL):** Stufe 1 (M): Seccomp auf Allow-Liste umstellen (nur was der Interpreter +
Worker braucht), `socket`-Syscalls familienweise sperren (AF_INET/AF_INET6/AF_NETLINK),
Mount-/PID-/Net-Namespace + leeres Net. Stufe 2 (XL, Zielbild): microVM (Firecracker)
oder gVisor pro Job. Stufe 0 sofort (S): Installationsregel „keine Secrets im
Worker-Modul-Verzeichnis" per Startprüfung erzwingen (Pfadvergleich gegen Config-Pfade).
**Abnahme:** Neue Angriffstests: UDP-Send, Unix-Socket-Connect, `ctypes`-Syscall,
Lesen einer Canary-Datei neben dem Modul — alle müssen scheitern (PR #155 geht hier voran).

### S-05 · P1 · API-Key-Lebenszyklus fehlt

**Befund:** Schlüssel ≥ 16 Byte, gespeichert als ungesalzener SHA-256 (für maschinell
erzeugte Hochentropie-Keys begründet und okay). ABER: 16 Byte lassen menschengewählte
Keys zu (dann offline brute-forcebar, falls die Config leakt); kein Ablauf, kein Widerruf,
keine Rotation, kein Key-ID-Konzept — ein geleakter Key gilt für immer, bis die Config
geändert und der Dienst neu gestartet wird.
**Fix (M):** Mindestlänge auf 32 Byte anheben + CLI `geniusnew new-api-key` (aus `secrets`),
Digest-Einträge mit `not_after` und Key-ID, Widerrufsliste in der Config, Rotation mit
Überlappungsfenster (zwei gültige Digests pro Principal). Für Portal→Kern (D1) signierte
Requests/mTLS statt Bearer.
**Abnahme:** Tests: abgelaufener/widerrufener Key → `UNAUTHENTICATED`; 16–31-Byte-Key →
Start verweigert; Rotation ohne Downtime im E2E-Test.

### S-06 · P1 · Systemuhr ist undeklarierte Vertrauensbasis

**Befund:** `contracts.py` verweigert `issued_at > now` und `now >= expires_at` (TTL 60 s).
Der Kern ist uhrenfrei konstruiert (`now` als Argument — gut!), aber der Dienst füttert ihn
mit der Wanduhr: NTP-Manipulation/Sprung verlängert Replay-Fenster oder legt den Dienst
lahm; keine Skew-Toleranz, keine Monotonie-Prüfung, keine NTP-Anforderung in der
Betriebsanleitung.
**Fix (S–M):** (1) Betriebsvertrag: `systemd-timesyncd`/Chrony Pflicht, Start-Check
„Uhr synchronisiert" (sofern feststellbar). (2) Monotone Untergrenze: größtes je gesehenes
`now` persistent merken; rückwärtslaufende Uhr → Start/Dispatch verweigern.
(3) Optional kleine Zukunftstoleranz (≤ 5 s) nur gegen dokumentierten Skew.
**Abnahme:** Test mit rückwärts springendem `now` nach Neustart ⇒ Verweigerung (neu).

### S-07 · P1 · `serve()` selbst erlaubt Nicht-Loopback

**Befund:** `config.py` erzwingt Loopback — aber die öffentliche Funktion
`http_entry.serve(entry, host=...)` akzeptiert jede nichtleere Zeichenkette. Wer die
Bibliothek ohne Config-Pfad einbettet, kann den TLS-losen Eingang versehentlich ans Netz
hängen. Defense-in-Depth-Lücke, kein akutes Loch im Dienstpfad.
**Fix (S):** In `serve()` dieselbe Loopback-Prüfung wie in `config._loopback_host`
(IP parsen, `is_loopback` verlangen); Nicht-Loopback nur über einen expliziten,
laut benannten Parameter (`allow_non_loopback=True`), den `serve`-CLI nie setzt.
**Abnahme:** Refusal-Test `serve(entry, host="0.0.0.0")` → `ContractError` (+ Guard-Eintrag).

### S-08 · P1 · Control Deck: mächtig, unauthentisiert, im selben Repo

**Befund:** `tools/control_deck/server.py` hat bewusst **kein Login**; Schutz = Loopback/
Tailscale-Bindung + Host-Allowlist (DNS-Rebinding bedacht — gut) + Origin-Check. Es kann
allowlistete Kommandos ausführen (`git status`, Tests, Demo, Docker-Info), Mail-Metadaten
zeigen und kennt einen Agent-Startpfad (nicht via HTTP). Schwächen: (a) Annahme
„100.64.0.0/10 = Tailnet" greift auf CGNAT-Hosts zu kurz (selbst dokumentiert); (b) CSP
mit `unsafe-inline` für Script/Style; (c) Umgebung wird an Subprozesse vererbt
(`**os.environ`) — Umgebungs-Secrets des Starters sind für Aktions-Subprozesse sichtbar;
(d) psychologisches Risiko: Ops-Oberfläche im Security-Repo lädt zum Dauerbetrieb ein.
**Fix (M):** Token-Pflicht auch auf Loopback (generiertes Start-Token in `0600`-Datei,
als Header/Cookie), Umgebung der Subprozesse auf Allowlist kürzen, CSP ohne
`unsafe-inline` (externe JS/CSS-Datei oder Nonce), `--host` auf Tailscale nur mit
explizitem `--i-understand-tailnet`-Schalter. Deck **nicht** auf dem Produktionshost des
Kerns betreiben — in `docs/CONTROL-DECK.md` als Betriebsregel festschreiben.
**Abnahme:** Tests: Request ohne Token → 401 (auch von 127.0.0.1); Canary-Env-Variable
erscheint nicht in Aktionsausgabe; CSP-Header ohne `unsafe-inline`.

### S-09 · P2 · TLS ist Deployment-Aufgabe — gut dokumentiert, bleibt Fußangel

**Befund:** Kern ohne TLS/Sessions, nginx-Vorlage ist sorgfältig (kein Port 80, keine
Auth-Header im Log, Limits abgeglichen). Restrisiko: Proxy-Fehlkonfiguration ist nicht
vom Kern feststellbar; kein mTLS, keine Weitergabe einer verifizierten Client-Identität.
**Fix (M):** Vorlage um optionales mTLS (`ssl_verify_client`) für Service-Clients
erweitern; Start-Selbsttest-Doku (`curl`-Negativtests: Klartext-80 zu, Header-Logging aus,
Chunked/16 KiB-Verhalten) als Checkliste in `docs/REVERSE-PROXY.md`; HSTS bleibt.
**Abnahme:** E3-Protokoll enthält die Proxy-Negativtests mit Ausgabe.

### S-10 · P2 · CI-/Supply-Chain-Hygiene

**Befund:** Actions per SHA gepinnt (vorbildlich), aber Service-Image `postgres:17` nur
per Tag; Test-DB mit `POSTGRES_HOST_AUTH_METHOD: trust` und veröffentlichtem Port
(disponibel und begründet, PR #154 adressiert Loopback); kein Dependabot/Renovate, kein
`pip-audit`, kein CodeQL/SAST — CVE in `cryptography`/`psycopg` fällt nur durch Zufall auf.
Bei hash-gepinnten Deps ist „kein Update-Mechanismus" zugleich „kein Patch-Mechanismus".
**Fix (S, Kaans OK als Tooling-Entscheidung):** (1) `postgres:17@sha256:…` per Digest
pinnen. (2) Dependabot **nur** für `github-actions`. (3) Wöchentlicher `pip-audit`-Job
(liest nur, ohne Runtime-Dep) + CodeQL für Python. (4) Hash-Update-Ablauf dokumentieren
(`pip-compile --generate-hashes` o. ä.) — heute ist unklar, wie `requirements.txt` entsteht.
**Abnahme:** CI grün mit Digest-Image; ein Probe-PR von Dependabot wird korrekt vorgeschlagen.

### P-01/P-02 · P0 · Audit-Kette skaliert nicht und hat eine harte Lebensdauer-Decke

**Befund:** `_append` ruft `_read`: **jeder** Append liest und verifiziert die *gesamte*
Kette und schickt sie an den Anker (O(n) pro Event, O(n²) über die Lebensdauer).
`anchor_process` deckelt Anfragen (64 MiB) und Zustand (16 MiB); eine Kopfzeile ist
≈ 250 Byte ⇒ grob **65 000 Köpfe** — bei mehreren Events pro Job also in der Größenordnung
**10⁴ Jobs Gesamtlebensdauer**, danach verweigert der Dienst jedes weitere Event
(fail closed, aber: Betriebsende). Das ist heute der härteste Blocker für Dauerbetrieb
(auch in STATUS als technisches Risiko benannt).
**Fix (L, Reihenfolge wie in STATUS Punkt 7 — der Plan ist richtig, er muss nur *vor*
Portal/Deck passieren):** (1) Messtest, der Append-Latenz über 10³/10⁴/10⁵ Events
protokolliert (S). (2) Inkrementelles Protokoll: Append sendet nur neues Record + alten
Kopf; Verifikation über gespeicherten, signierten Prefix-Kopf statt Vollscan; Anker
speichert nur letzten Kopf + Zähler (kompaktierte Zustandsdatei mit Genesis-Checkpoint).
(3) Checkpoint-/Epochendesign in `DATABASE.md` entscheiden (Kopf-Kette pro Epoche,
Epochenköpfe verkettet) — hebt die Decke, ohne Beweiswert zu verlieren.
**Abnahme:** Benchmark-Test: Append Nr. 100 000 dauert < 2× Append Nr. 100 (statt ~1000×);
Persistenz-/Restore-Demos und alle B5/B6-Tests bleiben grün; `SECURITY.md`-Grenze angepasst.

### P-03 · P1 · Eine DB-Verbindung für alles

**Befund:** Job-, Annahme- und Audit-Ledger teilen eine Verbindung (für B6-Atomarität
gewollt). Durchsatz ist strikt seriell; ein Verbindungsabbruch erfordert Dienstneustart.
Für eine geschlossene Beta okay, für alles darüber der nächste Engpass nach P-01.
**Fix (L):** Atomarität beibehalten, aber: dedizierte Audit-Verbindung mit eigener
Transaktionsgrenze (Kopf-Commit-Reihenfolge bereits definiert), Reconnect mit
Wiederverifikation statt Neustart-Pflicht, danach erst über Pool nachdenken. Vorher mit
P-01-Benchmark messen, was wirklich limitiert.
**Abnahme:** Crash-Test B7 um „DB-Verbindung wird gekappt, Dienst erholt sich ohne
Doppelannahme" erweitern.

### P-04 · P1 · Synchrones Job-Modell ohne Status-API

**Befund:** `POST /jobs` antwortet erst nach Worker-Ende (bis 30 s Wandzeit); `max_in_flight=8`,
danach `503`. Kein `GET /jobs/<id>`, keine Queue, keine Idempotenz-Schlüssel außer der
einmaligen Job-ID. Für echte (LLM-)Tools mit Minuten-Laufzeit unbrauchbar; Proxy-Timeouts
(45 s) sind darauf abgestimmt und würden reißen.
**Fix (L, Teil von D1):** Asynchrones Modell als eigenen Vertrag: `POST` → `202` + Job-ID,
`GET /jobs/<id>` nur fürs eigene Subject (fremd → wie heute nicht unterscheidbar `404/409`),
begrenzte Warteschlange mit denselben Token-Buckets, Ergebnis-Abruf exakt einmal oder TTL.
Synchroner Pfad für schnelle Tools darf bleiben.
**Abnahme:** E2E: langsamer Worker (simuliert 60 s) ohne Proxy-Timeout; fremde Job-ID
liefert identische Antwort wie nichtexistente.

### O-01 · P1 · Keine Observability

**Befund:** Kein `logging`, keine Metriken, kein Health-Endpunkt; Ausgaben sind drei
`print` auf stderr; Ablehnungsgründe existieren als geschlossene Codes (`REASONS`,
Orchestrator-Reason-Codes) — sie werden aber betrieblich nirgends gezählt/exportiert.
Ein Angriff (429/503/REJECTED-Flut) oder schleichendes Vollaufen (Ledger/Audit-Decken)
fällt erst auf, wenn der Dienst verweigert.
**Fix (M):** Strukturiertes, **inhaltsfreies** Betriebslog (JSON auf stderr → journald):
nur geschlossene Codes + Zähler + Trace-ID, niemals Payload/Key (die Canary-Tests der
Demo als Muster). Interner Zähler-Endpunkt auf separatem Loopback-Port oder Unix-Socket
(`/metrics` im Prometheus-Textformat, handgeschrieben ohne neue Dep): Requests je Reason,
in-flight, Audit-Count vs. Decke, Anker-Erreichbarkeit, Up-/Downtime. Health getrennt:
`ready` = DB + Anker + Worker-Socket geprüft (Logik existiert teils in `worker_service`).
**Abnahme:** Canary-Test: Payload/Key-Digests erscheinen in keinem Log/Metric-Output;
Deck/Proxy kann Readiness abfragen, ohne Job-Rechte zu besitzen.

### O-02 · P1 · Betrieb ist beschrieben, aber nicht belegt (C5/E2/E3)

**Befund:** systemd-Units/Rotation (C5) nur Entwurf (#117), Security-Review am Head (E2)
offen, Frischklon-Beweis (E3) offen; A2-UID-Abnahme HOLD. Solange E3 fehlt, ist jede
„läuft"-Aussage eine Aussage über Kaans Maschine, nicht über das Artefakt.
**Fix (M, Prozess statt Code):** E3 als **nächstes** Gate vorziehen (vor D-Code, vor
KI-Pilot): Wegwerf-VM (Ansatz in PR #173), nur mit Repo + Anleitung, Protokoll mit SHA.
Dieses Review kann als E2-Vorarbeit dienen, ersetzt das Review am exakten Head nicht.
**Abnahme:** E3-Protokoll im Repo; `ROADMAP-V02`-Tabelle entsprechend angeglichen.

### M-01–M-04 · P2 · Wartbarkeit & DX

- **M-01 Monolithen (M):** `worker_service.py` (1 552 Z.) in Lebenszyklus/Lease/Profil/
  Transport schneiden; `wiring.py` (830 Z.) ist als Kompositionswurzel okay, aber an der
  Grenze. Schnittregel: keine Verhaltensänderung, Refusal-Guard muss je neuem Modul mit.
- **M-02 Tooling (S, Entscheidung Kaan, STATUS Punkt 3):** `pyproject.toml` (nur Konfig,
  kein Build-Umbau) + `ruff` (inkl. `S`-Regeln/bandit-Linter) + `mypy --strict` schrittweise
  für `geniusnew/` + Coverage-Bericht ohne Gate. Neue Dev-Deps in `requirements-dev.txt`,
  ebenfalls hash-gepinnt, damit Runtime-Minimalismus bleibt.
- **M-03 Python-Matrix (S):** CI um 3.13 ergänzen (Tests-Job als Matrix 3.11+3.13;
  Refusals nur auf einer Version, sonst explodiert die Laufzeit).
- **M-04 Test-Einstieg (M):** Ein `scripts/test-db.sh`, das einen lokalen Wegwerf-Cluster
  startet und `GENIUSNEW_TEST_ADMIN_DSN` setzt (Docker oder `initdb` in tmp) — kein
  Test wird übersprungen (Regel 4 bleibt), aber der erste grüne Lauf dauert Minuten
  statt eines Nachmittags. Dazu Architekturdiagramm (eine Seite, Mermaid/SVG) ins README.

### G-01–G-03 · P2/P3 · Governance & Außenwirkung

- **G-01 (S):** LICENSE wählen (Entscheidung Kaan; ohne Lizenz ist Public ≠ nutzbar),
  Repo-Description + Topics setzen, `.github/CODEOWNERS` (mind. `SECURITY.md`,
  `requirements.txt`, `geniusnew/migrations/` → Kaan), Branch-Protection mit Pflicht-Check
  `contracts` verifizieren (per API nicht prüfbar → in E2 mit abhaken).
- **G-02 (Prozess):** Feature-Freeze bis E3: Portal-Code (D), KI-Pilot (#166/#172) und
  Deck-Ausbau hinter P-01/E3 zurückstellen. 10 offene PRs + 8 offene Issues bei einem
  Entscheider sind der eigentliche Engpass — WIP-Limit (z. B. max. 3 offene Code-PRs).
- **G-03 (S):** Entscheidendes Wissen ins Repo zurückholen (NotebookLM nur als Spiegel),
  Runbook „Kaan ist 2 Wochen weg" (Rotation, Restore, Anker) als eine Seite.

---

## 5. Verbesserungsvorschläge für ein besseres Endergebnis (Produkt-Sicht)

1. **Ein echter Anwendungsfall hinter dem Gate.** Heute ist `summarize` das Beispiel-Tool;
   der Kern ist eine Autorisierungs-Maschinerie ohne Nutzlast. Für die Beta **ein**
   reales Tool verbindlich machen (z. B. lokales LLM über definierte Worker-Schnittstelle
   mit Größen-/Zeitbudget, Prompt nie im Audit-Log). Erst daran zeigen sich P-04 (async),
   Timeouts und Ergebnisgrößen ehrlich.
2. **„Fertig" für v0.2 einfrieren:** E3 + P-01-Messung + S-01-Rotationsablauf + O-01-Minimum
   = Beta. Alles andere (Portal-UI, KI-Pilot) ist v0.3. Ein kleineres, belegtes Beta
   schlägt ein großes, behauptetes.
3. **Threat-Model als einseitige Tabelle** (Angreifer: fremder API-Client, böswilliger
   Worker, kompromittierter Kernprozess, Host-Nutzer, Backup-Restore) → welche Grenze
   hält was. Material steht in `SECURITY.md`, es fehlt nur die Angreifer-Sicht (STRIDE-Light).
4. **Client-Erfahrung:** OpenAPI-Beschreibung der 3 Routen + `curl`-Beispiele + Fehlercodes
   ins README; ein 20-Zeilen-Python-Client ohne Deps als Beispiel unter `docs/examples/`.
5. **Last- und Soak-Test als Skript** (`scripts/load.py`, nur gegen lokale Instanz):
   1 000 Jobs, Fehlerrate, Latenz-Perzentile, Audit-Wachstum — Ergebnis fließt in P-01 ein.
6. **Release-Disziplin:** CHANGELOG ab v0.2, signierte Tags, SBOM (`pip freeze` + Hashes
   liegen schon vor) als Release-Artefakt, Upgrade-Hinweis „Migration x vor Binary-Wechsel".
7. **Doku-Diät:** STATUS/DECISIONS bleiben; DRAFT-Dokumente mit Ablaufdatum versehen und
   abgelaufene nach `docs/history/` verschieben. Ziel: Ein neuer Leser braucht README +
   STATUS + SECURITY — nicht 30 Dateien.

## 6. Empfohlene Reihenfolge (nächste 6 Schritte)

| # | Schritt | Warum zuerst | Aufwand |
| --- | --- | --- | --- |
| 1 | **E3** Frischklon auf Wegwerf-VM bis zum laufenden Dienst | Belegt alles Bisherige; entlarvt Doku-Lücken | M |
| 2 | **P-01 Messung** (Append-Benchmark) → dann Inkrementell-Design | Harte Lebensdauer-Decke; blockiert jeden Dauerbetrieb | S → L |
| 3 | **S-07** Loopback in `serve()` + **S-10** CI-Pins/Dependabot-Actions | Klein, sofort, schließt Fußangeln | S |
| 4 | **O-01 Minimum:** inhaltsfreie Zähler + Readiness | Ohne Sicht kein Betrieb; Voraussetzung für Beta | M |
| 5 | **C5** inkl. Schlüsselrotation (S-01 Rotations-Teil) + eigene OS-Nutzer (A2) | Betriebsvertrag, Voraussetzung für echten Server | L |
| 6 | **E2** Review am dann exakten Head (Claude + Copilot) + G-01 Lizenz/CODEOWNERS | Beta-Siegel; dieses Papier als Checkliste | M |

Danach erst: D1-Code (mit P-04 Async-Design), S-04 Stufe 1, S-02 Verifier-Auslagerung.

---

*Prüfmethoden: vollständige Dateiliste/LOC, Lektüre von Kernmodulen
(`contracts`, `keys`, `http_entry`, `isolation`, `audit_store` u. a.), grep-Audits
(Secrets, `eval`/`pickle`/`shell=True`, SQL-Interpolation, `compare_digest`),
CI-/Config-Lektüre, GitHub-API (PRs/Issues/Lizenz), Abgleich mit `SECURITY.md`,
`docs/STATUS.md`, `docs/ROADMAP-V02.md`. Zahlen: 22 Kernmodule (~9 900 Zeilen),
43 Testdateien / 1 244 Tests (~19 200 Zeilen), 2 direkte Laufzeit-Abhängigkeiten.*
