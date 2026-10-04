# GeniusNew — Projektstand

**Stand: 04.10.2026, `main` an `dd4a35e`.** Teil der Wissensdatenbank, seit 04.10.2026
geführt von Claude Code (`docs/COLLABORATION.md`); Entscheidungen stehen in
`docs/DECISIONS.md`. Dieses Dokument ist in sich geschlossen gedacht: als Quelle für
NotebookLM, Microsoft 365 Copilot oder jeden anderen Assistenten, der das Repository
nicht selbst lesen kann. Verbindlich bleiben der Code, `SECURITY.md` und
`docs/ROADMAP-V02.md`. Bei Widerspruch gilt das Repository.

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
4. **Worker:** läuft in einem eigenen Prozess mit Ressourcenlimits. Kernelseitig
   gesperrt sind der Start neuer Programme (Seccomp), Dateizugriffe außerhalb einer
   Allowlist und TCP-Verbindungen (Landlock).
5. **Ergebnisprüfung:** nimmt das signierte Ergebnis an, und zwar nur einmal pro
   Handoff und nur innerhalb der Gültigkeit. Sie hält nur öffentliche Schlüssel und
   könnte kein Ergebnis fälschen.
6. **Audit:** Jede Entscheidung wird ohne Nutzdaten in eine Hash-Kette geschrieben,
   im Dienst dauerhaft in PostgreSQL. Den Kopf signiert die Audit-Rolle; ein Anker in
   eigenem Prozess oder als eigener Dienst hält ihn fest, sodass eine gekürzte und neu
   signierte Kette auffällt. Beim Start gleicht der Dienst Ledger, Approvals und Kette
   in beide Richtungen ab und verweigert jeden Widerspruch.

## Stand

**v0.1 ist erreicht** (Tag `v0.1` auf `3a0e1bc`). Das Ziel jetzt ist **v0.2,
technisch beta-ready** (`docs/ROADMAP-V02.md`).

**Gates bis v0.2:**

