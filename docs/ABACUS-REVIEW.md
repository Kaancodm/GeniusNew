# Abacus.AI im GeniusNew-Workflow

Der Terminaladapter `python3 -m tools.abacus_review` sendet einen ausdrücklich
vorbereiteten Review-Text an die Abacus RouteLLM API. Er nutzt nur die
Python-Standardbibliothek. Das Control Deck öffnet weiterhin ChatLLM im Browser;
ein Browser-Link ist kein Nachweis für einen funktionierenden API-Zugang.

## Zugang und Modell

1. Einen bereits in einem Chat veröffentlichten API-Schlüssel in Abacus widerrufen.
2. Unter **Apps & APIs → LLM APIs → Use RouteLLM or 70+ LLMs Directly Via Our API**
   einen neuen RouteLLM-Schlüssel erstellen. API-Billing und Modellverfügbarkeit im
   eigenen Abacus-Konto prüfen; ein ChatLLM-Abonnement beweist kein API-Kontingent.
3. Den neuen Schlüssel ausschließlich lokal verwenden. Der Adapter fragt ihn verdeckt
   im Terminal ab. Alternativ kann ein lokaler Secret-Manager `ABACUS_API_KEY` als
   Prozessumgebung bereitstellen. Keine Schlüssel in Git, Chat, CLI-Argumenten oder
   Shell-History ablegen. Ohne ein Terminal mit verdeckter Eingabe wird abgelehnt.
4. Die genaue Modell-ID aus Abacus wählen. Für das Pflicht-Sicherheitsreview verlangt
   [der Projektworkflow](COLLABORATION.md#gemini-review-format-seit-04102026) ein
   Gemini-Pro-Modell. Der Adapter verlangt eine explizite Modell-ID und lehnt
   `route-llm` ab; er bescheinigt weder Modellidentität noch Review-Gültigkeit.

Die Self-Serve-API ist fest auf
`https://routellm.abacus.ai/v1/chat/completions` begrenzt. Enterprise-Workspace-URLs
sind nicht unterstützt. Grundlage ist die
[offizielle Abacus-Dokumentation](https://abacus.ai/help/route-llm/chat-completions/).

## Review ausführen

Im Repository-Root auf dem Gerät oder Server, auf dem der neue Schlüssel verfügbar ist:

```sh
python3 -m tools.abacus_review --help
python3 -m tools.abacus_review \
  --model '<exakte-Modell-ID>' \
  --head '<voller-geprüfter-Commit-SHA>' \
  < /pfad/ausserhalb-des-repos/review-input.txt
```

Die Eingabedatei enthält PR-Nummer, Review-Maßstab, freigegebene Quelltexte/Diffs und
lokale Prüfergebnisse. Vorher selbst auf Secrets und vertrauliche Inhalte prüfen:
**Der gesamte Eingabetext wird an Abacus übertragen.** Der Adapter liest keine
Repository-Dateien automatisch und prüft nicht, ob der angegebene SHA zum Text passt.
Keine Datei mit echten Secrets als Review-Eingabe verwenden.

Die Ausgabe ist eine ungeprüfte Zweitmeinung. Ein Gemini-Review wird erst nach
inhaltlicher Prüfung im vereinbarten Format am exakten Head-SHA als PR-Kommentar
eingetragen. Der Adapter veröffentlicht nichts und gibt keine Freigaben. Die Ausgabe
nennt das angeforderte Modell; sie beweist nicht, welches Modell Abacus ausgeführt hat.

## Grenzen und Fehler

- Ein expliziter Aufruf erzeugt eine API-Anfrage und kann Kosten verursachen.
- Höchstens 128.000 UTF-8-Bytes Eingabe, 4.096 Ausgabetokens, 1.000.000 Bytes Antwort.
- Netzwerkoperationen haben ein Timeout von 120 Sekunden; keine automatischen Retries.
- Keine Redirects und keine Proxy-Umgebungsvariablen für die Bearer-Anfrage.
- Netzwerkfehler, ungültige Antworten, Tool-Calls und abgeschnittene Ergebnisse
  führen zu Exit-Code `1` und `UNKNOWN`. Fehler-Bodies und Schlüssel werden nicht
  ausgegeben. Kein Ersatzmodell und kein stilles Auto-Routing.
- Keine Anbindung an Worker, Gateway, Approvals, Audit oder Control-Deck-HTTP-Aktionen.

Ein erfolgreicher synthetischer Test beweist keinen Live-Zugang. Den Live-Nachweis
erst mit dem neu erstellten Schlüssel und einem unkritischen Testtext durchführen.
