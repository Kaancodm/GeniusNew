# Server einrichten: Tailscale und Termius mit einem Werkzeug

`ops/server/genius-server` richtet einen Debian- oder Ubuntu-Server so ein, dass du ihn
vom iPad oder Laptop über **Tailscale** und **Termius** erreichst, und zeigt jederzeit,
was gerade läuft. Es ist ein einzelnes Bash-Skript ohne Abhängigkeiten.

**Grundregel:** Ohne `--apply` verändert das Werkzeug nichts. Es zeigt jeden Befehl und
den Grund dazu. Verändert wird nur mit `--apply`.

**Grenze:** Das Werkzeug ist in der Testumgebung mit Attrappen für apt, systemd, sshd, ufw
und Tailscale geprüft (`tests/test_server_tool.py`), nicht auf einem echten Server.
Den ersten echten Lauf machst du selbst, zuerst als Trockenlauf.
Es betrifft Server-Betrieb und keinen Kerncode; das Deployment der GeniusNew-Dienste ist
nicht Teil davon (`docs/ANCHOR-SERVICE.md`, `docs/REVERSE-PROXY.md`).

## Voraussetzungen

- Debian oder Ubuntu mit systemd, ein normaler Benutzer mit `sudo` (kein reiner root-Zugang).
- Ein Tailscale-Konto, die Tailscale-App auf iPad/iPhone im selben Konto.
- Termius auf dem iPad/iPhone.
- Einen ersten Zugang zum Server (Konsole des Anbieters oder SSH mit Passwort).

## Befehle

| Befehl | Wirkung |
| --- | --- |
| `status` | Was läuft gerade? Nur lesen: Last, Tailscale, SSH, Sitzungen, Firewall, offene Ports, Dienste, Prozesse, **nächster Schritt** |
| `status --watch 5` | dasselbe, alle 5 Sekunden neu |
| `setup` | Trockenlauf: alle 6 Schritte mit Befehl und Grund |
| `setup --apply` | führt sie aus: Pakete, Tailscale, SSH nur mit Schlüssel |
| `add-key` | öffentlichen Schlüssel aus Termius eintragen |
| `lockdown --apply` | Firewall: SSH nur noch über Tailscale (mit automatischem Zurückrollen) |
| `confirm` | aus einer zweiten Tailscale-Sitzung: Firewall behalten |
| `termius` | zeigt Adresse, Port und Benutzer für Termius |

Aufruf: `bash genius-server <befehl>`. Beispiele unten nutzen `bash ops/server/genius-server`.

## Ablauf beim ersten Mal

1. Skript auf den Server holen und **lesen** (Git fehlt auf frischen Servern oft:
   `sudo apt-get install -y git`):
   `git clone https://github.com/Kaancodm/GeniusNew && cd GeniusNew && less ops/server/genius-server`
   (auf `main` erst nach dem Merge dieses PRs).
2. Trockenlauf: `bash ops/server/genius-server setup`. Jeder Schritt zeigt „Befehl:“ und „Warum:“.
3. `bash ops/server/genius-server setup --apply`. Es installiert Pakete, richtet die
   offizielle Tailscale-Paketquelle ein und zeigt einen **Anmelde-Link**. Öffne ihn am iPad
   und melde den Server im Tailscale-Konto an. Alternativ ohne Link: ein Auth-Key in einer
   Datei mit Modus 600 und `GENIUS_TS_AUTHKEY_FILE=<datei>` (der Key steht nie in einem
   Befehl oder im Repository).
4. In Termius einen Schlüssel erzeugen: *Keychain → + → Generate Key → ED25519*, den
   **öffentlichen** Schlüssel kopieren. Den privaten Schlüssel gibst du nie weiter.
5. Auf dem Server: `bash ops/server/genius-server add-key`, den öffentlichen Schlüssel einfügen.
   Das Werkzeug prüft Typ und Länge und zeigt den Fingerabdruck; vergleiche ihn mit Termius.
6. Nochmals `setup --apply`. Jetzt ist ein Schlüssel da, und das Werkzeug schaltet
   Passwort-Login und root-Login ab. Es prüft die neue Konfiguration mit `sshd -t` und
   `sshd -T` (alle vier gesetzten Werte) und nimmt sie bei Abweichung oder einem
   fehlgeschlagenen Neuladen zurück. Bestehende Verbindungen bleiben offen. Steht irgendwo ein
   `Match`-Block, bricht der Schritt vorher ab: `Match` kann den Passwort-Login für einzelne
   Benutzer oder Adressen wieder einschalten.
