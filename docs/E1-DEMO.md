# E1: Persistenz-Angriffe in der Demo

Die zusätzliche Demo nutzt ausschließlich eine wegwerfbare PostgreSQL-Datenbank,
die sie über `GENIUSNEW_TEST_ADMIN_DSN` anlegt und wieder entfernt. Sie verwendet
keine produktiven Zugangsdaten und keinen öffentlich erreichbaren Endpunkt.

`python scripts/demo_persistence.py` prüft drei Fälle mit dem echten dauerhaften
Dienst. `./scripts/demo.sh` bleibt die kurze HTTP-Demo ohne Datenbank:

1. Ein abgeschlossener Job wird nach Neustart mit derselben Kennung erneut
   eingereicht; zusätzlich wird sein tatsächlich signiertes Ergebnis erneut
   vorgelegt. Der Dienst lehnt beides ab und führt den Worker nicht nochmals aus.
2. Ein wartender Job wird durch SQL als Datenbank-Owner aus `pending_jobs` und
   `job_ledger` entfernt. Beim Neustart verweigert der Dienst den Zustand, weil
   das signierte `HANDOFF_ISSUED`-Event keine Ledger-Zeile mehr hat.
3. Ein gültig signierter Ankerzustand wird mit einer älteren, leeren Datenbank
   kombiniert. Der Dienst verweigert den Start, statt den Anker zurückzusetzen.

Jeder Fall hat eine eigene Datenbank und einen eigenen Ankerzustand. Die Demo
meldet nur beobachtbare Zustände und Ablehnungen; Schlüssel, Payload und
Verbindungsdaten erscheinen nicht in der Ausgabe. Die CI führt sie nach Tests
und der bisherigen HTTP-Demo aus. Jede unerwartete Annahme beendet sie mit
einem Fehlercode.
