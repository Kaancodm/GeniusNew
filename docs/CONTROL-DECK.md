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

## Sicherheitsgrenze

- GET-Routen: `/`, `/api/status` und `/api/mail`;
- POST ist ausschließlich auf `/api/action` erlaubt;
- `/api/action` akzeptiert nur die feste Allowlist `git_status`, `tests`, `demo`
  und `docker_status`;
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