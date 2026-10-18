# Alle Signaturen Ed25519; prüfende Instanzen halten nur öffentliche Schlüssel

- Datum: 25.09.2026
- Quelle: `SECURITY.md`, PRs #31/#34/#35

## Entscheidung

Alle Signaturen Ed25519: Audit-Köpfe (#31), Handoffs (#34), Ergebnisse (#35); prüfende Instanzen halten nur öffentliche Schlüssel

## Begründung

Mit HMAC konnte jede prüfende Instanz auch signieren
