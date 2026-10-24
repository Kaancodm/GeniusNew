# Gemini-Reviews über Antigravity

Seit Kaans Auftrag vom 21.10.2026 laufen Gemini-Reviews über Antigravity (`agy`).
Abacus ist aus dem aktuellen Reviewweg und der Agentenauswahl entfernt.
Historische Reviews behalten ihre ursprüngliche Werkzeug- und Commitbindung.

## Einrichten und anmelden

Die [offizielle Installationsanleitung](https://antigravity.google/docs/cli/install.md)
liefert den nativen Installer. Das Skript zuerst herunterladen und prüfen, danach
ausführen; es prüft den Download gegen die SHA-512-Prüfsumme im Release-Manifest:

```sh
curl -fsSL https://antigravity.google/cli/install.sh -o /tmp/antigravity-install.sh
less /tmp/antigravity-install.sh
bash /tmp/antigravity-install.sh
~/.local/bin/agy --version
```

Im Projektcheckout `agy --mode plan --sandbox` starten und **Google OAuth** wählen.
Das bestehende Google-Konto verwenden. In einer entfernten Sitzung zeigt die CLI
einen Anmeldelink; den einmaligen Bestätigungscode direkt im CLI-Dialog eingeben.
Zugangsdaten gehören weder ins Repository noch in PR-Kommentare oder Testprotokolle.
Kein neues Abo und kein API-Key-Anbieterwechsel sind Teil dieser Einrichtung.

`agy models` prüft anschließend, ob die Sitzung Modelle auflisten kann. Das belegt
weder Kontoberechtigung, Restkontingent noch ein bestandenes Review. Bei einer
Eligibility-Sperre muss der Kontoinhaber die von Google verlangte Prüfung abschließen;
der abrufbare Katalog hebt sie nicht auf. Die CLI-Version und das tatsächlich
verwendete Modell müssen im Review stehen; alte Modellnamen nicht blind übernehmen.

## Einen festgelegten Stand prüfen

1. Sauberen Checkout und vollständigen Head mit `git status --short` und
   `git rev-parse HEAD` bestätigen. Den Diff gegen `main`, `AGENTS.md`, `GEMINI.md`
   und `.gemini/styleguide.md` bereitstellen.
2. Ein verfügbares Gemini-Modell aus `agy models` auswählen. Die Pro-Pflicht bei
   neuen Abhängigkeiten, Sicherheitsgrenzen oder Signaturrollen bleibt bestehen;
   ein ähnlicher Modellname ist kein Nachweis für die Pro-Eignung.
3. Nur lesend im Plan-Modus mit Sandbox prüfen, keine Hilfsagenten, keine
   Schreibbefehle und kein Zugriff außerhalb des Checkouts. Beispiel nach Auswahl
   des tatsächlichen Modells und Formulierung des SHA-gebundenen Auftrags:

   ```sh
   agy --mode plan --sandbox --model "$REVIEW_MODEL" \
     --output-format json -p "$REVIEW_PROMPT"
   ```

4. Ergebnis unverändert im Format „Gemini-Review“ aus
   [`COLLABORATION.md`](COLLABORATION.md#gemini-review-format-seit-04102026) in den
   PR übernehmen. Modell, voller SHA, geprüfte Bereiche und Befunde nennen.
   Danach bestätigen, dass der Checkout unverändert ist.

Ohne Anmeldung oder erfolgreiches Modellreview bleibt das Review BLOCKED.
Eine Installation, Modellliste oder eigene Implementiererprüfung ist kein Ersatz.
Claude-DB-Reviews und die bestehenden Merge-/Betriebsgates bleiben erforderlich.
