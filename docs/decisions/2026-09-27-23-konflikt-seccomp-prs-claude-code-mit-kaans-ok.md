# Konflikt Seccomp-PRs (Claude Code, mit Kaans OK)

- Datum: 27.09.2026
- Quelle: PR #57, #63

## Entscheidung

**Konflikt Seccomp-PRs (Claude Code, mit Kaans OK):** Für den Prozessstart per Seccomp gilt nur #57 (nur geprüftes x86_64, sonst fail closed; reproduzierbarer Escape-Test). #43 und #63 sind geschlossen. aarch64 kommt später als eigener PR mit nativer CI

## Begründung

Drei PRs zu einem Thema; ungeprüfte Architekturen bleiben gesperrt
