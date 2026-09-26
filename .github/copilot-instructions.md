# Copilot-Anweisungen für GeniusNew

Vollständige Regeln: `AGENTS.md`. Rollen und Merge-Regeln: `docs/COLLABORATION.md`.
Projektstand: `docs/STATUS.md`. Verbindlich sind der tatsächliche Code, `SECURITY.md`,
Tests und CI am angegebenen Commit; Pläne und Quellenexporte können veraltet sein.
Nicht überprüfbare Angaben als `UNKNOWN` kennzeichnen.

## Rolle von Copilot

Vervollständigung im Editor und Review jedes PRs nach der Checkliste unten. Eigene
Umsetzung nur mit einem Auftrag (Issue-Formular „Begrenzte Aufgabe“), auf einem eigenen
Branch und als Draft-PR. Umsetzer und Merger ist Codex; Gemini Pro ist Head der
Datenbank; Claude Code löst Konflikte zwischen den Werkzeugen.

## Projekt und Arbeitsweise

- Aktives Repository: `Kaancodm/GeniusNew`. Der Projektname bleibt GeniusNew.
- `Kaancodm/Agent-Genius` ist ausschließlich Lesequelle. Übernahmen laufen über
  `docs/MIGRATION-MATRIX.md`; Agent Common gehört nicht zu diesem Projekt.
- Vor Änderungen Remote, Branch, vollen SHA und lokale Änderungen prüfen. Bestehende
  Arbeit erhalten; pro Aufgabe ein eigener Branch, kein Direkt-Push auf `main`. Ein
  Thema pro Draft-PR, keine beiläufigen Refactorings oder Umbenennungen.
- Doku und PR-Texte Deutsch; Code, Kommentare, Docstrings und Commit-Betreff Englisch.
- Python 3.11+ und hash-gepinnte Abhängigkeiten. Eine neue Abhängigkeit entscheidet Kaan.

## Sicherheitsregeln

- Öffentliches Repository: keine Zugangsdaten, Tokens, privaten Schlüssel, echten
  `.env`-Dateien, Produktionsdaten, privaten Endpunkte oder vertraulichen Unterlagen.
- Fail closed: Eine unklare Eingabe oder ein fehlender Zustand führt zu `ContractError`,
  nie zu einer stillen Freigabe oder einem großzügigen Default.
- Client-Eingaben, Tool-Ausgaben, Fremdcode und serialisierte Daten sind nicht
  vertrauenswürdig. Identität, Rechte, Policy, Tier und Freigaben stammen aus geprüftem
  Serverzustand.
- Orchestrator, Gateway, Worker, Ergebnisprüfung und Audit behalten ihre getrennten
  Rollen. Nur signierende Rollen halten private Ed25519-Schlüssel; Prüfer bekommen die
  öffentliche Hälfte und lehnen die private ab.
- Isolation, TTL, Replay-Schutz, Approvals, Allowlist, Signaturprüfung und Audit nicht
  abschwächen. Tests und Refusal-Guard nicht überspringen oder deaktivieren.
- Jede Ablehnung braucht einen Test, der ihr Fehlen bemerkt. Neue Module mit
  Ablehnungen kommen in `GUARDED` in `scripts/refusals.py`.
- Bekannte Grenzen aus `SECURITY.md` nur in einem fokussierten PR schließen, mit
  umgekehrtem Test und angepasster Tabelle.
- Signierte oder gehashte Daten werden byte-genau gespeichert (`bytea`), nie als
  `jsonb`.
- Demo und HTTP-Eingang bleiben lokal; eine öffentliche Bereitstellung ist eine eigene,
  von Kaan freizugebende Aufgabe.

## Prüfung und Übergabe

Unter Linux/WSL in einer isolierten Python-Umgebung, nacheinander:

```sh
python3 -m pip install --require-hashes -r requirements.txt
python3 -W error::ResourceWarning -m unittest discover -s tests
./scripts/demo.sh
python3 scripts/refusals.py geniusnew/<geaendertes_modul>.py
git diff --check
```

Reine Doku-Änderungen brauchen Link-, Konsistenz- und Diff-Prüfungen. Ergebnisse nur
als bestanden melden, wenn sie tatsächlich vorliegen. PR und Übergabe nennen Baseline,
vollen Head-SHA, ausgeführte Befehle, Ergebnisse, CI-Link, offene Grenzen und den
Wissensblock (`.github/PULL_REQUEST_TEMPLATE.md`). Sicherheitskritische Änderungen
brauchen eine unabhängige Prüfung des aktuellen Heads.

## Beim Review besonders prüfen

1. Gibt es einen Pfad mit stillem `return`, breitem `except` oder Default statt Ablehnung?
2. Hält eine prüfende Instanz einen privaten Schlüssel oder ein Root-Secret?
3. Erkennt ein Test das Entfernen jeder neuen Ablehnung, und steht das Modul in `GUARDED`?
4. Werden eine Grenze aus `SECURITY.md` und ihr offen haltender Test gemeinsam angepasst?
5. Gelangen Nutzdaten oder Secrets in Audit, Logs oder Fehlermeldungen?

Befunde zu diesen fünf Punkten blockieren den Merge. Release, Deployment, Zugriffs- und
Schutzänderungen, Produktionsinfrastruktur, das Löschen von Daten oder Branches und das
Rotieren von Zugangsdaten brauchen Kaans ausdrückliches OK.
