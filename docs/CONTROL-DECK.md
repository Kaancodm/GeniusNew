# Genius Control Deck

Das Control Deck ist die read-only Operationsoberfläche für den server-first Betrieb von
GeniusNew. Es gehört nicht zur Zero-Trust-Runtime und erhält keine Autorität über
Gateway, Worker, Datenbank oder Audit-Anker.

## Start

Vom Repository-Root:

```bash
python3 -m tools.control_deck --host 127.0.0.1 --port 8787
```

Das Dashboard zeigt Git, Tailscale, Desktop Commander, Docker, tmux, Agent-Logins und
die 21 Beta-Gates. Die TERM-Befehle werden nur in die Zwischenablage kopiert.

## Sicherheitsgrenze

- nur GET-Routen (`/`, `/api/status`);
- POST wird mit 405 abgelehnt;
- keine Route führt Shell-Befehle aus;
- keine Secrets, Remote-URLs oder Environment-Werte werden angezeigt;
- Standard-Bindung nur auf Loopback;
- direkter Zugriff vom iPad erfolgt ausschließlich über die private Tailscale-IP.

Das Dashboard ist Beobachter. Ein rotes Gate darf nicht per UI auf grün gesetzt werden.
Der Status muss aus Repository, Diensten und tatsächlichen Nachweisen folgen.