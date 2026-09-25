# Quickstart-Review (Roadmap Schritt 20)

**Status: bestanden und als Abschluss von Schritt 20 akzeptiert.** Der Nachweis gilt
für Commit `d3378daf97af80e5bcd4f97b163b5bed95253329` vom 25.09.2026.

## Abnahmekriterien

Der Projektverantwortliche hat am 25.09.2026 den ausgeführten technischen Review als
Abnahme akzeptiert und die zusätzliche Bedingung einer realen Person ohne vorherige
Projektkenntnis aufgehoben. Für diese Abnahme reichen der frische Clone und eine
eigene virtuelle Umgebung in der vorhandenen WSL-Installation; eine neu installierte
VM oder WSL-Distribution ist keine Voraussetzung.

Der Review muss weiterhin belegen:

- Die dokumentierten Schritte für Clone, venv, Installation und Demo funktionieren.
- Die Demo endet mit Exit-Code `0` und exakt der letzten Zeile aus der README.
- Unklarheiten, benötigte Rückfragen, Hilfsmittel und Abweichungen werden festgehalten.
- Commit, Umgebung und Ergebnis sind nachvollziehbar dokumentiert.

## Durchlauf vom 25.09.2026

| Punkt | Beobachtung |
| --- | --- |
| Prüfer | Codex; Projektkontext war bereits vorhanden |
| Betriebssystem | Ubuntu 26.04 LTS unter WSL2, x86_64; bestehende Installation |
| Python / Git | Python 3.14.4 / Git 2.53.0 |
| Ziel | Frischer Clone von `https://github.com/Kaancodm/GeniusNew.git` |
| Geprüfter Commit | `d3378daf97af80e5bcd4f97b163b5bed95253329` |
| Vorbereitung | Separates temporäres Verzeichnis, isoliertes Home, neue `.venv` |
| Clone / venv / Installation / Demo | Alle vier Schritte mit Exit-Code `0` |
| Installierte Pakete | `cryptography==50.0.1`, `cffi==2.1.1`, `pycparser==3.0`; Hash-Prüfung erfolgreich |
| Dauer | Etwa 7 Sekunden vom Klonen bis zum Ende der Demo |
| STDERR | Installation und Demo leer |
| Rückfragen / Unklarheiten | Keine beim vollständigen Durchlauf |
| Arbeitsbaum danach | `git status --short --untracked-files=all` leer; `.venv` ist ignoriert |
| Installationsartefakte | `.venv` im Clone und pip-Cache im isolierten Home bleiben vorhanden |

Die ausgeführten README-Schritte:

```sh
git clone https://github.com/Kaancodm/GeniusNew.git
cd GeniusNew
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install --require-hashes -r requirements.txt
./scripts/demo.sh
```

Zur Messung und Protokollierung wurden zusätzlich die Ausgaben getrennt erfasst,
die Exit-Codes unmittelbar gesichert und die Demo mit einem nicht erreichten
120-Sekunden-Timeout ausgeführt. Bei pip wurde der optionale Versionscheck mit
`--disable-pip-version-check` deaktiviert; `--require-hashes` blieb aktiv.

Die letzte Zeile der Demo lautete wörtlich:

```text
PASS — job succeeded over HTTP, chain verified against the anchored head, 13/13 attacks refused.
```

Ein vorausgehender Lauf hatte nur die Demo mit der vorhandenen Systemabhängigkeit
ausgeführt. Nach dem zwischenzeitlichen Merge der Ed25519-Änderung wurde die aktuelle
README erneut gelesen und der vollständige Quickstart in einem zweiten frischen Clone
mit neuer venv durchgeführt. Nur dieser vollständige Durchlauf begründet die Abnahme.

## Bedeutung und Gültigkeit

`PASS` belegt hier den demonstrierten HTTP-Pfad, die Prüfung der Audit-Chain gegen den
festgehaltenen Kopf und die Ablehnung der 13 konkreten Manipulationsversuche. Der
Prüfer verstand die im README genannten Grenzen; der Review ist keine
Produktionsfreigabe und kein Nachweis für die Verständlichkeit bei projektfremden
Menschen. Die bekannten Sicherheitsgrenzen aus `../SECURITY.md` bleiben bestehen.

Der Nachweis umfasst weder natives Windows noch macOS noch eine neue Betriebssystem-
Installation. Auch ausgehender Netzwerkverkehr wurde nicht separat überwacht.

Änderungen an den Quickstart-Befehlen, ihren Voraussetzungen, dem Demo-Pfad oder seiner
erwarteten Ausgabe benötigen einen erneuten Durchlauf auf dem betreffenden Commit.
Eine neue Demo mit anderer Angriffszahl ist nicht durch dieses Protokoll abgedeckt.
CI und der Release-Tag aus Schritt 21 werden getrennt geprüft.
