# Genius Control Deck

Das Control Deck ist die server-first Arbeits- und Operationsoberfläche für GeniusNew.
Es gehört nicht zur Zero-Trust-Runtime und erhält keine Autorität über Gateway, Worker,
Datenbank oder Audit-Anker. Schreibende Funktionen sind auf eine feste, getestete
Allowlist ungefährlicher Prüfaktionen begrenzt; freie Shell-Ausführung gibt es nicht.

## Start

Vom Repository-Root:

```bash
python3 -m tools.control_deck --host 127.0.0.1 --port 8787
```

Ohne explizite Konfiguration verwendet das Deck den Checkout, aus dem sein eigenes
Modul geladen wurde; ein bestimmter Benutzerpfad oder das aktuelle Arbeitsverzeichnis
ist dafür nicht erforderlich. `GENIUSNEW_REPO` überschreibt diesen Standard beim
Start, `--repo` hat Vorrang vor beiden. Ein ausdrücklich gewählter fehlender Checkout
bleibt als `repo missing` sichtbar; es gibt keinen stillen Wechsel auf den Standard.

Das Dashboard zeigt Git, Tailscale, Desktop Commander, Docker, tmux, Agent-Logins und
die 21 Beta-Gates. Oben stehen Projektstand/Wiedereinstieg, die drei nächsten Schritte
sowie Research- und Ideenbewertungen. Ein read-only Mail Center zeigt einen kompakten
Alarmstatus für Verkauf, Sponsoring, Allgemein und System. TERM-Befehle werden
kopiert oder zur manuellen Übernahme angezeigt. Wiedereinstiegspunkte öffnen PRs,
kopieren Login-Befehle oder starten eine allowlistete Prüfung.

Die Startseite bietet Git-, Test- und Demo-Prüfungen einzeln sowie als Prüfkette an.
Die Prüfkette stoppt beim ersten Fehler; das Ergebnis bleibt bis zum Schließen sichtbar.
Der Server lässt nur eine Prüfung gleichzeitig zu; weitere Aufrufe zeigen einen Fehler
an, statt eine zweite vollständige Testsuite parallel zu starten.
Diese festen Checks laufen im Checkout, der als „Deck“ angezeigt wird; die drei
Projektprioritäten sind kein ausführbarer Auftrag. Das Deck zeigt den laufenden
Checkout und den lokal gespeicherten `origin/main` getrennt. Bei abweichenden
Commits oder lokalen Änderungen warnt es vor einem zu prüfenden Stand. Auf einer HTTP-Seite
ohne sicheren Browser-Kontext öffnet ein Kopierknopf den auswählbaren TERM-Befehl
zur manuellen Übernahme, etwa auf dem iPhone. Agenten werden damit nicht gestartet.

Einzelprüfungen und „Prüfkette starten“ sperren ihren jeweiligen Button
während des Laufs. HTTP-, Netzwerk- und Antwortfehler erscheinen mit `FAIL` und Grund
im Panel. Danach wird der Button wieder freigegeben und der Status neu geladen.

Für Gemini wird bei vorhandenem Antigravity weiterhin `agy models` abgefragt.
Ohne `agy` prüft das Deck ausschließlich, ob die Gemini-CLI im Suchpfad vorhanden
ist. Es startet sie nicht und zeigt Gelb mit „CLI installiert · Anmeldung ungeprüft“.
Fehlt auch `gemini`, bleibt der Status Rot (`missing`). Wiederholte Statusabfragen
starten keinen Gemini-Modellauftrag und belegen weder Anmeldung noch Kontingent.

## Projektstand und Wiedereinstieg

Die Statuskorrekturen aus PR #115 sind übernommen: Nachweise verwenden den lokalen
Verweis `refs/remotes/origin/main`; HTTP-Refresh führt weder Git-Fetch noch eine
GitHub-CI-Abfrage aus. CI bleibt „nicht live abgefragt“. Gemergter Code zählt als
Implementierung, offene Gemini-Sicherheitsreviews (B4/B5) und Betriebsnachweise
(A2/C2) bleiben HOLD. Der gesonderte Claude-DB-Review für B4/B5 entfällt nach
der Entscheidung vom 18.10.2026; das E2-Gesamtreview ist noch offen (`docs/DECISIONS.md`). C3 hat
einen dokumentierten Claude-DB-Review am Merge-Head und wird bei gemergtem Code
nicht mehr als fehlender Review angezeigt.
C5 zählt seinen gemergten Probe-Code, bleibt ohne Betriebsnachweis HOLD; E3 kommt vor den Portal-Schritten D1–D3.
Das Deck erteilt keine Beta-/Release-Freigabe.

