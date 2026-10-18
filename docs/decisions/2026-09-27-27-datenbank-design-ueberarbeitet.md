# Datenbank-Design überarbeitet

- Datum: 27.09.2026
- Quelle: Chat mit Kaan, 27.09.2026

## Entscheidung

**Datenbank-Design überarbeitet (Kaan):** Kaan entscheidet Ziele, Architektur und offene Fragen (Betriebsort, Technik, neue Dependencies, `SECURITY.md`-Grenzen, Tags, Deployment), muss `docs/DATABASE.md` aber nicht mehr selbst schreiben. **ChatGPT erstellt und pflegt den technischen Entwurf im Auftrag von Kaan** — Schema, Tabellen, Migrationen, Persistenzmodell, Fehler-/Recovery-Verhalten, Verbindungskonzept, Trennung Audit-Kette/Anker-Speicher, Portal-/Core-Datenhaltung, Backup/Restore, Betriebsanforderungen. Claude Code bleibt unabhängiger Sicherheitsreviewer von Entwurf und Code (`docs/DATABASE.md` entwirft Claude weiterhin nicht selbst). Workflow: Kaan entscheidet Ziel → ChatGPT entwirft → Claude Security Review → Kaan entscheidet offene Punkte und gibt frei → Codex implementiert → Claude DB Review am exakten Head-SHA → CI → ggf. Kaan-Gates → Kaan mergt. Löst die Fassung vom selben Tag ab, nach der Kaan `docs/DATABASE.md` selbst schreibt

## Begründung

Kaan trifft Entscheidungen, muss technische Dokumentation aber nicht selbst ausarbeiten; wer entwirft, gibt nicht selbst frei, und wer implementiert, entscheidet nicht selbst über die Architektur
