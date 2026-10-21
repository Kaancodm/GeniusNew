# Produkt 1: Text-Digest (`digest`)

Das erste nutzbare Produkt hinter dem Zero-Trust-Gate: Aus einem Text (Deutsch oder
Englisch) erzeugt der isolierte Worker `TextDigest` deterministisch ein kompaktes
Markdown-Digest — Kernsätze im Wortlaut, Schlüsselbegriffe, Zahlen mit Einheiten,
Lesezeit und Lesbarkeit. Kein Modell, kein Netz im Worker, keine Erfindung: Jede Zeile
des Digests ist von Hand gegen den Eingangstext nachrechenbar, und das Ergebnis trägt
dieselbe Signatur- und Audit-Kette wie jeder andere Job.

## Beispiel (echte Ausgabe der Produkt-Demo)

```markdown
# Text-Digest

Sprache: de · 169 Wörter · 14 Sätze · Lesezeit: 51 Sek · Lesbarkeit (Flesch, ca.): 63 (mittel)

## Kern in Sätzen

Das Ergebnis wird vom Worker signiert und von einer Prüfinstanz angenommen, die nur öffentliche Schlüssel hält.
Jede Entscheidung landet in einer verketteten Audit Kette.
Ein Anker in einem eigenen Prozess hält den signierten Kopf dieser Kette fest.

## Schlüsselbegriffe

kette (4), anker (2), entscheidung (2), handoff (2), hält (2), macht (2), prozess (2), schlüssel (2), signiert (2), worker (2)

## Zahlen im Text

- 60
- 15
- 62.137 Records
- 16 MiB
```

## Ausprobieren

```sh
python3 scripts/demo_product.py        # Beispieltext rein, Digest + verifizierte Kette raus
```

Braucht wie die Sicherheits-Demo einen Host mit Landlock ABI 4; Exit 0 nur, wenn der
Job `SUCCEEDED` endet und die Audit-Kette gegen den verankerten Kopf verifiziert.

## Als Dienst nutzen

`digest` ist ein eingebautes Tool (`BUILTIN_WORKERS`). In der Server-Konfiguration
genügen Policy und Grant (Ausschnitt, Rest wie `docs/examples/geniusnew.toml`):

```toml
[policy]
allowed_tools = ["digest"]

[[policy.grants]]
subject = "kunde-a"
user_id = "user-a"
worker_agent_id = "worker-digest"
tier = "basic"
tools = ["digest"]
sandbox_profile = "isolated"
requires_approval = false
```

Client (nur Standardbibliothek, Schlüssel nie in der Ausgabe):

```sh
python3 -m tools.genius_client --url https://geniusnew.example.org \
    --api-key-file ~/.config/geniusnew/api-key --file bericht.txt --out digest.md
# oder: cat bericht.txt | GENIUSNEW_API_KEY=… python3 -m tools.genius_client
```

Welches Tool läuft, entscheidet allein der serverseitige Grant — der Request trägt
außer `{"text": …}` nichts. Ein Grant mit `requires_approval = true` liefert
`PENDING_APPROVAL`; der Client benennt dann die Job-ID für den Freigebefluss.

## Grenzen (v1, ehrlich)

- **Textlänge:** ein Job ist durch den Wire-Vertrag auf 16 KiB begrenzt; praktisch
  ~14 KB Text (~2.000 Wörter). Längere Dokumente brauchen später Chunking bzw. den
  asynchronen Pfad aus dem Portal-Vertrag (D1) — der Client verweigert Überlänge vor
  dem Senden, statt sie dem Server zu überlassen.
- **Methode:** extraktiv (Sätze werden zitiert, nicht umformuliert), Schlüsselbegriffe
  per Häufigkeit mit DE-/EN-Stoppwortliste, Silbenzählung und Flesch sind ausgewiesene
  Näherungen. Für Fließtext gebaut; Markdown-Tabellen oder Code werden nicht eigens
  interpretiert.
- **Sprachen:** DE/EN werden erkannt; andere Texte erhalten Statistiken und
  Satz-Scores mit „nicht sicher erkannt", statt eine Sprache zu raten.
- Das Digest ist ein signiertes Worker-Ergebnis wie jedes andere: `FAILED` mit
  geschlossenem Reason-Code statt Freitext-Fehler, TTL und Permit-Pfad unverändert.
