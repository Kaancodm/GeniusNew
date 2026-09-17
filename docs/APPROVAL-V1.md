# GeniusNew Approval v1

Approval v1 ist die serverseitige Grenze zwischen einem signierten
`PENDING_APPROVAL`-Handoff und einer späteren Worker-Ausführung. Es erzeugt noch
keinen ausführbaren Handoff und ersetzt keine Gateway-Prüfung.

## Scope

`create_scope()` akzeptiert ausschließlich einen aktuell gültigen, signierten
Pending-Handoff. Der Scope ist exakt an folgende Werte gebunden:

- SHA-256 des vollständigen kanonischen Handoffs einschließlich Signatur
- Job, Nutzer, Worker und Risiko-Tier
- Policy-Version
- Aktion `EXECUTE_HANDOFF`

Ein anderer Handoff, Job, Nutzer, Worker, Risiko-Tier oder Policy-Stand kann den
Token nicht einlösen. Der Token-Ablauf wird außerdem auf die Ablaufzeit des
signierten Handoffs begrenzt.

## Token und Zustände

`ApprovalStore.grant()` gibt einen zufälligen Token mit mindestens 32 Byte nur an
den vertrauenswürdigen Approval-Presenter zurück. Im Store liegt ausschließlich
dessen SHA-256-Digest. Ein Token ist höchstens 600 Sekunden gültig.

Der Store kennt die Zustände `GRANTED`, `CONSUMED` und `REVOKED`. Verbrauch und
Widerruf sind pro Store durch einen Lock atomar. Jede Zustandsänderung erzeugt
einen neuen SHA-256-Hash, der den vorherigen Record-Hash bindet. Ein Token kann
nur genau einmal von `GRANTED` nach `CONSUMED` wechseln.

## Persistenzgrenze

`ApprovalStore` ist bewusst nur prozesslokal. Ein Neustart verliert den Zustand
und ist deshalb keine Produktionspersistenz. Ein späterer persistenter Adapter
muss dieselben Scope-, Ablauf- und Einmalverbrauchsregeln atomar erzwingen,
bevor ein Gateway eine Worker-Ausführung zulässt.
