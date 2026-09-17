# Roadmap zu v0.1

Dieses Dokument ist ein **Plan, kein Zustandsbericht.** Was tatsächlich gilt, steht in
`main`, in den Tests und in der CI. Wenn dieses Dokument und das Repository sich
widersprechen, hat das Repository recht.

## Ziel von v0.1

> Ein Fremder klont das Repository, führt **einen** Befehl aus, und sieht: ein Job geht
> über HTTP hinein, die Identität wird serverseitig bestimmt, die Policy prüft ihn, er
> läuft isoliert, das Ergebnis kommt signiert zurück, und die Audit-Chain lässt sich
> verifizieren. Die CI beweist denselben Pfad bei jedem Push.

Erst wenn das gilt, ist v0.1 erreicht. Nicht vorher.

v0.1 ist ausdrücklich **keine** Produktionsfreigabe und kein Sicherheitsnachweis für einen
echten Betrieb. Es ist der erste durchgehende, nachvollziehbare Pfad durch alle Schichten.

## Bewusst nicht in v0.1

Diese Entscheidungen stehen bereits in `docs/IMPORT-MANIFEST.md` und werden hier nur
zusammengefasst:

- **Firecracker / microVM** — `REJECT_FOR_BOOTSTRAP`. Erhöht Komplexität und
  Angriffsfläche, ohne die v0.1 nicht nachweisbar wäre. Isolation beginnt auf
  Prozessebene.
- **Datenbank / Persistenz** — `REJECT_FOR_BOOTSTRAP`. Der Kern ist prozesslokal.
  Persistenz ist eine eigene, bewusst zu entwerfende Phase.
- **Portal / Web-UI** — externe Angriffsfläche, kommt nach den Kernverträgen.
- **Semantisches Gedächtnis** — nicht Teil des minimalen Trust-Kerns.

Etwas aus dieser Liste vorzuziehen, ist kein Fortschritt, sondern eine Verbreiterung des
Scopes. Wer es trotzdem tut, nimmt die Entscheidung bewusst zurück und begründet sie.

## Die Schritte

### Teil 1 — landen, was in Arbeit ist

Offene Arbeit, die nicht integriert wird, verfällt. Das ist die Hauptursache der beiden
vorangegangenen Neuaufbauten. Deshalb steht dieser Teil vorn.

1. **#9** — Validator lehnt tief verschachtelte Eingaben ab, statt abzustürzen.
2. **#5** — Contract-CI auf GitHub Actions. Wird nach #9 ohne weitere Änderung grün.
3. **#8** — Migrationsmatrix wird kanonisches Import-Gate; `IMPORT-MANIFEST.md` wird als
   historisch markiert.
4. **#6** — serverseitige Einmal-Approval-Tokens, scope-gebunden. Enthält einen offenen
   Vorschlag zur Herkunftsprüfung von `ApprovalScope`; siehe die Diskussion am PR.
5. **#3** — schließen. Eine Datenbank-Grundlage widerspricht dem Bootstrap-Scope oben.
   Kommt als eigene Phase zurück, wenn sie gebraucht wird.

### Teil 2 — die vertikale Scheibe

Ein Pfad durch alle Schichten, nicht alle Schichten halb fertig.

6. **Audit-Chain** — append-only, hash-verkettet, mit einer `verify()`-Funktion, die eine
   manipulierte Kette erkennt. Tests gegen Einfügen, Löschen und Verändern.
7. **Worker-Schnittstelle** plus ein deterministischer Trivial-Worker als Referenz.
8. **Isolationsgrenze auf Prozessebene** — kein Netz, kein Schreibzugriff außerhalb eines
   temporären Verzeichnisses, Zeitlimit, Ressourcenlimit. Nachweisbar durch Tests, die den
   Ausbruch versuchen.
9. **Orchestrator** — Admission, Policy-Prüfung, Approval, Dispatch, Ergebnisannahme.
   Keine geteilte veränderliche Autorität, deterministische Zuordnung, fail closed.
10. **HTTP-Eingang** — API-Key wird serverseitig auf einen Principal abgebildet.
    Identität, Tier und Rechte kommen **nie** aus dem Request.
11. **Verdrahtung** plus ein End-to-End-Test, der den gesamten Pfad geht und die
    Audit-Chain am Ende verifiziert.

### Teil 3 — nachweisbar machen

Ein Ergebnis, das nur der Autor reproduzieren kann, ist kein Ergebnis.

12. **`scripts/demo.sh`** — ein Befehl. Startet einen Job, druckt die Audit-Chain,
    verifiziert sie und sagt deutlich, ob die Prüfung bestanden wurde.
13. **CI führt den End-to-End-Test mit aus**, nicht nur die Unit-Tests.
14. **README-Quickstart**, den ein Fremder ohne Rückfragen befolgen kann. Am besten von
    jemandem gegengelesen, der das Projekt nicht kennt.
15. **Tag `v0.1`** auf einem grünen, verifizierten Commit.

## Arbeitsregeln

Diese drei Regeln adressieren die beobachteten Ursachen der vorangegangenen Neuaufbauten.
Sie sind keine Stilfragen.

**Klein schneiden, oft mergen.** Ein Pull Request, der älter als etwa zwei Tage wird, ist
eine künftige Abschreibung. Lässt sich etwas heute nicht mergen, ist es zu groß
geschnitten. Das Altprojekt trägt zehn offene Pull Requests auf veralteten Basen — gute
Arbeit, die nicht mehr landen kann.

**Behauptungen gehören in Checks, nicht in Sätze.** Dokumente veralten lautlos, Checks
nicht. Drei Aussagen in der Projektdokumentation waren nachweislich falsch, bis sie
geprüft wurden: eine Aussage über den Paketnamen, eine über die Sichtbarkeit des
Repositories und eine über das gültige Import-Gate. Keine davon war böswillig; alle drei
sind entstanden, weil niemand sie nachrechnen musste.

**Nicht umbenennen.** Jede Identitätsänderung bisher hat einen vollständigen
Migrationszyklus gekostet und strukturell nichts verbessert. Das Projekt heißt GeniusNew.

## Danach

Was nach v0.1 kommt, ist in `docs/MIGRATION-MATRIX.md` unter `REBUILD` klassifiziert und
an einen exakten Quell-SHA gebunden. Diese Roadmap greift dem nicht vor.

Eine Aufgabe ist bereits notiert und gehört in die erste Phase nach v0.1: das bestehende
`schemas/handoff-v1.schema.json` trägt eine `$id` unter einer Domain, die nicht gehalten
wird. Die Entscheidung dazu steht in der Migrationsmatrix — GeniusNew verwendet künftig
`urn:geniusnew:schema:<name>:v1`.
