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
Alarmstatus für Verkauf, Sponsoring, Allgemein und System. TERM-Befehle werden nur in
die Zwischenablage kopiert. Wiedereinstiegspunkte öffnen PRs, kopieren Login-Befehle
oder starten eine allowlistete Prüfung.

Einzelprüfungen und „Ausführbare Checks starten“ sperren ihren jeweiligen Button
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
Implementierung, fehlende Reviews (B4/B5) und Betriebsnachweise (A2/C2) bleiben HOLD.
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
behält die feste Argumentliste und Aktions-Allowlist bei.

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

## Grok, Abacus.AI und Hermes

Der Agentenbereich liefert über `/api/agents` feste Webzugänge sowie lokale Installations-
und Terminalstatusdaten. Es entstehen keine Modellaufträge durch Seitenaufrufe.
Installiert, Terminal geöffnet, angemeldet und erfolgreicher Modellauftrag sind
unterschiedliche Zustände; eine offene Terminal-Sitzung beweist keinen Modellzugriff.

- Grok: direkter Web-Zugang; der Browser prüft die Anmeldung.
- Grok Bot: externer Zugang, ohne unterstützten direkten Steuerungsadapter.
  Weder ein installierter Skill noch eine frühere SSH-Einrichtung beweisen Live-Zugriff.
- Abacus.AI: Browser-Link und zwei TERM-Kopierknöpfe für die geprüften Textreview-
  Profile Gemini 3.8 Flash und Sonnet 5.5. Die Anzeige kennzeichnet den historischen
  Testnachweis vom 05.10.2026 und das aktuell ungeprüfte Kontingent getrennt.
  Beide Starter öffnen die jeweilige getestete Sitzung im Plan-Modus mit gesperrten
  Werkzeugen; erst ein eingegebener Auftrag verbraucht Modellkontingent.
  Die Modellkennungen wurden am CLI-Lauf belegt, nicht aus der Antwort abgeleitet.
  Die API startet Abacus nicht. Automatische Repo-Arbeit bleibt ungeprüft.
  Das Deck liest keine Credentials und reicht keine Secrets weiter.
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
