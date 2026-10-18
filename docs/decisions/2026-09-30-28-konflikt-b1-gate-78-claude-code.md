# Konflikt B1-Gate #78 (Claude Code)

- Datum: 30.09.2026
- Quelle: #78, #83, `docs/DATABASE.md` §4.2, §8, §9, §10

## Entscheidung

**Konflikt B1-Gate #78 (Claude Code):** B1 (#78, gemergt `706e76d`, Head `99ef6e36…`) ist **GO**; keine Nachbesserung, kein Revert, Migration 0001 bleibt unverändert. Die drei späteren Codex-Befunde sind verbindlich zugeordnet: (1) privilegierte Runtime-DSN → **B2-E1**, vor dem B2-Merge; (2) Schema-Fingerprint → **Architekturfrage für Kaan**, Empfehlung: im Code gepinnt, Fälligkeit vor dem B3-Merge, keine Claude-Auflage; (3) Ledger-Insert in beliebigem Zustand → **eigene Migration mit BEFORE-INSERT-Trigger** (nur `PENDING_APPROVAL`/`RESERVED`), Fälligkeit vor dem B3-Merge, Entwurfsänderung braucht Kaans Freigabe. **B2-Merge: HOLD**, bis E1 und E2 (in #83 umgesetzt) am exakten Head unabhängig bestätigt sind; **B3-Merge: HOLD** bis Punkt 3, E3 und Kaans Entscheidung zu Punkt 2

## Begründung

Auf `main` nutzt die Laufzeit die Ledger nicht (prozesslokal, `SECURITY.md`); alle drei Befunde wirken erst mit dem ersten Laufzeit-Schreibzugriff (B2) bzw. der Acceptance (B3). Befund 3 ist belegt: Die Runtime-Rolle kann alle fünf Zustände einfügen, weil der Übergangstrigger nur `BEFORE UPDATE` läuft. Das Codex-Review kam nach dem Merge (23:09:57 gegen 23:01:49 Uhr). Ein Fingerprint schützt nur, wenn er im Code steht, nicht in der Datenbank, die der Owner ändern kann. Claude war Partei (eigenes Review) und schrieb die B2-Basis; Kaan kann eine Zweitmeinung einholen
