# Harness² als lokales Experiment

[Harness²](https://github.com/google-research/harness2) ist ein Forschungsprojekt
von Google Research zur Verbesserung eines Agenten-Harnesses. Laut eigener README
ist es kein offiziell unterstütztes Google-Produkt. Es ist ein Verfahren um
Agenten herum und kein zusätzlicher Agent für die Laya-Auswahlliste.

Der Stand für dieses Experiment ist auf Commit
`1cfe254e8d25b15f37048c3730134686abe60335` festgelegt. Das Werkzeug unter
`tools/harness2_experiment.py` verlangt einen lokalen Checkout mit genau diesem
HEAD und der offiziellen `origin`-URL. Die Demo liest die Paketdateien aus dem
Git-Objekt dieses Commits. Änderungen und unversionierte Dateien im Checkout
gelangen so nicht in die Ausführung.

## Lokale Prüfung

Außerhalb des GeniusNew-Checkouts:

```sh
git clone https://github.com/google-research/harness2.git /tmp/harness2-source
git -C /tmp/harness2-source checkout --detach 1cfe254e8d25b15f37048c3730134686abe60335
```

Im GeniusNew-Checkout:

```sh
python3 -m tools.harness2_experiment inspect --source /tmp/harness2-source
python3 -m tools.harness2_experiment demo --source /tmp/harness2-source
```

`inspect` prüft Quelle und Commit ohne den fremden Code auszuführen. `demo`
führt beide Rekursionsmodi mit dem deterministischen Upstream-Beispiel aus:
zwei Aufgaben, keine Modellaufrufe. Das Paket wird nicht installiert, und die
Ausgaben liegen nur während des Laufs in einem temporären Verzeichnis. Gedruckt
werden Commit, Anzahl erledigter/fehlgeschlagener Aufgaben sowie Ausführungen
und Editor-Aufrufe. Für die Demo ist Python 3.12 oder 3.13 nötig.

Der Unterprozess bekommt ein leeres Test-Home und keine geerbten Modell- oder
Cloud-Umgebungsvariablen. Er ist dadurch **nicht** durch einen Betriebssystem-
Sandbox- oder Netzfilter isoliert; die Ausführung bleibt eine bewusste lokale
Aktion. Benchmark-Setup, Modellzugänge, autonome Codeänderungen und Übernahme
eines vorgeschlagenen Harnesses in GeniusNew sind nicht Teil dieses Experiments.
Insbesondere werden `AGENTS.md`, Sicherheitsgrenzen, Tests und Review-Gates
durch Harness² nicht verändert.

Für Modell-Benchmarks nennt der Upstream zusätzliche Laufzeiten,
Benchmark-Daten, Vertex-AI-Zugang und potenzielle API-Kosten. Der Codex-Executor
des Upstreams deaktiviert seine Sandbox. Ein solcher Lauf braucht deshalb einen
eigenen Plan und eine getrennte Umgebung; `tools/harness2_experiment.py` bietet
ihn bewusst nicht an.
