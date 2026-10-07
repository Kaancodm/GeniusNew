# Laya Shadow Mode

Status: **Experiment, keine Autorität.**

Ziel ist zu messen, ob ein lokaler Jev-kompatibler Decision-Service bei der Auswahl
zwischen `kiro`, `copilot`, `gemini` und `abacus` nützlich ist. Die bestehende
GeniusNew-Policy, Gateway-Prüfung, Approvals, Signaturen und Audit-Entscheidungen werden
dadurch nicht verändert.

## Sicherheitsgrenze

- Der Decision-Service darf nur eine **Beobachtung** liefern.
- Die autoritative Auswahl kommt vom bestehenden Aufrufer und wird niemals durch den
  Modellwert überschrieben.
- Fehler, ungültige Antworten oder ein nicht erreichbarer Dienst ergeben `UNKNOWN`.
- Der mitgelieferte HTTP-Adapter akzeptiert ausschließlich Loopback-Endpunkte
  (`127.0.0.1` oder `::1`). Hostnamen und URL-Userinfo werden abgelehnt.
- HTTP-Redirects sind verboten; Proxy-Umgebungsvariablen werden für den Shadow-Adapter
  nicht verwendet, damit der Request den Loopback-Host nicht verlassen kann.
- Keine API-Keys, Tokens, Nutzdaten oder produktiven Jobs in diesem Experiment.
- Keine Änderung an `requirements.txt`: der Adapter verwendet nur die
  Python-Standardbibliothek.

## Messung

Für jeden Testfall werden nur diese Werte benötigt:

- autoritative Agent-Auswahl,
- Modell-Auswahl,
- Match ja/nein oder `UNKNOWN`.

Erst nach einer getrennten Auswertung von Trefferquote, Latenz und Fehlerquote darf
über aktives Routing entschieden werden. Ein aktives Routing wäre eine eigene
Architekturentscheidung und ist **nicht** Teil dieses Experiments.

## Lokaler Anschluss

Der Adapter erwartet einen Jev-kompatiblen HTTP-Endpunkt auf Loopback und sendet
`state` plus eine typisierte `choice`-Frage unter `questions.agent`. Aus der
Antwort wird ausschließlich `answers.agent.choice` gelesen. Beispiel:

```python
from experiments.laya_shadow import http_decider, observe

decide = http_decider("http://127.0.0.1:8000/v1/systemone")
result = observe("review gateway boundary", "kiro", decide)
print(result.as_dict())
```

Der tatsächliche Laya-Dienst und seine Modellgewichte gehören auf den Server bzw. in
dessen private Laufzeitumgebung, nicht in dieses Repository.
