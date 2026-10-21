# Laya Shadow Mode

Status: **Experiment, keine Autorität.**

Ziel ist zu messen, ob ein lokaler Jev-kompatibler Decision-Service bei der Auswahl
zwischen `codex`, `gemini` und `antigravity` nützlich ist. Die bestehende
GeniusNew-Policy, Gateway-Prüfung, Approvals, Signaturen und Audit-Entscheidungen werden
dadurch nicht verändert.

## Verfügbarkeit (Kaans Meldung vom 20.10.2026)

| Werkzeug | Stand | Auswahl im Shadow-Test |
| --- | --- | --- |
| Codex über ChatGPT Pro | Kontingent mit 100 % gemeldet | ja |
| Gemini Pro | Ein Pro-Konto gemeldet; Restkontingent vor Modellauftrag prüfen | ja, nur bei verfügbarem Kontingent |
| Google Antigravity (`agy`) | CLI, Modellliste und kurzer Argon-Low-Auftrag am 20.10. geprüft; Restkontingent ungeprüft | ja, solange der Modellzugang funktioniert |
| Claude Pro | Kontingent-Reset etwa 40 Minuten nach der Meldung erwartet; danach neu prüfen | noch nicht |
| GitHub Copilot, Kiro | Laut Kaan für zwei Wochen nicht verwendbar; Wiedervorlage am 03.11.2026 | nein |

Abacus ist seit Kaans Auftrag vom 21.10.2026 aus der aktuellen Werkzeugauswahl
entfernt und bleibt im Shadow-Test abgelehnt.

Die Wiedervorlage aktiviert kein Werkzeug automatisch. Eine neue Nutzbarkeitsprüfung
und eine Listenänderung sind nötig. Die Pause hebt keine Review-Pflichten aus
`docs/COLLABORATION.md` auf. Antigravity ist ein eigener Google-Agentenclient;
seine Modellliste beweist kein zusätzliches Kontingent. Der einzelne erfolgreiche
Argon-Auftrag belegt kein verbleibendes Kontingent für weitere Aufgaben.

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
result = observe("review gateway boundary", "codex", decide)
print(result.as_dict())
```

Der tatsächliche Laya-Dienst und seine Modellgewichte gehören auf den Server bzw. in
dessen private Laufzeitumgebung, nicht in dieses öffentliche Repository.
