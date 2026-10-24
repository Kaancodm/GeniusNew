# Bugbot — GeniusNew

Bugbot ist eine **freiwillige Zusatzprüfung**. Sie ersetzt kein Gemini-Review, kein
`Claude DB Review` und kein Kiro-Review (`docs/COLLABORATION.md`). Antworte auf Deutsch.

Prüfe vorrangig, mit Datei:Zeile als Beleg:

1. **Secrets:** Schlüssel, Tokens, `.env`, Hostnamen, private Adressen (Repo ist öffentlich).
2. **Fail closed:** Ein Fehler- oder Unbekannt-Pfad, der still weiterläuft oder auf einen
   Default fällt, statt `ContractError` zu werfen.
3. **Tests:** Neue Ablehnung ohne Test; übersprungene, abgeschwächte oder gelöschte Tests;
   neues Modul mit Ablehnungen fehlt in `GUARDED` (`scripts/refusals.py`).
4. **Signaturrollen:** Ein prüfendes Objekt (`*Verifier`) bekommt einen privaten Schlüssel.
5. **Grenzen:** Ein offen gehaltener Test (`…_and_this_is_the_boundary`) geändert, ohne
   `SECURITY.md` anzupassen.
6. **Abhängigkeiten:** Änderung an `requirements*.txt` → Hinweis „braucht Kaans OK“.
7. **Portal:** Browser-Eingaben bestimmen Identität, Rechte, Tier oder Job-Kennung.

Schweregrade wie `.gemini/styleguide.md` (Critical, High, Medium, Low). Keine Stilkritik.