| Bereich | Erledigt | Offen |
| --- | --- | --- |
| A — Worker-Sandbox | A1 Seccomp (#57), A2 Landlock-Allowlist (#76) | eigener OS-Nutzer für den Worker (mit C5) |
| B — Persistenz | B0 Entwurf (#70), B1 PostgreSQL-Fundament (#78), B2 Job-Ledger (#90), B3 Annahmen (#93), B4 wartende Jobs (#94), B5 Audit-Kette (#95), B6 Abgleich Ledger ↔ Kette (#97, #98), B7 Absturztest (#106) | Claude DB Review für B4 und B5 nachholen (offene Entscheidung 6 in der Roadmap) |
| C — Betrieb | C1 `serve` (#61), C2 Anker-Dienst (#62), C3 Rolle für Freigebende (#111), C4 HTTP-Grenzen (#71, #89) | **C5** Betriebsanleitung, systemd, Backup/Restore |
| D — Portal | — | D1 Vertrag Portal↔Kern, D2 Identität, D3 Ansichten |
| E — Nachweis | — | E1 Persistenz-Angriffe in der Demo (#112, CI läuft), E2 Review des vollständigen Heads, E3 frischer Host nach Anleitung |

**Reihenfolge seit 04.10.2026 (Kaan):** erst der echte Server, dann **C5 und E3**, danach
das Portal mit **D1** zuerst (Python, Vorlage ist der Prototyp von Codex).

**Betrieb und Werkzeuge:** `ops/server/genius-server` richtet einen Debian-/Ubuntu-Server
mit Tailscale und Termius ein, sperrt SSH auf Tailscale und betreibt das Control Deck
als Dienst nur über Tailscale (#110, #114, `docs/setup/SERVER-TOOL.md`). Das Control Deck
(#73, #74, #75, #91) zeigt Status nur lesend. Der Server läuft (Debian 13, Kernel 6.12,
x86_64) und ist über Tailscale erreichbar; das Deck dort ist noch von Hand gestartet,
nicht als Dienst.

**Messbar (`main` an `dd4a35e`, lokal gemessen):**
- 1078 Tests laufen in etwa 107 Sekunden (mit Test-PostgreSQL).
- Die Demo endet mit „PASS“, dabei werden **15 von 15 Angriffen** abgelehnt.
- Der Refusal-Guard deckt 21 Python-Module und das Server-Werkzeug ab: Jede Ablehnung
  wird einzeln abgeschaltet, und die Suite muss jedes Mal rot werden.

## Bekannte Grenzen

Vollständig in `SECURITY.md`. Die wichtigsten:

- Außer Worker und Anker laufen alle Instanzen in **einem Prozess**. Die Trennung ist
  logisch, nicht im Speicher.
- Der Dienst speichert Ledger, wartende Jobs, Approvals und Audit-Kette in
  **PostgreSQL**; Demo und Aufrufer ohne Datenbank bleiben prozesslokal. Eine
  abgerissene DB-Verbindung verweigert weitere Jobs bis zum Neustart.
- Gespeicherte Annahmen hängen an der **aktuellen Policy und den aktuellen
  Schlüsseln**: Eine unvereinbare Policy-Änderung oder Schlüsselrotation verweigert
  den Start, bis C5 einen Ablauf dafür beschreibt.
- Alle Schlüssel hängen an **einem Root-Secret**, und es gibt **einen**
  Worker-Schlüssel für alle Worker.
- Der Anker kann als eigener Dienst laufen; unter **eigenem Nutzer** betrieben ist er
  erst durch die Installation (C5). Sein Nutzer, `root` oder ein zurückgespieltes
  Backup können seine Zustandsdatei auf einen älteren, gültig signierten Kopf
  zurückschneiden.
- Die Worker-Isolation ist eine Prozessgrenze mit Seccomp und Landlock, keine microVM.
  Worker laufen nur auf Linux x86_64 ab Kernel 6.7 (fail closed); UDP und Unix-Sockets
  sperrt nur der Python-Audit-Hook.
- Der HTTP-Eingang hat kein TLS und keine Sessions; das ist Aufgabe des
  Reverse-Proxys (`docs/REVERSE-PROXY.md`). `serve` lauscht nur auf Loopback.
- API-Key-Digests sind ungesalzen: richtig für zufällige Maschinenschlüssel.

## Nächste Schritte

1. **E1 (#112)** mergen, sobald die CI grün ist.
2. **Server:** das von Hand gestartete Control Deck stoppen und mit
   `bash ops/server/genius-server deck --apply` als Dienst einrichten.
3. **Portal-Prototyp sichern:** Der Branch `codex/portal-prototype-0927` liegt nur im
   Server-Checkout; vor jedem Branch-Wechsel dort `git status` prüfen und ihn nach
   GitHub pushen.
4. **C5 (Betrieb):** systemd-Units für Kern und Anker unter getrennten Nutzern,
   Backup und Restore von Datenbank und Ankerzustand, Ablauf für Schlüsselrotation und
   Policy-Änderung; einmal einen Restore durchspielen.
5. PostgreSQL auf dem Server nach der C5-Anleitung einrichten (ChatGPT).
6. **E3:** frischer Klon auf dem Server folgt der Anleitung wörtlich bis zum laufenden
   Dienst; Protokoll mit SHA.
7. **D1 (Codex):** Vertrag Portal↔Kern mit signierten Requests (Ed25519, Ablauf,
   Replay-Schutz); Plan zuerst mit Gemini-Design-Vorprüfung. Offene Entscheidung 2
   der Roadmap (signierte Requests oder mTLS) braucht Kaans OK.
8. Claude DB Review für B4 und B5 nachholen.
9. D2 und D3 (Portal-Identität und Ansichten), danach E2 (Review des vollständigen
   Heads) und der Tag `v0.2`.

## Zusammenarbeit der Werkzeuge

Verbindlich ist `docs/COLLABORATION.md`. Kurz: **Kaan** entscheidet. **Codex** setzt
Kerncode um und mergt eigene PRs bei grüner CI. **ChatGPT** übernimmt neue Werkzeuge,
Server-Pflege, Infrastruktur und `docs/DATABASE.md` in Kaans Auftrag. **Claude Code**
besitzt `docs/COLLABORATION.md`, `docs/DECISIONS.md` und dieses Dokument, reviewt
DB-Entwurf und DB-Code sicherheitstechnisch und löst Konflikte. Jeder PR basiert auf
`main` (keine Stapel-PRs).

| Werkzeug | Rolle | Liest |
| --- | --- | --- |
| Gemini Pro | Review über die Antigravity- oder Abacus-CLI: Pflicht-Zweitmeinung bei Ausnahmen (Pro-Modell), Design-Vorprüfung, sonst auf Anforderung | `GEMINI.md`, `.gemini/styleguide.md` |
| Kiro-CLI | Unabhängige Zweitprüfung für Claudes eigenen Code, nur lesend | `docs/COLLABORATION.md` |
| NotebookLM | **Wissensdatenbank**: Auskunft für alle, mit Quelle | dieses Dokument, `docs/DECISIONS.md`, `SECURITY.md`, `docs/ROADMAP-V02.md`, `docs/COLLABORATION.md`, `AGENTS.md` |
| ChatGPT Pro / Codex | **Kerncode-Umsetzung**, ein Thema pro PR (`codex/<thema>`), mit Wissensblock | `AGENTS.md`, `docs/COLLABORATION.md` |
| ChatGPT (Chat) | **Neue Werkzeuge, Server-Pflege, Infrastruktur, Integrationen** (`chatgpt/<thema>`); Kaan mergt | `AGENTS.md`, `docs/COLLABORATION.md` |
| GitHub Copilot Pro | Editor und Review jedes PRs | `.github/copilot-instructions.md` |
| Microsoft 365 Copilot | Berichte, E-Mails, Folien | OneDrive-Ordner `GeniusNew` |
| Claude Code | Wissen und Regeln, Konfliktlöser mit Überschreibrecht, Sicherheits-Review von DB-Entwurf und DB-Code | `AGENTS.md`, `docs/COLLABORATION.md` |

Übergaben zwischen den Werkzeugen laufen über die zwei Prompts in `docs/HANDOVER.md`.
Claude-PRs mit Code brauchen vor Kaans Merge ein unabhängiges Review (Codex, Kiro oder
Gemini); neue Abhängigkeiten, geänderte `SECURITY.md`-Grenzen und Tags brauchen Kaans OK.
