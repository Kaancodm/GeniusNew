# Harness² als lokales Experiment

[Harness²](https://github.com/google-research/harness2) ist ein Forschungsprojekt
von Google Research zur Verbesserung eines Agenten-Harnesses. Laut eigener README
ist es kein offiziell unterstütztes Google-Produkt. Es ist ein Verfahren um
Agenten herum und kein zusätzlicher Agent für die Laya-Auswahlliste.

Der Stand für dieses Experiment ist auf Commit
`1cfe254e8d25b15f37048c3730134686abe60335` festgelegt. Das Werkzeug unter
`tools/harness2_experiment.py` verlangt einen lokalen Checkout mit genau diesem
HEAD und der offiziellen `origin`-URL. Die Demo liest die Paketdateien aus dem
Git-Baum und den rohen Blob-Objekten dieses Commits. `git archive` wird nicht
verwendet, da lokale Git-Attribute dessen Ausgabe verändern können. Änderungen,
lokale Filter und unversionierte Dateien im Checkout gelangen so nicht in die
Ausführung. Fehlende Objekte werden nicht über einen Netzabruf ergänzt.

## Lokale Prüfung

Außerhalb des GeniusNew-Checkouts:

```sh
HARNESS2_CHECKOUT="$(mktemp -d -t harness2-source-XXXXXX)"
git clone https://github.com/google-research/harness2.git "$HARNESS2_CHECKOUT"
git -C "$HARNESS2_CHECKOUT" checkout --detach 1cfe254e8d25b15f37048c3730134686abe60335
```

Im GeniusNew-Checkout, mit Python 3.12 oder 3.13 und installiertem `bubblewrap`:

```sh
python3 -m tools.harness2_experiment inspect --source "$HARNESS2_CHECKOUT"
python3 -m tools.harness2_experiment demo --source "$HARNESS2_CHECKOUT"
```

`inspect` prüft Quelle und Commit ohne den fremden Code auszuführen. `demo`
führt beide Rekursionsmodi mit dem deterministischen Upstream-Beispiel aus:
zwei Aufgaben, keine Modellaufrufe. Das Paket wird nicht installiert, und die
Ausgaben liegen nur während des Laufs in einem temporären Verzeichnis. Gedruckt
werden Commit, Anzahl erledigter/fehlgeschlagener Aufgaben sowie Ausführungen
und Editor-Aufrufe.

`demo` startet nur unter `bubblewrap` mit getrennten Benutzer-, Prozess- und
Netz-Namensräumen. Sichtbar sind ein leeres, schreibgeschütztes Wurzelverzeichnis,
die System-Python-Laufzeit unter `/usr` (nur lesend), das gepinnte Paket (nur
lesend) und ein temporäres Ausgabeverzeichnis (schreibbar). Host-Home, `/etc`,
`/proc` und `/dev` werden nicht eingebunden; Modell- oder Cloud-Variablen werden
nicht geerbt. Fehlt `bubblewrap`, kann der Host keine Namensräume anlegen oder
liegt der Python-Interpreter außerhalb der eingebundenen Laufzeit, verweigert
das Werkzeug die Demo. `inspect` bleibt als reine Metadatenprüfung möglich.

Benchmark-Setup, Modellzugänge, autonome Codeänderungen und Übernahme eines
vorgeschlagenen Harnesses in GeniusNew sind nicht Teil dieses Experiments.
Insbesondere werden `AGENTS.md`, Sicherheitsgrenzen, Tests und Review-Gates
durch Harness² nicht verändert. Die CI prüft die Runner-Logik mit Python 3.11;
sie führt die Harness²-Demo ohne passende Laufzeit und Sandbox nicht aus.

Für Modell-Benchmarks nennt der Upstream zusätzliche Laufzeiten,
Benchmark-Daten, Vertex-AI-Zugang und potenzielle API-Kosten. Der Codex-Executor
des Upstreams deaktiviert seine Sandbox. Ein solcher Lauf braucht deshalb einen
eigenen Plan und eine getrennte Umgebung; `tools/harness2_experiment.py` bietet
ihn bewusst nicht an.