7. In Termius den Host anlegen (`bash ops/server/genius-server termius` zeigt die Werte):
   Adresse = MagicDNS-Name oder 100.x-Adresse, Port (aus `sshd -T`, meist 22), Benutzer,
   Schlüssel. Tailscale-App muss an sein.
8. **Schlüssel-Login in Termius testen, bevor du die erste Sitzung schließt.**
9. In dieser Termius-Sitzung (also über Tailscale): `bash ops/server/genius-server lockdown --apply`.
   Es stellt zuerst ein Sicherheitsnetz (nach 5 Minuten schaltet sich die Firewall von selbst
   aus), setzt die Regeln und schaltet `ufw` ein. SSH gilt dann nur noch über `tailscale0`,
   auf genau den Ports, auf denen sshd lauscht. Ist `ufw` schon aktiv, ändert `lockdown` nichts:
   Das Sicherheitsnetz könnte die Firewall nur ganz abschalten, nicht alte Regeln zurückholen.
10. Zweite Termius-Verbindung über Tailscale öffnen und `bash ops/server/genius-server confirm`
    ausführen. Erst das entfernt das Sicherheitsnetz. Hat es schon ausgelöst, meldet `confirm`
    das und bestätigt nicht.
11. Ab jetzt `status` (oder `status --watch`) für die Übersicht. Der letzte Abschnitt nennt
    den nächsten Schritt.
12. Langläufer in `tmux new -As genius` starten; die Sitzung überlebt einen Verbindungsabbruch.

## Schutz vor Aussperren

- Die SSH-Härtung wird **übersprungen**, solange in `authorized_keys` kein gültiger Schlüssel steht.
- `lockdown` und `confirm` laufen nur aus einer Sitzung, deren Quelladresse in 100.64.0.0/10 oder
  fd7a:115c:a1e0::/48 liegt (Tailscale, IPv4 oder IPv6; MagicDNS geht also auch). Starte sie **ohne** `sudo` davor; sudo entfernt die Verbindungsangabe, dann
  bricht das Werkzeug ab (sicher, aber lästig). Das Werkzeug ruft `sudo` selbst auf.
- Die Firewall hat ein Sicherheitsnetz mit Zeitgeber. Ohne `confirm` ist sie nach 5 Minuten wieder aus.
- `lockdown` warnt, wenn eine ältere Regel (zum Beispiel `22/tcp ALLOW Anywhere`) SSH weiter
  öffentlich offen lässt. Löschen musst du sie selbst: `sudo ufw delete allow 22/tcp`.
- Andere eingehende Dienste (Web, Datenbank) sind nach `lockdown` von außen nicht erreichbar.
  Das ist gewollt; Freigaben nur gezielt und über `tailscale0`.

## Was das Werkzeug nicht tut

- Es legt keinen Benutzer an und erzeugt keine Schlüssel (der Schlüssel entsteht in Termius).
- Es installiert keine GeniusNew-Dienste, keine Datenbank, keinen Reverse-Proxy, keine TLS-Zertifikate.
- Es ändert keine Zugangsdaten und speichert keine Geheimnisse.
- Es ersetzt keinen Server-Review: Release, Deployment und Zugriffsrechte brauchen Kaans OK
  (`AGENTS.md`).

## Fehlersuche

| Meldung | Bedeutung |
| --- | --- |
| `als root gestartet` | Als normaler Benutzer mit sudo starten, oder `GENIUS_USER=<benutzer>` setzen |
| `nur Debian oder Ubuntu` | Andere Systeme sind nicht unterstützt (fail closed) |
| `ÜBERSPRUNGEN … kein gültiger Schlüssel` | Erst `add-key`, dann `setup --apply` erneut |
| `'…' gilt nicht` | Eine andere sshd-Datei überstimmt die Härtung; zurückgenommen, nichts geändert |
| `Match-Blöcke in …` | `Match` aus der genannten Datei entfernen oder die Härtung von Hand machen |
| `ufw ist schon aktiv` | `lockdown` ändert keine aktive Firewall; Regeln mit `sudo ufw status verbose` prüfen |
| `SSH-Port nicht ermittelbar` | `sudo sshd -T \| grep ^port` liefert keinen gültigen Port |
| `Du bist nicht über Tailscale verbunden` | In Termius über die Tailscale-Adresse neu verbinden |
| `schon ausgelöst` oder `nicht aktiv` bei `confirm` | Das Sicherheitsnetz hat ausgelöst; eine Minute warten, dann `lockdown --apply` erneut |
