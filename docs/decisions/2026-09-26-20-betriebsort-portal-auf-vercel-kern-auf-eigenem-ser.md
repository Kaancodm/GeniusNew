# Betriebsort: Portal auf Vercel, Kern auf eigenem Server. Datenbank Pos

- Datum: 26.09.2026
- Quelle: Issue #44

## Entscheidung

Betriebsort: Portal auf Vercel, Kern auf eigenem Server. Datenbank PostgreSQL mit `psycopg`; Portal-Passwörter mit `argon2-cffi` (beides neue Abhängigkeiten, von Kaan freigegeben)

## Begründung

Serverless-Portal braucht eine netzwerkfähige Datenbank; Worker-Isolation und Anker brauchen einen echten Host
