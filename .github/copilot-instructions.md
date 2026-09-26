# Copilot-Anweisungen für GeniusNew

Vollständige Regeln: `AGENTS.md` im Repository-Wurzelverzeichnis. Projektstand:
`docs/STATUS.md`. Das Wichtigste:

- Zero-Trust-Agentensystem in Python 3.11+, eine hash-gepinnte Abhängigkeit
  (`cryptography`). Einrichten: `python3 -m pip install --require-hashes -r requirements.txt`.
- Prüfen: `python3 -m unittest discover -s tests`, `./scripts/demo.sh`,
  `python3 scripts/refusals.py geniusnew/<modul>.py`.
- Fail closed: jede unklare Eingabe wird mit `ContractError` abgelehnt.
- Jede Ablehnung braucht einen Test, der ihr Fehlen bemerkt (Refusal-Guard).
- Nur signierende Rollen halten private Ed25519-Schlüssel; prüfende Instanzen bekommen
  die öffentliche Hälfte und lehnen die private ab.
- Keine Secrets, keine übersprungenen Tests, keine neuen Abhängigkeiten ohne Entscheidung.
- Doku Deutsch, Code und Kommentare Englisch.

## Beim Code-Review besonders prüfen

1. Gibt es einen Pfad, der nicht fail closed ist (stilles `return`, breites `except`,
   Default statt Ablehnung)?
2. Hält eine prüfende Instanz plötzlich einen privaten Schlüssel oder ein Root-Secret?
3. Hat jede neue Ablehnung einen Test, und steht ein neues Modul in `GUARDED`?
4. Ändert der PR eine Grenze aus `SECURITY.md`, ohne den offen haltenden Test und die
   Tabelle anzupassen?
5. Landen Nutzdaten oder Secrets in Audit-Einträgen, Logs oder Fehlermeldungen?