Ohne Antigravity kopiert der Gemini-Wiedereinstieg `gemini` statt `agy`. Die gelbe
Anzeige bedeutet ungeprüfte Anmeldung; sie ist kein fehlgeschlagener Modelltest.
Research-Karten und ihr Detailpanel zeigen die gespeicherte Quelle als Text.

TERM-Kommandos zeigen auf den Checkout aus `--repo`, nicht auf ein zweites,
möglicherweise veraltetes `~/GeniusNew`. Vorhandene lokale venv-Pythonpfade werden
verwendet. Statusabfragen führen diese TERM-Kommandos nicht aus.

Auch die HTTP-Aktion `tests` verwendet `repo/.venv/bin/python3`, wenn dieser Pfad
vorhanden ist, sonst `python3`. Sie teilt diese Auswahl mit den TERM-Befehlen und
behält die feste Argumentliste und Aktions-Allowlist bei. Das Zeitlimit beträgt
vier Minuten, damit die vollständige Suite im Deck-Checkout beendet werden kann.

## Mail Center

Das Control Deck liest ausschließlich einen normalisierten lokalen Mail-Snapshot. Der
Standardpfad ist `~/.local/state/genius-control-deck/mail.json`; alternativ kann
`GENIUSNEW_MAIL_SNAPSHOT` auf einen anderen lokalen Pfad zeigen. Der Snapshot ist
Runtime-State und darf nicht in Git committed werden.

Mail- und Projektstatus werden unabhängig geladen. Ein fehlgeschlagener Mail-Abruf
oder eine ungültige JSON-Antwort zeigt „Mail-Snapshot nicht synchronisiert“, während
der Projektstatus weiter gerendert wird. Fehler beim Statusabruf werden sichtbar
im Projektkopf angezeigt. Alle vier Mail-Kategorien zeigen Betreff und Absender
als maskierten Text im bestehenden Kartenlayout.

Der Provider-Bridge — zunächst Superhuman — bleibt außerhalb des Control-Deck-Prozesses
und schreibt nur die minimal nötigen Metadaten: Thread-ID, Kategorie, Betreff, Absender,
ungelesen/wichtig/kritisch und Zeitstempel. Mail-Bodies, OAuth-Tokens und Credentials
werden vom Dashboard weder benötigt noch ausgegeben.

Kategorien:

- `sales` → Verkauf;
- `sponsoring` → Sponsoring/Partnerschaften;
- `general` → Allgemein;
- `system` → GitHub, Security, Billing, Server und andere Betriebsnachrichten.

Das Symbol zeigt `!!` bei kritischen Nachrichten, `!` bei wichtigen Nachrichten und
ansonsten die Zahl ungelesener Nachrichten. Es gibt bewusst keine Aktion zum Senden,
Löschen, Archivieren oder Markieren von E-Mails.

## Sicherheitsgrenze

- GET-Routen: `/`, `/api/status`, `/api/mail` und `/api/agents`;
- POST ist ausschließlich auf `/api/action` erlaubt;
- `/api/action` akzeptiert nur die feste Allowlist `git_status`, `tests`, `demo`, `docker_status` und `hermes_status`;
- unbekannte Aktionen werden vor Prozessstart abgelehnt;
- keine freie Shell, kein Merge, kein Deploy und keine Security-Freigabe aus dem Browser;
- keine Secrets, Remote-URLs oder Environment-Werte werden angezeigt;
- Standard-Bindung nur auf Loopback; der Serverbetrieb bindet explizit an die private
  Tailscale-IP. `--host` lehnt jede andere Adresse ab, auch `0.0.0.0`: erlaubt sind
  IPv4-Loopback und `100.64.0.0/10`; IPv6 unterstützt der Server nicht. Nutzt der Provider des Servers
  selbst CGNAT, kann eine Adresse aus `100.64.0.0/10` auch am öffentlichen Interface
  liegen; dann bitte die Adresse aus `tailscale ip -4` verwenden, nicht raten;
- direkter Zugriff vom iPad erfolgt ausschließlich über das private Tailscale-Netz;
- jede Anfrage muss einen `Host`-Header tragen, der zur gebundenen Adresse und zum Port
  passt (oder zu `localhost`/`127.0.0.1`), sonst `421`. Das verhindert DNS-Rebinding:
  Eine fremde Webseite, deren Name auf die Deck-Adresse zeigt, kann weder `/api/mail`
  lesen noch Aktionen starten. POST mit fremdem `Origin` wird mit `403` abgelehnt;
