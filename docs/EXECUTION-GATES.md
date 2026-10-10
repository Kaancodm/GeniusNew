# Ausführungs-Gates für GeniusNew

Stand: 06.10.2026. Entscheidung von Kaan.

Diese Datei definiert, wann Werkzeuge und Agenten selbstständig handeln dürfen und wann
eine ausdrückliche Freigabe von Kaan erforderlich ist. Sie ist für ChatGPT, Codex,
Claude Code, Gemini, Kiro, Copilot und Automationen wie n8n verbindlich.

Die Regel ist bewusst einfach:

- **AUTO:** Entwicklungs-, Test-, Tool-, Staging- und Automationsarbeit darf selbstständig
  ausgeführt werden, wenn sie reversibel ist und keine der GO-Grenzen berührt.
- **GO:** Vor den unten genannten Grenzen muss Kaan ausdrücklich freigeben.

## AUTO

1. Read-only prüfen: Repository, CI, Logs, Server, Dienste, Netzwerkstatus,
   Konfiguration, Versionen und Sicherheitszustand.
2. Reversible Entwicklungsänderungen: User-Space-Tools, Worktrees, Testcontainer,
   temporäre Dienste und Konfigurationen mit überprüfbarem Rückweg.
3. Entwicklung auf Arbeitsbranches: Code, Tests, Commits, Rebase und PR-Erstellung.
   Kein Direkt-Push auf `main`.
4. Unit-, Integrations-, Regression-, Security-, Restore- und isolierte Lasttests gegen
   Dev/Test/Staging. Keine Lasttests gegen Produktion.
5. Agentenwechsel und Fallbacks bei Kontingent- oder Toolblockern, sofern bestehende
   fachliche Reviews und Gates erhalten bleiben.
6. Secret-Konfiguration auf Vorhandensein, Dateirechte und korrekte Einbindung prüfen,
   ohne Secret-Werte auszugeben.
7. Interne Netze: Loopback, Container-Netze, lokale Ports und nicht öffentliche
   Testdienste.
8. Dev/Test-Datenbanken: Schema-, Migrations-, Restore- und Manipulationstests mit
   Testdaten.
9. Test-/Staging-Deployments einschließlich Preflight und Rollback-Proben.
10. Git-Arbeit auf Nicht-`main`-Branches einschließlich Commit und PR.
11. Automationen mit begrenzten Rechten für Reports, Handoffs, CI, Slack, Research,
    Monitoring und Routineaufgaben.
12. Normale Blocker selbstständig untersuchen, reparieren, testen und mit einem sicheren
    Fallback weiterführen.

## GO erforderlich

Vor der Ausführung ist Kaans ausdrückliche Freigabe erforderlich für:

- Merge oder Direktänderung an `main`.
- Produktionsdeployment, öffentlicher Release oder Release-Tag.
- Neue oder geänderte öffentliche Netzwerkfreigaben, Firewall- oder SSH-Zugangsregeln.
- Erzeugen, Rotieren, Ersetzen oder Offenlegen von Secrets/Schlüsseln.
- Änderungen an echten produktiven Daten oder produktiven Datenbankrechten.
- Irreversible oder destruktive Aktionen wie Force-Push, Löschen wichtiger Branches,
  endgültiges Löschen von Daten oder nicht sicher rückgängig machbare Migrationen.

## Sicherheits- und Review-Regeln bleiben bestehen

AUTO bedeutet nicht Selbstfreigabe. Bestehende projektspezifische CI-, Review-,
Security-, DB- und Evidenz-Gates bleiben gültig. Ein Agentenwechsel oder eine
Automation darf sie nicht umgehen. Tests und Reviews müssen sich auf den tatsächlich
geprüften Head-SHA beziehen, wenn die bestehende Projektregel das verlangt.

## Automationsvertrag für n8n und ähnliche Werkzeuge

Automationen dürfen AUTO-Aktionen auslösen. Sie erhalten standardmäßig keine
Berechtigung für `main`-Merge, Produktionsdeployment, Secret-Rotation, öffentliche
Netzwerkänderungen oder Schreibzugriff auf produktive Core-Daten. Erreicht ein Workflow
eine GO-Grenze, stoppt er und fordert Kaans Freigabe an.

## Konfliktregel

Diese Entscheidung vom 06.10.2026 ersetzt ältere Regeln, soweit diese Codex oder einem
anderen Werkzeug einen automatischen Merge nach `main` erlaubten oder für normale
reversible Dev-/Testarbeit zusätzliche manuelle Freigaben verlangten. Fachliche
Spezialgates bleiben bestehen, sofern sie nicht ausdrücklich einer GO-Grenze
widersprechen.

Die einmalige Eintragung dieser Entscheidung in die Governance-Dateien ist von Kaan
direkt beauftragt. Danach gelten die bestehenden Datei-Zuständigkeiten weiter.
