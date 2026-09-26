# Copilot-Anweisungen für GeniusNew

Vollständige Regeln: `AGENTS.md`. Projektstand: `docs/STATUS.md`. Verbindlich sind
der tatsächliche Code, `SECURITY.md`, Tests und CI am angegebenen Commit; Pläne und
Quellenexporte können veraltet sein. Nicht überprüfbare Angaben als `UNKNOWN`
kennzeichnen.

## Projekt und Arbeitsweise

- Aktives Repository: `Kaancodm/GeniusNew`. Der Projektname bleibt GeniusNew.
- `Kaancodm/Agent-Genius` ist ausschließlich Lesequelle. Übernahmen laufen über
  `docs/MIGRATION-MATRIX.md`; Agent Common gehört nicht zu diesem Projekt.
- Vor Änderungen Remote, Branch, vollständigen SHA und lokale Änderungen prüfen.
  Bestehende Arbeit erhalten; pro Aufgabe ein eigener Branch, kein Direkt-Push auf
  `main`. Ein Thema pro Draft PR, keine beiläufigen Refactorings oder Umbenennungen.
- Doku und PR-Texte Deutsch; Code, Kommentare, Docstrings und Commit-Betreff Englisch.
- Python 3.11+ und hash-gepinnte Abhängigkeiten verwenden. Neue Abhängigkeiten
  benötigen eine Entscheidung des Projektverantwortlichen.

## Sicherheitsregeln

- Öffentliches Repository: keine Zugangsdaten, Tokens, privaten Schlüssel, echten
  `.env`-Dateien, Produktionsdaten, privaten Endpunkte oder vertraulichen Unterlagen.
- Fail closed: unklare Eingabe oder fehlender Zustand führt zu `ContractError`,
  niemals zu einer stillen Freigabe oder einem permissiven Default.
- Client-Eingaben, Tool-Ausgaben, Fremdcode und serialisierte Daten sind untrusted.
  Identität, Rechte, Policy, Tier und Freigaben stammen aus geprüftem Serverzustand.
- Orchestrator, Gateway, Worker, Ergebnisprüfung und Audit behalten ihre getrennten
  Vertrauensrollen. Nur signierende Rollen halten private Ed25519-Schlüssel;
  Prüfer erhalten die öffentliche Hälfte und lehnen private Schlüssel ab.
- Isolation, TTL, Replay-Schutz, Approvals, Allowlist, Signaturprüfung und Audit
  nicht abschwächen. Tests und Refusal-Guard nicht überspringen oder deaktivieren.
- Jede Ablehnung braucht einen Test, der ihr Fehlen bemerkt. Neue Module mit
  Ablehnungen in `GUARDED` in `scripts/refusals.py` aufnehmen.
- Bekannte Grenzen aus `SECURITY.md` nur in einem fokussierten PR schließen, mit
  passenden Tests und aktualisierter Dokumentation.
- Demo und HTTP-Eingang bleiben lokal; eine öffentliche Bereitstellung ist eine
  eigene, ausdrücklich freizugebende Aufgabe.

## Prüfung und Übergabe

Für Codeänderungen unter Linux/WSL in einer isolierten Python-Umgebung ausführen:

```sh
python3 -m pip install --require-hashes -r requirements.txt
python3 -W error::ResourceWarning -m unittest discover -s tests
./scripts/demo.sh
python3 scripts/refusals.py geniusnew/<geaendertes_modul>.py
git diff --check
```

Tests, Demo und Refusal-Guard nacheinander ausführen. Der Modulpfad ist durch die
tatsächlich geänderten Module zu ersetzen; die CI prüft die gesamte Modulmatrix.
Reine Dokumentationsänderungen benötigen passende Link-, Konsistenz- und
Diff-Prüfungen. Ergebnisse nur als bestanden melden, wenn sie tatsächlich vorliegen.

PR und Übergabe nennen Baseline, vollständigen Head-SHA, ausgeführte Befehle,
Ergebnisse, CI-Link und offene Grenzen. Lokale Umsetzung, Tests, CI, Review, Merge
und Release getrennt ausweisen. Sicherheitskritische Änderungen benötigen eine
unabhängige Prüfung des aktuellen Heads; der Implementierer ist nicht alleiniger
abschließender Prüfer. Review-Befunde am Diff und mit geeigneten Tests nachprüfen.

## Beim Review besonders prüfen

1. Gibt es einen Pfad mit stillem `return`, breitem `except` oder Default statt Ablehnung?
2. Hält eine prüfende Instanz einen privaten Schlüssel oder ein Root-Secret?
3. Erkennt ein Test das Entfernen jeder neuen Ablehnung, und steht das Modul in `GUARDED`?
4. Werden eine Grenze aus `SECURITY.md` und ihr offen haltender Test gemeinsam angepasst?
5. Gelangen Nutzdaten oder Secrets in Audit, Logs oder Fehlermeldungen?

Merge, Release, Deployment, Zugriffs- und Schutzänderungen, Produktionsinfrastruktur,
Daten- oder Branchlöschung und das Rotieren von Zugangsdaten benötigen Kaans
ausdrückliches OK. Dazu zählt auch das Abschalten der SSH-Kennwortanmeldung.
Prüfbare Änderungen und Nachweise vor dieser Entscheidung vorbereiten.
