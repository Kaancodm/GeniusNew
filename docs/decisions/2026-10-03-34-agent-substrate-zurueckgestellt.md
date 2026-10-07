# Agent Substrate zurückgestellt

- Datum: 03.10.2026
- Quelle: Chat mit Claude Code, 03.10.2026 (Research-Prüfung)

## Entscheidung

**Agent Substrate zurückgestellt (Kaan):** Für v0.2 bleibt die Worker-Isolation bei Prozessgrenze, Seccomp und Landlock (A1, A2). Agent Substrate (gVisor/microVM, Kubernetes) ist Kandidat für eine spätere, stärkere Isolation

## Begründung

Kubernetes- und microVM-Umbau vor dem MVP wäre ein Architekturwechsel; Teile von Agent Substrate gelten im Projekt selbst noch als „aspirational“
