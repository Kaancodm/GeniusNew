# Roadmap zu v0.2 — technisch beta-ready

Dieses Dokument ist ein **Plan, kein Zustandsbericht.** Was tatsächlich gilt, steht in
`main`, in den Tests und in der CI. Wenn dieses Dokument und das Repository sich
widersprechen, hat das Repository recht. Maßgeblich bleiben `SECURITY.md`,
`docs/CONSTITUTION-V1-DRAFT.md`, `docs/DECISIONS.md` und, sobald gemergt,
`docs/DATABASE.md`.

Angelegt auf `main` `81785c7cd2ee090aab353eea39545f22efacb1ad` im temporären
Beta-Betriebsmodus (`AGENTS.md`, Kaan, 27.09.2026). Offene PRs zu diesem Zeitpunkt:
#56 (DB-Design), #57 (Seccomp), #58 (Beta-Regeln), dazu die Altlasten #32 und #43. Die
parallel entstandene Fassung #60 (`docs/BETA-READINESS.md`) ist hier zusammengeführt:
Abgleich der Ledger gegen die Kette (B6), Mindestziel für A2, Restore-Weg (C5) und die
Frage, ob das Portal zur Beta gehört.

## Ziel von v0.2

> Der Kern läuft auf einem eigenen Linux-Server als Dienst, nicht als Demo. Er startet
> nur mit gültiger Konfiguration und intakter Datenbank. Ein Neustart, ein Absturz an
> jeder Zustandsgrenze oder eine zweite Instanz führt keinen Job doppelt aus, nimmt kein
> Ergebnis doppelt an und verliert keinen wartenden Job. Die Audit-Kette ist dauerhaft
> und gegen einen Anker unter eigenem Betriebssystem-Nutzer prüfbar. Ein Worker kann
> weder Prozesse starten noch Dateien des Hosts herausgeben. Freigebende und Nutzer
> arbeiten über das Portal, das mit dem Kern nur über eine authentisierte
> Server-zu-Server-Schnittstelle spricht. Die CI beweist das bei jedem Push, auch gegen
> ein echtes PostgreSQL.

**Technisch beta-ready** heißt: alle Gates unten sind am selben Head-SHA belegt.
**Beta-ready ist keine Freigabe.** Öffentlicher Deploy, produktiver Release, der Tag
`v0.2` und Secret-Rotation bleiben Kaans ausdrückliche Entscheidung.

## Bewusst nicht in v0.2

- **microVM / Firecracker.** Die Worker-Grenze bleibt ein Prozess, verstärkt durch
  Seccomp, eigenen Nutzer und Dateisystem-Einschränkung.
- **Mehrere Worker-Typen mit eigenen Schlüsseln.** Solange es genau einen Worker-Typ
  gibt, bleibt „ein Worker-Schlüssel“ eine in `SECURITY.md` gelistete Grenze.
