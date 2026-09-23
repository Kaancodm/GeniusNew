# autoresearch — Anweisungen für einen Agenten

Dies ist die Datei, die einem Coding-Agenten gegeben wird, der die Schleife aus
`scripts/autoresearch.py` fährt. Das Muster stammt aus
[karpathy/autoresearch](https://github.com/karpathy/autoresearch) (MIT): genau eine Datei
ändern, fest messen, behalten oder verwerfen, wiederholen.

Das ist Entwicklungs-Werkzeug, kein Teil des Produkts. Laufen soll es erst nach dem Tag
`v0.1`.

## Ziel

Die Testsuite schneller machen, ohne sie schwächer zu machen. Gemessen wird der Median
der Laufzeit aus drei Läufen. Das zählt, weil `scripts/refusals.py` die Suite einmal pro
Ablehnung ausführt: Jede gesparte Sekunde spart in der CI Minuten.

## Regeln

1. Du änderst **nur** die Zieldatei, die beim Start genannt wurde. Jede andere Änderung
   macht das Skript rückgängig, ohne zu messen.
2. Nach jeder Idee: `python3 scripts/autoresearch.py step --note "<was du probiert hast>"`.
   Das Skript entscheidet `keep`, `discard` oder `crash`, nicht du.
3. Ein Tor ist nie das Problem. Weniger Tests, weniger bemerkte Ablehnungen oder weniger
   abgewehrte Angriffe gelten nicht als Beschleunigung und werden verworfen. Versuche nicht,
   ein Tor zu umgehen; wenn eine Idee nur so funktioniert, ist sie verworfen.
4. Kein Test darf schlafen, und keine Zusicherung darf schwächer werden, damit er schneller
   läuft. Eine Zusicherung, die „irgendein Fehler“ statt „dieser Fehler“ prüft, ist schwächer.
5. `python3 scripts/autoresearch.py status` zeigt das Protokoll. Lies es, bevor du eine Idee
   wiederholst.
6. Nichts pushen, nichts mergen. Behaltene Commits bleiben auf `autoresearch/<tag>`; ein
   Mensch wählt aus, was per Pull Request eingeht.

## Rechte des Agenten

Mit so wenig Rechten starten, wie die Schleife braucht:

- Lesen: das ganze Repository.
- Schreiben: nur die Zieldatei.
- Ausführen: nur `python3 scripts/autoresearch.py *`.
- Kein Netz, kein `git push`, keine Änderung an Einstellungen oder Hooks.

Das Skript prüft den Scope selbst. Die Rechte sind die zweite Linie, nicht die erste.

## Start

```sh
python3 scripts/autoresearch.py start --target tests/test_demo.py --tag demo-speed
```

`start` verlangt einen sauberen Arbeitsbaum, legt den lokalen Branch
`autoresearch/<tag>` an und misst die Baseline. Ist sie nicht grün, startet nichts.
Das Protokoll steht in `autoresearch/<tag>.tsv` (von Git ignoriert).

## Mit dem Claude-Code-Skill uditgoenka/autoresearch

[uditgoenka/autoresearch](https://github.com/uditgoenka/autoresearch) (MIT) ist ein
Skill für Claude Code, der dieselbe Schleife von der Agenten-Seite fährt: Er committet
jede Idee, misst mit einem Verify-Befehl und macht per `git revert` rückgängig. Er wird
**lokal** installiert, nicht in dieses Repository. Das ist fremder Code, der in deinem
Claude Code läuft; lies ihn vor der Installation.

Dann übernimmt der Skill Keep und Discard, und die Tore bleiben hier: `verify` gibt genau
eine Zahl aus (Sekunden, niedriger ist besser) und endet mit Exit-Code ≠ 0, sobald etwas
außerhalb der Zieldatei geändert wurde (committet oder nicht), ein Tor rot ist oder
weniger geprüft wird als bei der Baseline. `step` wird in diesem Modus nicht benutzt.

```sh
python3 scripts/autoresearch.py start --target tests/test_demo.py --tag demo-speed
```

```
/autoresearch
Goal: Die Testsuite schneller machen, ohne Prüfungen zu verlieren
Scope: tests/test_demo.py
Metric: Laufzeit der Suite in Sekunden (niedriger ist besser)
Verify: python3 scripts/autoresearch.py verify
Iterations: 25
```

Die Regeln oben gelten unverändert, vor allem: nichts pushen.
