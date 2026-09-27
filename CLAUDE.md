# GeniusNew — Claude Code

@AGENTS.md
@docs/COLLABORATION.md

Diese beiden Dateien sind verbindlich. Bei sicherheitsrelevanter Arbeit zusätzlich
`SECURITY.md` lesen. Für Übergaben gilt `docs/HANDOVER.md`.

## Claude-spezifische Arbeitsweise

- Kleine und eindeutig sequenzielle Aufgaben direkt bearbeiten.
- Subagents nur für unabhängige, parallelisierbare Teilaufgaben einsetzen. Keine
  Subagents für eine einzelne Datei, einen kleinen Fix oder rein sequenzielle Arbeit.
- Keine zusätzlichen Pflichtprüfungen erfinden. Verifikation genau nach `AGENTS.md`
  und den tatsächlich betroffenen Modulen durchführen.
- Nur tatsächlich ausgeführte Prüfungen als PASS melden; alles andere ist `UNKNOWN`.
- Repo-Zustand, Diff, Tests und CI sind maßgeblich. Pläne und Prompts sind Kontext.
- Die Rollen- und Merge-Grenzen aus `docs/COLLABORATION.md` bleiben unverändert.
- Keine Secrets, keine Tests abschwächen, keine Sicherheitsgrenzen nebenbei ändern.
