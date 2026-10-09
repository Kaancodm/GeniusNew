# Offene Punkte aus Konflikt B1-Gate

- Datum: 30.09.2026
- Quelle: Antwort von Kaan im Chat mit Claude Code, 30.09.2026; #78

## Entscheidung

**Offene Punkte aus Konflikt B1-Gate (Kaan):** kein Schema-Fingerprint, keine Migration, die Ledger-Inserts auf `PENDING_APPROVAL`/`RESERVED` beschränkt, keine Zweitmeinung zur Konfliktentscheidung. Die B3-Merge-Bedingung schrumpft damit auf E3 (Acceptance-Startinvariante); B2 bleibt bei E1/E2

## Begründung

Kaans Architekturentscheidung. Folge, bewusst in Kauf genommen: Die Runtime-Rolle kann eine Zeile direkt als `EXECUTION_COMMITTED`, `COMPLETED` oder `REFUSED` anlegen, und ein Owner kann Trigger oder Constraints nach der Migration ändern, ohne dass der Start es bemerkt. Beides schützt nur der Code, nicht die Datenbank
