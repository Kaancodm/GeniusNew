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

Das Dashboard zeigt Git, Tailscale, Desktop Commander, Docker, tmux, Agent-Logins und
die 21 Beta-Gates. Oben stehen Projektstand/Wiedereinstieg, die drei nächsten Schritte
sowie Research- und Ideenbewertungen. Ein read-only Mail Center zeigt einen kompakten
Alarmstatus für Verkauf, Sponsoring, Allgemein und System. TERM-Befehle werden nur in
die Zwischenablage kopiert. Wiedereinstiegspunkte öffnen PRs, kopieren Login-Befehle
oder starten eine allowlistete Prüfung.

## Mail Center

Das Control Deck liest ausschließlich einen normalisierten lokalen Mail-Snapshot. Der
Standardpfad ist `~/.local/state/genius-control-deck/mail.json`; alternativ kann
`GENIUSNEW_MAIL_SNAPSHOT` auf einen anderen lokalen Pfad zeigen. Der Snapshot ist
Runtime-State und darf nicht in Git committed werden.

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

## HARPA-Automationen

Der optionale HARPA-Eingang nimmt zeitgesteuerte Browser-Berichte über
`POST /api/harpa` an und zeigt die letzten Einträge im Dashboard. Ein Bericht kann
keine Control-Deck-Aktion, keinen Shell-Befehl und keinen Agentenstart anfordern.
Wiederholungen mit derselben `event_id` werden nicht doppelt gespeichert.

`genius-server deck --apply` erzeugt den eigenen Eingangsschlüssel einmalig außerhalb
des Repositorys und bindet ihn als systemd-Credential ein. Der Schlüssel wird beim
erneuten Einrichten nicht ersetzt und niemals im Dashboard oder in Logs ausgegeben.
Er wird einmal lokal in HARPA als `Authorization: Bearer …` hinterlegt.

HARPA `REQUEST`-Schritt:

- Methode: `POST`;
- URL: `http://<Tailscale-IP>:8787/api/harpa`;
- Header: `Content-Type: application/json` und `Authorization: Bearer <Schlüssel>`;
- Body: exakt die fünf Felder `event_id`, `kind`, `title`, `summary` und `source_url`.

Beispiel ohne echten Schlüssel:

```json
{
  "event_id": "pricing-2026-10-08T08:00:00Z",
  "kind": "monitor",
  "title": "Preisseite geändert",
  "summary": "Kurze Zusammenfassung der öffentlichen Änderung.",
  "source_url": "https://example.com/pricing"
}
```

`kind` ist auf `monitor`, `report` oder `research` begrenzt. Die Quelladresse muss
HTTP(S) verwenden und darf keine Zugangsdaten enthalten. Der Zeitplan wird in HARPA
unter **Automate → Run AI command** einmal eingerichtet. Der Laptop-Browser mit HARPA
muss zum Ausführungszeitpunkt erreichbar sein und Tailscale-Zugriff auf den Server haben.

## Sicherheitsgrenze

- GET-Routen: `/`, `/api/status`, `/api/mail`, `/api/agents` und `/api/harpa`;
- POST ist ausschließlich auf `/api/action` und `/api/harpa` erlaubt;
- `/api/action` akzeptiert nur die feste Allowlist `git_status`, `tests`, `demo`, `docker_status` und `hermes_status`;
- `/api/harpa` akzeptiert nur den festen Berichtvertrag und einen eigenen Bearer-Schlüssel;
- unbekannte Aktionen werden vor Prozessstart abgelehnt;
- keine freie Shell, kein Merge, kein Deploy und keine Security-Freigabe aus dem Browser;
- keine Secrets oder Environment-Werte werden angezeigt; HARPA-Berichte zeigen nur
  ihre validierte HTTP(S)-Quelladresse;
- Standard-Bindung nur auf Loopback; der Serverbetrieb bindet explizit an die private
  Tailscale-IP. `--host` lehnt jede andere Adresse ab, auch `0.0.0.0`: erlaubt sind
  IPv4-Loopback und `100.64.0.0/10`; IPv6 unterstützt der Server nicht. Nutzt der Provider des Servers
  selbst CGNAT, kann eine Adresse aus `100.64.0.0/10` auch am öffentlichen Interface
  liegen; dann bitte die Adresse aus `tailscale ip -4` verwenden, nicht raten;
- direkter Zugriff vom iPad erfolgt ausschließlich über das private Tailscale-Netz;
- jede Anfrage muss einen `Host`-Header tragen, der zur gebundenen Adresse und zum Port
  passt (oder zu `localhost`/`127.0.0.1`), sonst `421`. Das verhindert DNS-Rebinding:
  Eine fremde Webseite, deren Name auf die Deck-Adresse zeigt, kann weder `/api/mail`
  lesen noch Aktionen starten. `/api/action` lehnt POST mit fremdem `Origin` mit `403`
  ab; der HARPA-Eingang verwendet stattdessen seinen eigenen Bearer-Schlüssel;
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
- Abacus.AI: vorläufiger Ersatz für Grok Build. Das Control Deck öffnet ausschließlich
  ChatLLM im Browser; es gibt keinen lokalen Abacus-Start, keinen Repo-Zugriff und keine
  Secret-Weitergabe aus dem Control Deck.
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
