# Quickstart-Gegenlesen (Roadmap Schritt 20)

Schritt 20 verlangt, dass jemand **ohne Projektkenntnis** den Quickstart in `README.md`
befolgt — ohne Rückfragen. Dieses Dokument ist das Protokoll dafür. Es ersetzt die
Person nicht; es sorgt dafür, dass ihr Durchlauf etwas belegt.

**Status: offen.** Gegenlesende Person: _noch nicht benannt._

## Wer

Jemand, der dieses Repository, seine Pull Requests und seine Dokumente noch nicht
gesehen hat. Programmierkenntnisse sind nicht nötig, eine Kommandozeile bedienen schon.
Wer am Projekt mitgearbeitet hat, zählt nicht — auch nicht, wer nur mitgelesen hat.

## Umgebung

Eine von beiden, frisch:

- **Linux:** eine neue VM oder ein neuer Container mit einem aktuellen Ubuntu, in dem
  dieses Repository noch nie geklont wurde.
- **Windows mit WSL:** eine WSL-Distribution, die für den Test neu installiert wurde
  (`wsl --install`, oder `wsl --unregister` einer alten Test-Distribution vorher).

Nicht auf dem Rechner eines Projektbeteiligten, nicht in einem Verzeichnis, in dem schon
etwas liegt.

## Ablauf

1. Die Person bekommt **nur** den Link auf das Repository und den Satz:
   „Befolge den Quickstart in der README. Frag nichts; schreib auf, wo du hängst.“
2. Sie liest ab `## Quickstart` (bzw. `### Windows: über WSL`) und tut, was dort steht.
3. Niemand hilft. Eine Frage, die sie stellen würde, wird notiert, nicht beantwortet.
4. Sie hört auf, wenn die Demo endet oder es nicht weitergeht.

## Was aufgeschrieben wird

| Punkt | Eintrag |
| --- | --- |
| Datum, Person (Name oder Kürzel) | |
| Umgebung (Distribution und Version, `python3 --version`) | |
| Commit (`git rev-parse HEAD`) | |
| Dauer vom Klonen bis zur letzten Zeile | |
| Letzte Zeile der Ausgabe, wörtlich | |
| Exit-Code (`echo $?` direkt danach) | |
| Jede Stelle, an der gezögert, geraten oder nachgeschlagen wurde | |
| Jede Frage, die gestellt worden wäre | |
| Was `PASS` nach Verständnis der Person bedeutet — in eigenen Worten | |

Die letzte Zeile zählt: Wer `PASS` für eine Produktionsfreigabe hält, hat den Abschnitt
„Was `PASS` nicht bedeutet“ nicht verstanden, und das ist ein Befund über die README,
nicht über die Person.

## Wann Schritt 20 erledigt ist

- Letzte Zeile gleich der in der README, Exit-Code `0`, **und**
- keine Stelle, an der es ohne Raten nicht weiterging.

Jeder andere Befund wird als Änderung an der README behoben, und der Durchlauf wird mit
einer **neuen** Person wiederholt — wer den Quickstart einmal gesehen hat, ist kein
Fremder mehr.

Das ausgefüllte Protokoll kommt als Kommentar in den Pull Request, der Schritt 20 in
`docs/ROADMAP-V01.md` auf erledigt setzt.

## Was die CI davon schon abnimmt

Ein Teil dessen, was ein Fremder erlebt, ist prüfbar und wird bei jedem Push geprüft
(`.github/workflows/verify.yml`): die Demo läuft aus einer frischen Kopie der
eingecheckten Dateien mit leerer Umgebung, und ihre letzte Zeile muss wörtlich in der
README stehen; unter Windows muss sie mit einer `FAIL`-Zeile enden, die auf WSL verweist.
Ob die README *verständlich* ist, prüft keine CI. Dafür ist dieser Schritt da.
