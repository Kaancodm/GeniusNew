# B5 nur als frische Installation

- Datum: 01.10.2026
- Quelle: #95, `docs/POSTGRES-B1.md`

## Entscheidung

**B5 nur als frische Installation (Kaan):** Eine B4-Installation mit nichtleerem Anker wird nicht still übernommen; B5 verweigert dann den Start. Ein Import ist ein eigener, geprüfter Schritt

## Begründung

B4 hat keine dauerhaften Audit-Records; eine Rekonstruktion wäre nicht beweisbar
