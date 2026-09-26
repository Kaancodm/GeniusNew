# GeniusNew — Projektstand

**Stand: 26.09.2026.** Dieses Dokument ist in sich geschlossen gedacht: als Quelle für
NotebookLM, Microsoft 365 Copilot oder jeden anderen Assistenten, der das Repository
nicht selbst lesen kann. Verbindlich bleiben der Code, `SECURITY.md` und
`docs/ROADMAP-V01.md`. Bei Widerspruch gilt das Repository.

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
4. **Worker:** läuft in einem eigenen Prozess mit Ressourcenlimits. Prozessstart
   sperrt der Kernel über einen Seccomp-Filter. Netz und Schreiben außerhalb eines
   temporären Verzeichnisses sperrt ein Python-Audit-Hook (`SECURITY.md`).
5. **Ergebnisprüfung:** nimmt das signierte Ergebnis an, und zwar nur einmal pro
   Handoff und nur innerhalb der Gültigkeit. Sie hält nur öffentliche Schlüssel und
   könnte kein Ergebnis fälschen.
6. **Audit:** Jede Entscheidung wird ohne Nutzdaten in eine Hash-Kette geschrieben.
   Deren Kopf signiert die Audit-Rolle; ein Anker in eigenem Prozess hält ihn fest,
   sodass eine gekürzte und neu signierte Kette auffällt.

## Stand

**v0.1 ist erreicht** (Commit `3a0e1bc`). Alle 20 Roadmap-Schritte sind erledigt, auch
der Quickstart-Review (Schritt 20, angenommen am 25.09.2026). Offen ist nur Schritt 21,
der Git-Tag `v0.1`. Die Arbeitssitzung kann keine Tags pushen, deshalb legt ihn der
Projektverantwortliche über die GitHub-Oberfläche an.

**Seit v0.1 gemergt:**

| PR | Inhalt |
| --- | --- |
| #31 | Audit-Köpfe mit Ed25519 signiert; der Anker hält nur den öffentlichen Schlüssel |
| #34 | Handoffs mit Ed25519 signiert (Handoff v2); das Gateway kann keinen Handoff mehr ausstellen |
| #35 | Ergebnisse mit Ed25519 signiert (Ergebnis v2); die Ergebnisprüfung kann kein Ergebnis mehr fälschen |
| #33 | Quickstart-Review dokumentiert (Codex) |
| #26 | Phase-0/1-Nachweis aktualisiert |

Damit gibt es **keine HMAC-Signatur mehr**: Jede prüfende Instanz hält nur
öffentliche Schlüssel.

**Offen:** #36, Anker-Persistenz. Der Anker schreibt jeden signierten Kopf in eine
Zustandsdatei und setzt nach einem Neustart dort fort. Die CI ist grün, der PR wartet
auf die Merge-Freigabe.

**Messbar (Hauptzweig plus #36):**
- 482 Tests laufen in etwa 14 Sekunden.
- Die Demo endet mit „PASS“, dabei werden **15 von 15 Angriffen** abgelehnt.
- Der Refusal-Guard deckt 15 Module ab: Jede Ablehnung im Code wird einzeln
  abgeschaltet, und die Suite muss jedes Mal rot werden.
- Die CI läuft etwa 4 Minuten.

## Bekannte Grenzen

Vollständig in `SECURITY.md`. Die wichtigsten:

- Außer Worker und Anker laufen alle Instanzen in **einem Prozess**. Die Trennung ist
  logisch, nicht im Speicher.
- Job-Ledger und Annahme-Ledger sind **prozesslokal**, auf 100 000 Einträge begrenzt,
  und die Einträge laufen nie ab.
- Alle Schlüssel hängen an **einem Root-Secret**, und es gibt **einen**
  Worker-Schlüssel für alle Worker.
- Der Anker läuft unter demselben Betriebssystem-Nutzer. Wer seine Zustandsdatei
  schreiben kann, kann sie auf einen älteren, gültig signierten Kopf zurückschneiden.
- Freigaben (Approvals) erteilt nur der Server. Es gibt keine eigene Rolle für
  Freigebende.
- Die Worker-Isolation ist eine Prozessgrenze, keine microVM. Außerhalb von Linux
  x86_64/aarch64 läuft kein Worker (fail closed). Prozessstart sperrt der Kernel
  (Seccomp). Worker-Code darf aber Dateien des Hosts lesen; das hält ein Test offen.
- Der HTTP-Eingang hat kein TLS, kein Rate-Limiting und keine Sessions. Das ist
  Deployment-Aufgabe.

## Nächste Schritte (v0.2)

1. #36 mergen (Anker-Persistenz).
2. Tag `v0.1` auf `3a0e1bc` anlegen (Projektverantwortlicher).
3. Einträge in Job- und Annahme-Ledger ablaufen lassen, statt bei 100 000 alles
   abzulehnen.
4. Beide Ledger persistent machen: Ein Neustart nimmt nichts doppelt an.
5. Den Anker als eigenständigen Dienst unter eigenem Nutzer betreiben.
6. Das Gateway in einen eigenen Prozess legen.
7. Eine Rolle für Freigebende mit eigener HTTP-Route einführen.
8. Wartende Jobs sollen einen Neustart überstehen.
9. Einen Schlüssel pro Worker einführen.
10. Doku zu TLS und Rate-Limiting mit Reverse-Proxy-Beispiel.
11. Die Tests zusätzlich unter macOS in der CI.
12. Design-Notiz: Worker mit Netzwerk-Allowlist.
13. `docs/ROADMAP-V02.md` anlegen, danach den Tag `v0.2`.

## Zusammenarbeit der Werkzeuge

| Werkzeug | Rolle | Liest |
| --- | --- | --- |
| Claude Code | Umsetzung, PRs, CI bis grün | `AGENTS.md` |
| ChatGPT Pro / Codex | Umsetzung und Reviews, z. B. Quickstart-Review #33 | `AGENTS.md` |
| GitHub Copilot Pro | Vervollständigung im Editor, PR-Reviews | `.github/copilot-instructions.md` |
| NotebookLM (Gemini) | Fragen an den Projektstand, Zusammenfassungen | dieses Dokument, `SECURITY.md`, `docs/ROADMAP-V01.md` als Quellen |
| Microsoft 365 Copilot | Berichte, Präsentationen, E-Mails zum Stand | dieses Dokument (in OneDrive/SharePoint abgelegt) |

Regel für alle: Gemergt wird nur mit ausdrücklichem OK des Projektverantwortlichen, und
jede Änderung läuft über einen PR mit grüner CI.
