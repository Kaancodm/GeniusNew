# ADR-002: Persistenz des Audit-Ankers

- Status: **Offen** — nicht entschieden
- Datum: 2026-09-25
- Entscheider: Kaan
- Bezug: `docs/ROADMAP-V01.md` Schritt 8, `SECURITY.md` („Bekannte Grenzen“),
  `geniusnew/anchor_process.py`

Dieses Dokument hält eine Entscheidung fest, die **noch nicht getroffen** ist. Es trennt
sie bewusst vom Lebenszyklus des Ankers, der bereits umgesetzt ist, damit das eine nicht
nebenbei im anderen entschieden wird.

## Kontext

Der Audit-Anker hält den zuletzt festgelegten Kettenkopf (Anzahl und Hash) und lehnt jede
Kette ab, die diesen Kopf nicht fortsetzt. Damit fällt eine gekürzte und neu signierte
Kette auf, die in sich gültig wäre.

Seit diesem Stand gilt:

- Der Anker läuft in eigenem Prozess. Der Schreiber erreicht seinen Speicher nicht.
- **Start und Stopp** liegen bei einem eigenen Pfad (`anchor_process.start` →
  `AnchorHandle.stop`). Der Dienst bekommt nur einen `AnchorClient` mit dem Socket-Pfad.
  Ein Neustart des Dienstes setzt den Anker nicht mehr zurück; die Demo belegt das mit
  dem Angriff „Restart the service and re-sign the chain“.
- Der Anker hält seine Festlegungen **nur im Speicher**. Ein Neustart des *Ankers* ist
  weiterhin ein Zurücksetzen. `tests/test_anchor_process.py::test_a_restarted_anchor_remembers_nothing_and_this_is_the_boundary`
  hält das offen.

Eine Folge, die vor jeder Persistenz-Entscheidung bekannt sein muss: Die Audit-Chain
selbst ist ebenfalls nur im Speicher (`AuditChain`). Überlebt der Anker einen Neustart des
Dienstes, die Kette aber nicht, dann lehnt der Anker jede neue Kette des neugestarteten
Dienstes ab — sie setzt die festgelegte nicht fort. Das ist fail closed und richtig: das
Log ist verloren, und der Anker sagt es. Es heißt aber, dass Anker-Persistenz ohne
Ketten-Persistenz nur verschiebt, *wer* den Verlust bemerkt, nicht, dass nichts verloren
geht. Beide gehören in dieselbe Entscheidung.

## Zu entscheiden

1. **Ob** der Anker vor v0.1 persistiert wird, oder ob „Anker-Neustart = Zurücksetzen,
   bewusst durch den Betreiber“ für v0.1 reicht.
2. **Was** persistiert wird: nur `(count, head_hash)` oder zusätzlich der signierte Kopf.
3. **Wohin**, und unter wessen Kontrolle — der Speicherort muss außerhalb der Reichweite
   des Schreibers liegen, sonst ist er ein Anker im Speicher mit Umweg über die Platte.
4. **Wie** mit der Kette zusammen: ob die Audit-Chain im selben Schritt persistiert wird.

## Optionen

| Option | Was sie schließt | Was offen bleibt | Aufwand |
| --- | --- | --- | --- |
| A. Keine Persistenz in v0.1 (heutiger Stand) | nichts Neues | Anker-Neustart vergisst; nur der Betreiber-Pfad kann ihn auslösen | keiner |
| B. Append-only-Datei des Ankers, `fsync` je Festlegung, eigener Nutzer | Anker-Neustart | Wer Nutzer und Platte kontrolliert, kann die Datei kürzen; Kette selbst weiter flüchtig | klein |
| C. Wie B, plus Persistenz der Audit-Chain | Neustart von Dienst und Anker ohne Verlust | Datenbank/Persistenz ist laut Roadmap bewusst nicht in v0.1 | mittel |
| D. Externer Zeuge (zweiter Anker auf anderem Host, Transparenz-Log) | Kürzen durch einen einzelnen Host | Deployment und Vertrauen in den Zeugen | groß |

## Kriterien für die Entscheidung

- Die Roadmap schließt Datenbank und Persistenz aus v0.1 aus („eigene, bewusst zu
  entwerfende Phase“). Eine Option, die das berührt, braucht eine ausdrückliche Rücknahme
  dieses Ausschlusses mit Begründung.
- Ein persistierter Anker darf nicht leichter zurückzusetzen sein als der heutige: Eine
  Datei, die der Dienst-Nutzer schreiben kann, ist schwächer als ein Prozess, den er nur
  beenden kann.
- Wer die Grenze schließt, passt `SECURITY.md` an und dreht den offen gehaltenen Test um —
  im selben Pull Request.

## Nächster Schritt

Entscheidung durch den Entscheider oben. Bis dahin gilt Option A, und dieses Dokument
bleibt auf **Offen**.
