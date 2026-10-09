# Folio — lokale Dokument-App

Folio ist ein eigenständiger lokaler MVP **innerhalb des GeniusNew-Repositories**. Er
ändert den Agenten-Kern und das geplante Portal nicht. Die App bietet Konten mit Login,
formatierte Seiten und Freigaben für registrierte Nutzer als `viewer` oder `editor`.
Inhalte werden beim Speichern serverseitig bereinigt. Gleichzeitige Änderungen werden
über eine Versionsnummer erkannt; bei einem Konflikt muss die Seite neu geladen werden.

## Starten

Python 3.11 oder neuer und die bestehenden Repository-Abhängigkeiten genügen. Die
SQLite-Datei gehört **außerhalb des Repositories** und muss vertraulich behandelt und
gesichert werden:

```sh
python3 -m document_app.app init --database /tmp/folio-local.sqlite3
python3 -m document_app.app serve --database /tmp/folio-local.sqlite3
```

Danach `http://127.0.0.1:8765` öffnen. Für eine Freigabe registriert sich die
eingeladene Person zuerst selbst; der Eigentümer trägt anschließend deren E-Mail in
„Share“ ein. Der Server bindet ausschließlich an Loopback.

## Grenzen des lokalen MVP

Dies ist keine Produktionsfreigabe. Der bestehende Portal-Entwurf in
`docs/DATABASE.md` verlangt für den späteren Betrieb eine separate PostgreSQL-Datenbank
und Argon2id. Folio verwendet lokal SQLite und `scrypt`, benötigt keine neuen
Abhängigkeiten und darf nicht als Vercel-Portal bereitgestellt werden. Gemeinsames
Bearbeiten erfolgt durch Teilen und erneutes Laden, ohne Live-Cursor oder Echtzeit-Sync.
Eine Konto-Wiederherstellung und ein Rate-Limit für Login-Versuche fehlen noch.
