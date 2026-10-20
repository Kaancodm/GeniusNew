# Advisory-Anbindung (`geniusnew/advisory.py`)

Das ist die **einzige** Stelle, an der der Kern mit einem lokalen Modell spricht.
Ein Aufrufer im Kern gibt einen begrenzten Auftrag ab und bekommt untrusted Text
zurück. Nichts anderes kreuzt diese Grenze — in keine Richtung.

Der Kerncode ändert damit keine Rollen: `docs/ROADMAP-V02.md` nimmt den
LLM-Worker weiterhin ausdrücklich aus v0.2, und der Pilot in `tools/ai_pilot/`
bleibt das Operator-Werkzeug, das die Modellaufrufe macht. Diese Datei beschreibt
nur die Grenze dazwischen.

## Garantien

- **Keine Autorität.** Die Brücke hält keinen Schlüssel, signiert nichts, mintet
  kein Permit, schreibt kein Audit-Ereignis und dispatch keinen Job. Ihr ganzes
  Ergebnis ist ein String und dessen SHA-256-Digest (`AdvisoryResult`). Ein
  Modellvorschlag ist Daten — genau wie ein Request-Body. Keine Instanz, die
  etwas entscheidet, liest ihn.
- **Das Engine wird nicht vertraut.** Alles, was die Engine zurückgibt, prüft
  diese Grenze selbst gegen eigene Grenzwerte (Art, Länge, Kodierung, Leere),
  weil die Prüfungen des Piloten dem Piloten gehören und nicht dem Kern — aus
  demselben Grund validiert das Gateway einen Handoff nach, den der Orchestrator
  signiert hat. Was auch immer die Engine wirft, wird hier **eine** Ablehnung mit
  fester Meldung; ihre eigene Meldung geht nicht durch.
- **Aus heißt aus.** Die Brücke ist ohne Konfiguration immer aus. Aktiv wird sie
  nur über `[ai_advisory] enabled = true` **und** eine explizit übergebene
  Engine; `build_advisory` importiert den Piloten erst dann. Es gibt keine
  Default-Engine, keine Environment-Variable und keinen stillen Modellwechsel.
- **Ablehnen statt degradieren.** Unklare Eingabe, ausgefallene Engine, übergroße
  Antwort oder ein zweiter paralleler Aufrufer enden in einem `ContractError`.
  Kein Ersatzmodell, kein Retry, keine Teilantwort.

## Einschalten

In der Server-Konfiguration (`docs/examples/geniusnew.toml`):

```toml
[ai_advisory]
enabled = true
```

Voraussetzungen auf dem Server:

- `tools/ai_pilot/` muss für den Interpreter des Dienstes importierbar sein;
  sonst verweigert der Start (fail closed), statt still auf „aus" zu fallen.
- Ollama lauscht auf `127.0.0.1:11434` mit genau den beiden gepinnten Modellen
  (`qwen3.5:4b-q4_K_M` als Planer, `qwen2.5-coder:1.5b` als Helfer). Endpunkt,
  Modelle, Grenzwerte und der Protokollvertrag stehen in
  `tools/ai_pilot/README.md`; der Pilot ist unverändert übernommen.

`127.0.0.1:11434` hat keine Authentisierung. Ist der Port frei, kann ein anderer
lokaler Nutzer ihn belegen und Antworten mit einer behaupteten Modell-ID liefern.
Modellausgabe und Modell-ID bleiben deshalb untrusted; die ID beweist weder die
Herkunft noch die Integrität einer Antwort. `enabled = true` bleibt im Betrieb
gesperrt, bis die getrennte Prozessgrenze freigegeben und umgesetzt ist.

Ohne Tabelle startet der Dienst mit einer **ausgeschalteten** Brücke
(`Service.advisory`); niemand muss auf ihr Fehlen prüfen.

## Benutzung (programmatisch)

```python
result = service.advisory.request("summary", "Zwei harmlose Sätze.")
# result.kind, result.text (untrusted), result.text_sha256
```

Jede Ablehnung ist ein `ContractError` aus `geniusnew/contracts.py`. Es gibt
bewusst **keinen HTTP-Weg**: Advisory-Text über den Eingang zu veröffentlichen ist
eine neue Netzfläche mit eigenen Limits und eigenem Review, kein Nebeneffekt
dieses Moduls. Bis dieses Gate existiert, führt der Weg nur über das Objekt.

## Prüfungen

- `tests/test_advisory.py` — die Grenze gegen eine Test-Engine: Kill-Switch,
  geschlossene Arten, Eingabe-/Ausgabegrenzen, Kodierung, Nebenläufigkeit,
  Digest, Ergebnisfläche. Eine Ablehnung, ein Test.
- `tests/test_ai_pilot.py` — der Pilot selbst gegen einen Fake-Ollama.
- `scripts/refusals.py` — `geniusnew/advisory.py` und `tools/ai_pilot/pilot.py`
  stehen in `GUARDED`; jede Ablehnung muss einzeln abgeschaltet die Suite rot
  machen.
- `tests/test_serve.py::AdvisoryWiringTest` — was `serve` aus der Konfiguration
  an `build` übergibt.

## Beziehung zum Piloten und zur Entscheidungslage

Der Pilot bleibt beratend und steht unter `tools/`; `geniusnew/` erhält über
dieses Modul nur den Aufrufweg. Die Entscheidung von Kaan vom 18.10.2026
(`docs/DECISIONS.md`: „Der Pilot bleibt beratend, nur unter `tools/`, ohne
Eingriff in `geniusnew/`") wird durch diese Anbindung **bewusst erweitert**:
Kern und Modell sind jetzt verbunden, aber ohne jede Rechteübertragung. Merge
und Betrieb brauchen weiterhin Kaans ausdrückliches GO; STOP bleibt für
Tailscale-ACL, `tailscale serve`, Firewall, Ollama-Bindung, ai.local als
Netzserver, systemd, Schlüssel und Deployment.