- Zugriff über einen Namen statt der IP (z. B. `tailscale serve` oder MagicDNS) braucht
  den Namen ausdrücklich: `--allow-host <name>` bzw. `--allow-host <name>:<port>`.

Das Dashboard ist Beobachter. Ein rotes Gate darf nicht per UI auf grün gesetzt werden.
Der Status muss aus Repository, Diensten und tatsächlichen Nachweisen folgen.

## KI-Auswahl und Verfügbarkeit

Stand der manuellen Meldung von Kaan: 20.10.2026. Codex über ChatGPT Pro hat
laut Meldung 100 % Kontingent. Für Claude Pro wurde ein Reset etwa 40 Minuten
nach der Meldung erwartet; sein aktuelles Kontingent ist nicht erneut geprüft.
GitHub Copilot, Kiro und Abacus.AI sind für zwei Wochen nicht verwendbar. Die
Wiedervorlage ist der 03.11.2026; sie aktiviert sie nicht automatisch. Ihre
Karten enthalten in dieser Zeit keine Start- oder Kopierbefehle.

Gemini Pro hat laut Kaan noch ein Pro-Konto. Google Antigravity erscheint als
eigener Agentenclient in der Auswahl. Das Deck prüft für ihn weiterhin nur
`agy models`; eine erfolgreiche Modellliste bestätigt den Zugang, nicht
Restkontingent. Am 20.10.2026 beantwortete `gemini-4-argon-low` einen kurzen
Auftrag im Plan-Modus. Dieser Einzeltest misst kein Restkontingent. Ist Antigravity
erreichbar, bleibt es als Werkzeug verfügbar. Es ist kein Nachweis für ein
zusätzliches Gemini-Kontingent. Die Pause der anderen Werkzeuge ändert keine
Review-Pflichten aus `docs/COLLABORATION.md`.

### Weitere Agentenzugänge

Der Agentenbereich liefert über `/api/agents` feste Webzugänge sowie lokale Installations-
und Terminalstatusdaten. Es entstehen keine Modellaufträge durch Seitenaufrufe.
Installiert, Terminal geöffnet, angemeldet und erfolgreicher Modellauftrag sind
unterschiedliche Zustände; eine offene Terminal-Sitzung beweist keinen Modellzugriff.

- Grok: direkter Web-Zugang; der Browser prüft die Anmeldung.
- Grok Bot: externer Zugang, ohne unterstützten direkten Steuerungsadapter.
  Weder ein installierter Skill noch eine frühere SSH-Einrichtung beweisen Live-Zugriff.
- Abacus.AI: Textreview-Profile wurden am 05.10.2026 geprüft. Dieser alte
  Testnachweis belegt keine aktuelle Nutzbarkeit. Während der gemeldeten Pause
  zeigt die Karte weder Browser-Link noch TERM-Kopierknöpfe. Automatische
  Repo-Arbeit bleibt ungeprüft. Das Deck liest keine Credentials und reicht
  keine Secrets weiter.
- Grok Build bleibt pausiert, bis sein unterstützter Zugriffs- und Sandbox-Umfang neu
  geprüft und als eigenes Gate freigegeben ist.
- Hermes-Status liest nur Programmpfad und tmux-Zustand; der Paket-Launcher wird
  nicht ausgeführt, da bereits seine Versionsabfrage eine Installationssperre schreibt.
- Hermes: fest vorgegebener Terminalstart mit bestehendem Modellanbieter und bestehender
  Werkzeugkonfiguration, maximal acht Turns und 120 Sekunden pro Auftrag.
  Hermes hat kein eigenes Modellkontingent; ein Codex-Anbieter verbraucht Codex-Kontingent.

„Start / Öffnen · TERM“ kopiert einen festen Befehl. Er startet oder öffnet die
Sitzung erst nach Ausführung im angemeldeten Server-Terminal. Die HTTP-API kann
keine Agenten starten, keine Prompts senden und keine freien Befehle ausführen.
Ein Clipboard-Fallback unterstützt das private HTTP-Dashboard auf iPad/iPhone.

Server-Terminal, aus dem Repository-Root:

```sh
python3 -m tools.control_deck.actions --start-agent hermes
```

Mit --detach wird nur gestartet. Mehrfacher Start verwendet dieselbe Sitzung;
andere tmux-Sitzungen bleiben unberührt. Starts erfolgen ohne initialen Modellauftrag.
Die Diensthärtung (u. a. NoNewPrivileges und schreibgeschütztes Home) bleibt erhalten.