- **LLM-Worker.** Nach Konflikt 4 (Issue #44) kommt vorher der negative Test
  „Prompt verlangt unerlaubten Tool-Call → kein Aufruf“.
- **Gateway und Orchestrator in getrennten Prozessen.** Die Ein-Prozess-Grenze bleibt
  gelistet; die DB-Rechte werden aber so geschnitten, dass ein späterer Split möglich ist.
- **macOS/Windows als Serverplattform.** Der Kern ist Linux-only; andere Plattformen
  verweigern fail closed.

## Gates

Jedes Gate ist ein eigener PR (oder eine kleine Folge), mit rotem Regressionstest
zuerst, Tests, Demo und Refusal-Guard nacheinander grün und Evidenz am exakten Head.
„Grenze schließen“ heißt immer: offen gehaltenen Test umkehren und `SECURITY.md`
anpassen.

### A — Worker-Sandbox

| # | Gate | Nachweis | Stand |
| --- | --- | --- | --- |
| A1 | Prozessstart im Worker kernel-seitig gesperrt (Seccomp), andere Plattformen fail closed | `test_a_spawn_below_the_audit_hook_…` umgekehrt | erledigt: #57 (Codex), gemergt `8a358d1` |
| A2 | Worker liest keine Host-Dateien: eigener unprivilegierter OS-Nutzer und Landlock-Allowlist (nur Job-Verzeichnis, Python-Laufzeit lesend); ohne Landlock-Unterstützung fail closed. **Mindestziel**, falls Kaan die volle Lösung verschiebt: Root-Secret, DB-Zugangsdaten und Ankerzustand sind für den Worker nicht lesbar | `test_a_read_outside_the_temporary_directory_…` umgekehrt; Mindestziel: Test „Worker liest die Secret-Datei“ wird abgelehnt | Landlock-Allowlist erledigt: #76 gemergt (`3297f93`), Mindestziel erreicht; offen: eigener OS-Nutzer für den Worker (Betriebsmodell, zusammen mit C2/C5) |

### B — Persistenz (nach `docs/DATABASE.md`)

| # | Gate | Nachweis | Stand |
| --- | --- | --- | --- |
| B0 | `docs/DATABASE.md` gemergt, inklusive der Review-Befunde aus #56 (Vereinigungsmenge dort) | Doku; Claude-Security-Review, Freigabe durch Kaan | erledigt: #70 gemergt (Claude DB Review APPROVED an `dc0e825`, Freigabe Kaan 29.09.2026) |
| B1 | `0001_core_foundation`: PostgreSQL-Dienst in der CI, `psycopg` hash-gepinnt, Migrationen mit Checksum, Start verweigert bei fehlender DB oder falscher Migration | Start-Refusal-Tests gegen echtes PostgreSQL | #78 (Codex): Claude DB Review APPROVED an `99ef6e3`, CI grün; Auflagen für B2 im Review |
| B2 | Job-Ledger persistent: Tests A–C aus Konflikt 4, Runtime ohne DELETE, nur Vorwärtsübergänge | Ledger-Grenztest in `test_orchestrator.py` umgekehrt | offen |
| B3 | Annahme-Ledger persistent, Annahme nur aus `EXECUTION_COMMITTED` | Ledger-Grenztest in `test_verifier.py` umgekehrt | offen |
| B4 | Wartende Jobs und Approval-Speicher persistent, append-only, verzweigungsfrei | Neustart verliert keinen wartenden Job; verbrauchter Token bleibt verbraucht | offen |
| B5 | Audit-Kette persistent; signierter Kopf in derselben Transaktion; Neustart verankert nur bereits signierte Köpfe nach | Per SQL angehängtes Event → Start verweigert | offen |
| B6 | Ledger und Approval-Speicher **manipulationssichtbar**: jede sicherheitsrelevante Zeile und ihr Audit-Event in derselben Transaktion; die Startprüfung gleicht beide Richtungen gegen die verankerte Kette ab (`acceptance_ledger` ↔ `RESULT_ACCEPTED`, `job_ledger` ↔ `HANDOFF_ADMITTED`, Approval-Records ↔ `approval_record_hash`) | Per SQL gelöschte Ledger-Zeile oder eingefügter `GRANTED`-Record → Start verweigert | offen |
| B7 | Absturztest: Dienst wird an jeder Zustandsgrenze hart beendet (`SIGKILL`) und neu gestartet | kein Doppellauf, keine Doppelannahme, Kette verifiziert | offen |

### C — Betrieb des Kerns

| # | Gate | Nachweis | Stand |
| --- | --- | --- | --- |
| C1 | **Server-Einstieg** statt nur Demo: `python -m geniusnew serve` liest Root-Secret, Policy, DB- und Anker-Verbindung ausschließlich aus serverseitiger Konfiguration; das Demo-Secret wird außerhalb der Demo abgelehnt | Refusal-Tests für jede fehlende/ungültige Einstellung | #61 gemergt; DB-Verbindung folgt mit B1 |
| C2 | Anker als eigener Dienst unter eigenem OS-Nutzer, Lebenszyklus außerhalb des Kerns, signierte Anker-Antworten mit Nonce (Teile aus #32, neu auf `main` gebaut) | Anker-Rückschnitt-Grenztest umgekehrt oder neu begründet | #62 (Claude) |
| C3 | Rolle „Freigebende“ mit eigener HTTP-Route; keine Selbstfreigabe | Refusal-Tests für fremde Rolle, eigene Aufträge, Doppelentscheidung | offen |
| C4 | HTTP-Härtung: Body-Limit, Timeouts, Rate-Limit pro API-Key; TLS über Reverse-Proxy mit Beispielkonfiguration in `docs/` | Tests für Limits; Doku | Body-Limit und Socket-Timeout auf `main`; Rate-Limit, Job- und Verbindungsgrenze sowie geprüfte nginx-Vorlage: #71 gemergt (`080f1f4`); offen: Limits in `[service]` der TOML einstellbar |
| C5 | Betriebsanleitung: systemd-Units (Kern, Anker, Nutzer getrennt), Backup und Restore von DB und Anker-Zustand, Ablauf der Schlüsselrotation. Ein Backup, das hinter dem Anker liegt, startet nicht (richtig so); der auditierte Weg zurück in den Betrieb ohne stilles Zurücksetzen des Ankers braucht Kaans Entscheidung | Doku + einmal durchgespielter Restore gegen die CI-Datenbank | offen |

### D — Portal (Vercel, nach `docs/MIGRATION-MATRIX.md` neu gebaut)

| # | Gate | Nachweis | Stand |
| --- | --- | --- | --- |
| D1 | Portal→Kern-Vertrag: Server-zu-Server-Authentisierung (signierte Requests mit Ablauf und Replay-Schutz); Browser-Felder `subject`, `tier`, `user_id`, `tools`, `job_id` werden abgelehnt | Refusal-Tests auf Kernseite | offen |
| D2 | Portal-Identität: Argon2id, Sessions nur als Digest, Rotation beim Login, CSRF, `HttpOnly`/`Secure`/`SameSite` | Tests für jede Eigenschaft | offen |
| D3 | Portal-Ansichten: Auftrag stellen, Verlauf, Freigaben, API-Schlüssel; fremde Aufträge → 404 | End-to-End-Test Portal→Kern | offen |

### E — Nachweis

| # | Gate | Nachweis | Stand |
| --- | --- | --- | --- |
| E1 | Demo erweitert um Persistenz-Angriffe (Replay nach Neustart, SQL-Manipulation, Anker-Vorlauf) | letzte Zeile `PASS` | offen |
| E2 | Unabhängiges Security-Review des vollständigen Heads (Claude und Copilot), `SECURITY.md` als „Bekannte Grenzen der Beta“ | Review-Kommentar am Head-SHA | offen |
| E3 | Frischer Klon auf einem sauberen Linux-Host folgt der Betriebsanleitung wörtlich bis zum laufenden Dienst | Protokoll mit SHA | offen |

## Reihenfolge

A1 → B0 → B1 → B2 → B3 → B4 → B5 → B6 → B7 → C1 → C2 → C3 → C4 → A2 → D1 → D2 → D3 →
C5 → E1 → E2 → E3.

Zuständigkeit (Vorschlag, je Branch ein Implementierer; Rollen nach #66): Codex B1–B4, C3, jeder DB-PR mit „Claude DB Review: APPROVED“ am exakten Head; Claude C1
(#61), C2 (#62, Neuaufbau aus #32), C4 (#71) sowie Design und Umsetzung von A2 (#72, #76; Entscheidung Kaan 29.09.2026); B5 und B6 Claude (Security) mit
Codex; E2 prüft Claude, E3 und jeder Deploy bleiben bei Kaan.

A2 und C2 hängen am selben Betriebsmodell (eigene OS-Nutzer) und können parallel zu B
laufen, wenn ein zweiter Implementierer frei ist. Ein Branch hat immer genau einen
Implementierer.

## Offene Entscheidungen für Kaan

1. ~~A2 vor der Beta schließen oder als Grenze in die Beta nehmen~~ — entschieden am
   29.09.2026: A2 vor der Beta, Umsetzung Claude (#76).
2. Portal→Kern-Authentisierung: signierte Requests (Vorschlag, ohne neue Abhängigkeit
   über Ed25519) oder mTLS.
3. Zielplattform des Servers: laut Server-Evidenz in #57 Debian 13, Kernel 6.12, x86_64
   (reicht für Landlock ABI 4). Offen: ob Landlock dort aktiv ist (Einzeiler in
   `docs/ISOLATION-A2.md`).
4. Gehört das Portal (D1–D3) zur Beta, oder ist die Beta zunächst die Kern-API und das
   Portal ein eigener Meilenstein danach (Vorschlag aus #60)?
