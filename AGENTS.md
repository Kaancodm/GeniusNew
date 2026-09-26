# AGENTS.md — Arbeitsanweisungen für KI-Assistenten

Gilt für jeden Assistenten, der in diesem Repository arbeitet: Claude Code, ChatGPT /
Codex, GitHub Copilot, Gemini. Kurzfassung für Copilot:
`.github/copilot-instructions.md`. Aktueller Projektstand: `docs/STATUS.md`.

## Was das ist

GeniusNew ist ein Zero-Trust-Agentensystem. Ein Auftrag kommt über HTTP herein, der
Orchestrator stellt einen signierten Handoff aus, das Gateway prüft ihn unabhängig und
mintet einen einmaligen `DispatchPermit`, ein Worker läuft in einem isolierten Prozess,
die Ergebnisprüfung nimmt das signierte Ergebnis an, und jede Entscheidung landet in
einer hash-verketteten Audit-Kette, deren Kopf ein Anker in eigenem Prozess festhält.
Keine Instanz bestätigt ihre eigene Entscheidung.

Sprache: Doku und PR-Texte **Deutsch**, Code, Kommentare, Docstrings und
Commit-Betreff **Englisch**.

## Einrichten und prüfen

```sh
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install --require-hashes -r requirements.txt
python3 -W error::ResourceWarning -m unittest discover -s tests   # Sekunden
./scripts/demo.sh                                                 # letzte Zeile: PASS …
python3 scripts/refusals.py geniusnew/<modul>.py                   # Minuten pro Modul
```

Die CI (`.github/workflows/verify.yml`) führt die Tests und den Refusal-Guard als
Matrix pro Modul aus. Der zusammenfassende Check heißt `contracts`.

## Harte Regeln

1. **Keine Secrets** im Repository: keine Schlüssel, Tokens, `.env`, keine echten
   Daten. Die Demo leitet ihre Schlüssel aus einem festen Demo-Secret ab.
2. **Fail closed.** Unklare Eingabe, fehlender Zustand, nicht erreichbare Instanz:
   ablehnen mit `ContractError`, nie still weitermachen oder auf einen Default fallen.
3. **Jede Ablehnung braucht einen Test, der ihr Fehlen bemerkt.** `scripts/refusals.py`
   schaltet jede Ablehnung einzeln ab und verlangt, dass die Suite rot wird. Ein neues
   Modul mit Ablehnungen kommt in `GUARDED` in `scripts/refusals.py`.
4. **Nie** einen Test überspringen, deaktivieren oder abschwächen, um grün zu werden.
5. **Signaturrollen trennen.** Alle Signaturen sind Ed25519. Nur die signierende Rolle
   hält den privaten Schlüssel (`HandoffSigner` im Orchestrator, `WorkerAuthority` am
   Worker-Rand, `AuditAuthority` für Audit-Köpfe). Alles, was nur prüft, bekommt die
   öffentliche Hälfte (`HandoffVerifier`, `WorkerVerifier`, `AuditVerifier`) und lehnt
   es ab, mit der privaten gebaut zu werden.
6. **Bekannte Grenzen stehen in `SECURITY.md`.** Viele sind durch einen Test
   „offen gehalten“ (`…_and_this_is_the_boundary`). Eine Grenze zu schließen ist ein
   eigener PR, der diesen Test umkehrt und `SECURITY.md` anpasst, nie ein
   Nebeneffekt.
7. **Abhängigkeiten** sind hash-gepinnt (`requirements.txt`, `--require-hashes`).
   Eine neue Abhängigkeit ist eine Entscheidung des Projektverantwortlichen.
8. **Altprojekt** `Kaancodm/Agent-Genius` ist nur Lesequelle. Übernahmen nur über das
   Gate in `docs/MIGRATION-MATRIX.md`, und dann neu gebaut, nicht kopiert.
9. **Nicht umbenennen.** Das Projekt heißt GeniusNew.

## Arbeitsweise

- Klein schneiden: ein Thema pro PR, als Draft. Ein PR, der älter als etwa zwei Tage
  wird, ist zu groß.
- Vor dem Push: Tests, Demo und Refusal-Guard für die geänderten Module lokal grün.
- Mergen nur mit ausdrücklichem OK des Projektverantwortlichen (Kaan).
- Kommentare erklären das *Warum* und die Grenze, nicht das *Was*; so wie der
  umgebende Code.

## Wo was steht

| Thema | Datei |
| --- | --- |
| Projektstand und nächste Schritte | `docs/STATUS.md` |
| Übergabe-Prompts zwischen Werkzeugen | `docs/HANDOVER.md` |
| Roadmap v0.1 mit Status je Schritt | `docs/ROADMAP-V01.md` |
| Bekannte Grenzen | `SECURITY.md` |
| Verfassung (Rollen, §7 Audit, §8 Trennung) | `docs/CONSTITUTION-V1-DRAFT.md` |
| Handoff-Format | `docs/HANDOFF-V2.md`, `schemas/handoff-v2.schema.json` |
| Zusammenbau aller Teile | `geniusnew/wiring.py` |
