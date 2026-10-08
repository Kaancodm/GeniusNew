# Autonomer Entwicklungsworkflow
Stand: 08.10.2026. Auftrag von Kaan.

## Arbeitsbefugnis
Ein erteilter Entwicklungsauftrag gilt bis zum überprüften Ergebnis.
Lesen, Diagnose, Worktrees, reversible Code-/Dokuänderungen, lokale Werkzeuge,
Tests, Commits und Draft-PRs auf Arbeitsbranches laufen ohne wiederholte GO-Fragen.
Die zuständige Rolle darf innerhalb des Auftrags selbst umsetzen; die Rollenmatrix
beschreibt die bevorzugte Arbeitsteilung und verursacht keine zusätzliche Wartefreigabe.
Ein Implementierer pro Branch; keine fremden Änderungen überschreiben.

## Weiterarbeiten
Normale Unklarheiten mit einer knappen Annahme lösen. Bei nicht sicherheitsrelevanten
Toolfehlern Ursache protokollieren, begrenzt wiederholen oder einen geeigneten Ersatz
nutzen. Vor Modellaufträgen einen Ersatzweg mit eigenem Kontingent festhalten;
Zugänge und Qualität des Ersatzes müssen zur Aufgabe passen.
Die lokale KI ist für kleine Texte und Entwürfe geeignet. Sie ersetzt keine
unabhängige Security-/DB-Prüfung. Zwei Gemini-Konten sind getrennte Profile;
Kontingente werden weder zusammengelegt noch durch Tokenweitergabe umgangen.

## Konkrete Freigaben
SSH, Firewall, Zugriffsrechte, Secrets, produktive Datenbanken, Produktionsdeployment,
öffentliche Veröffentlichung/Release, main-Merge und irreversible Löschungen brauchen
eine konkrete Freigabe für Ziel, Änderung und Rückweg. Das Setup ist keine pauschale
Freigabe für diese Aktionen. Gebührenpflichtige neue APIs brauchen ein bestätigtes Budget.

## Nachweis
Passende Tests und bestehende CI-/Review-Gates ausführen und den vollen SHA nennen.
Fehlgeschlagene oder ungeprüfte Schritte bleiben sichtbar; nicht als PASS ausgeben.
Keine Sicherheitsentscheidung aus unklarer Identität oder Berechtigung ableiten.
Das betrifft Entwicklungswerkzeuge und Freigabereibung. Der Runtime-Code wird weiterhin
nach seinen tatsächlichen Verträgen geprüft. PR #134 ist ein offener Regelentwurf,
der dem aktuellen PR-Cleanup-Auftrag vom 08.10.2026 widerspricht und keine
Arbeitsbefugnis erteilt. Zero-Trust, Fail-Closed und der bestehende Testschutz bleiben
vollständig erhalten; ein Architekturumstieg wird nicht begonnen.

## Cloud-Automation
Gumloop koordiniert, der Server führt Entwicklungsarbeit aus. Cloud-Dienste sind keine
Tailscale-Mitglieder allein durch ein Abo. Zunächst GitHub-/Connector-basierte Übergaben;
kein öffentlicher Shell-WebHook. HARPA arbeitet in einem verbundenen Desktop-Browser.
Produktive Aktionen und externe Nachrichten benötigen den jeweiligen konkreten Auftrag.
