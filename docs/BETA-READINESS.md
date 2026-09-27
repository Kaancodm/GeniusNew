# Technische Beta-Reife — Definition und Phasenplan

**Status: Vorschlag von Claude Code vom 27.09.2026, Kaan entscheidet.** Grundlage ist
`main` `81785c7`. Offen waren zu diesem Zeitpunkt #56 (DB-Design), #57 (Seccomp) und #58
(Beta-Betriebsmodus). Ist `main` weiter, gilt `main`.

„Technisch beta-ready“ heißt hier: Ein **geschlossener Kreis bekannter Nutzer** kann den
Kern auf dem eigenen Server über HTTPS benutzen. Ein Neustart vergisst nichts und nimmt
nichts doppelt an, und jede Grenze, die dann noch offen ist, steht mit Test in
`SECURITY.md`. Öffentlicher Deploy und Release bleiben bei Kaan (`AGENTS.md`).

## 1. Beta-Kriterien

Jedes Kriterium ist erst erfüllt, wenn ein Test oder CI-Lauf am exakten Head-SHA es
belegt. Eine Beschreibung im PR reicht nicht.

| # | Kriterium | Beleg | Schließt Grenze aus `SECURITY.md` |
| --- | --- | --- | --- |
| B1 | **Persistenz:** Job- und Annahme-Ledger, wartende Jobs, Approval-Speicher und Audit-Kette liegen in PostgreSQL (`docs/DATABASE.md`). Neustart und zweite Instanz führen keinen Job doppelt aus und nehmen kein Ergebnis doppelt an | Replay-Tests A–C aus Issue #44 gegen PostgreSQL in der CI. Die offen gehaltenen `…_process_local_…`-Tests sind umgekehrt | Job-Ledger, Annahme-Ledger, 100 000-Grenze, wartende Jobs |
| B2 | **Ledger manipulationssichtbar:** Jede Ledger- und Approval-Zeile wird beim Start gegen die verankerte Audit-Kette abgeglichen, Zustand und Event in einer Transaktion (Review zu #56) | Tests zu gelöschter Zeile und gefälschtem Approval-Record: Start verweigert | neu, als verbleibende Grenze dokumentiert |
| B3 | **Worker liest keine Dienst-Secrets:** Root-Secret, DB-Zugangsdaten und Anker-Zustand sind für den Worker-Prozess nicht lesbar | Test: Worker versucht, die Secret-Datei zu lesen, und wird abgelehnt | „Worker darf lesen, was der Dienstnutzer lesen darf“ (wenigstens für Secrets) |
| B4 | **Worker startet keine Prozesse:** Seccomp unter Linux x86_64, andere Plattformen fail closed | #57 | Audit-Hook-Lücke |
| B5 | **Anker außerhalb des Dienstes:** eigener Lebenszyklus, jede Antwort über die Request-Nonce signiert, im Betrieb unter eigenem OS-Nutzer | Neustart-Test des Dienstes, Test mit einem untergeschobenen Anker, Beispiel-Unit mit eigenem Nutzer | Anker unter demselben Nutzer |
| B6 | **Produktiver Startpunkt:** `python -m geniusnew serve` lädt Policy, API-Key-Digests und das Root-Secret aus einer Datei mit Rechten `0600`. Alles Fehlende oder Unklare: kein Start | Tests für jede Ablehnung beim Start, Refusal-Guard | neu |
| B7 | **Betrieb hinter TLS:** Reverse-Proxy-Beispiel mit TLS und Rate-Limit, Health-Endpunkt ohne Interna, Logs ohne Payload und ohne Secrets | Doku plus Test für Health und Log-Inhalt | HTTP ohne TLS und Rate-Limit (als Deployment-Beleg) |
| B8 | **Freigebende über HTTP:** eigene Rolle und Route für Approvals, getrennt vom Antragsteller | Test: Antragsteller kann sich nicht selbst freigeben | Approvals nur serverseitig |
| B9 | **Beta-Evidenz:** Quickstart am exakten SHA, Demo gegen PostgreSQL, `kill -9` während der Ausführung mit anschließendem sauberem Neustart | ein Evidenz-Dokument wie `docs/QUICKSTART-REVIEW.md` | — |

## 2. Bewusst nicht für die Beta nötig

Diese Punkte bleiben als offene Grenzen in `SECURITY.md` stehen:

- **Gateway in eigenem Prozess:** Die Trennung bleibt logisch.
- **Ein Schlüssel pro Worker:** Solange genau ein Worker-Typ läuft.
- **macOS und Windows als Laufzeit:** Die Worker-Isolation verweigert dort den Start (fail
  closed). Unterstützt wird nur Linux x86_64.
- **microVM statt Prozessgrenze.**
- **Das Portal:** Die Beta ist die Kern-API. Das Portal auf Vercel ist ein eigener
  Meilenstein und braucht zuerst den Auth-Vertrag Portal→Kern aus #56.

## 3. Phasen und Zuständigkeit

Pro Branch gibt es genau einen Implementierer (`AGENTS.md`, Beta-Betriebsmodus). Die
Reihenfolge folgt den Abhängigkeiten.

| Phase | Inhalt | Wer | Hängt ab von |
| --- | --- | --- | --- |
| P1 | #57 Seccomp mergen, #43 schließen | Codex | — |
| P2 | #56 um Review-Punkte 1–3 ergänzen und mergen | Codex | — |
| P3 | `0001_core_foundation`: PostgreSQL in der CI, `psycopg` hash-gepinnt, persistentes Job-Ledger | Codex | P2, neue Abhängigkeit (von Kaan freigegeben) |
| P4 | Annahme-Ledger, wartende Jobs, Approval-Speicher | Codex | P3 |
| P5 | Audit-Kette in der DB, Abgleich gegen den Anker (B2) | Claude (Security) mit Codex | P4 |
| P6 | Anker als eigener Dienst mit signierten Antworten (B5), Neuaufbau aus #32 | Claude | — |
| P7 | Produktiver Startpunkt und Secret-Laden (B6) | Claude | — |
| P8 | Worker ohne Lesezugriff auf Secrets (B3) | Claude (Design), Codex oder Claude | P7 |
| P9 | Freigebende-Rolle und Route (B8) | Codex | P4 |
| P10 | Betrieb: Proxy-Beispiel, Health, Logs (B7) | Codex | P7 |
| P11 | Beta-Evidenz (B9), danach Kaans Entscheidung über den Deploy | Claude prüft, Kaan entscheidet | alle |

## 4. Entscheidungen für Kaan

1. Gilt diese Definition, besonders Abschnitt 2 (was nicht zur Beta gehört)?
2. B3: Wird der Worker unter einem eigenen OS-Nutzer betrieben (einfach, braucht
   Root-Rechte beim Start), oder wird er mit Landlock eingeschränkt (Linux ≥ 5.13, keine
   neue Abhängigkeit)?
3. Gehört das Portal zur Beta oder, wie hier vorgeschlagen, erst danach?
