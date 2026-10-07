# Konflikt 1 (Claude Code)

- Datum: 26.09.2026
- Quelle: Issue #44

## Entscheidung

**Konflikt 1 (Claude Code):** Der Datenbank-Entwurf folgt dem Code (`job_ledger`, `acceptance_ledger`, `pending_jobs`), trennt Kern- und Portal-Datenbank und speichert signierte Daten byte-genau (`bytea`, nicht `jsonb`)

## Begründung

Signaturen und Hashes gelten nur für die exakten Bytes
