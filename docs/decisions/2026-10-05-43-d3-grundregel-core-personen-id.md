# D3-Grundregel: kanonische Core-Personen-ID

- Datum: 05.10.2026
- Quelle: PR #120, Kommentar vom 05.10.2026, https://github.com/Kaancodm/GeniusNew/pull/120#issuecomment-6001623893; Kaans Bestätigung im Gespräch „Ja, diese Regel festlegen“

## Entscheidung

**Kaan (05.10.2026):** Core vergibt eine stabile kanonische Personen-ID und verwaltet die Bindung von Portal-Konten, direkten API-Keys und Approver-Subjects an diese ID. Unbekannte oder mehrdeutige Bindungen werden abgelehnt. Der Portal-Service-Principal bleibt vom Nutzer getrennt; Selbstfreigabe wird anhand der Core-Personen-ID verweigert, auch bei verschiedenen Sessions, Keys oder Subjects. Der konkrete Beleg für Alias-Zusammenführung und der Aufnahme-/Sperrprozess bleiben HOLD für einen eigenen D3-Vertrag.

## Begründung

Kaan bestätigte die von Codex vorgeschlagene Grundregel ausdrücklich. Sie ist im D1-Entwurf (`docs/PORTAL-CORE-D1-DRAFT.md`) festgehalten; Codex bat darum, sie in die Entscheidungsdateien zu übernehmen.
