# Server einrichten: Tailscale und Termius mit einem Werkzeug

`ops/server/genius-server` richtet einen Debian- oder Ubuntu-Server so ein, dass du ihn
vom iPad oder Laptop über **Tailscale** und **Termius** erreichst, und zeigt jederzeit,
was gerade läuft. Es ist ein einzelnes Bash-Skript ohne Abhängigkeiten.

**Grundregel:** `setup`, `lockdown` und `deck` verändern ohne `--apply` nichts; sie zeigen jeden
Befehl und den Grund dazu. `add-key` (trägt einen Schlüssel ein) und `confirm` (behält die
Firewall) wirken sofort, denn sie sind selbst der bewusste Schritt. `status` und `termius`
lesen nur.

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
| `confirm` | aus einer zweiten, **neuen** Tailscale-Verbindung: Firewall behalten |
| `termius` | zeigt Adresse, Port und Benutzer für Termius |
| `deck --apply` | Control Deck (Dashboard) als systemd-Dienst, nur über Tailscale |

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
    ausführen. Erst das entfernt das Sicherheitsnetz. Aus der Verbindung, in der `lockdown`
    lief, lehnt `confirm` ab: Eine schon bestehende Verbindung bleibt offen, auch wenn die neuen
    Regeln jede neue Verbindung sperren würden; nur eine neue beweist, dass SSH erreichbar bleibt. Hat es schon ausgelöst, meldet `confirm`
    das und bestätigt nicht.
11. Ab jetzt `status` (oder `status --watch`) für die Übersicht. Der letzte Abschnitt nennt
    den nächsten Schritt.
12. Langläufer in `tmux new -As genius` starten; die Sitzung überlebt einen Verbindungsabbruch.
13. Optional, das Dashboard als Dienst: erst `bash ops/server/genius-server deck` lesen, dann
    `bash ops/server/genius-server deck --apply`. Danach am iPad mit eingeschaltetem Tailscale
    `http://<100.x-Adresse>:8787` öffnen.

## Dashboard (Control Deck) als Dienst

`deck --apply` schreibt `/etc/systemd/system/geniusnew-deck.service`, startet den Dienst und
gibt erst danach in der Firewall Port 8787 **nur auf `tailscale0`** frei. Er startet beim
Booten neu.

- Er läuft als dein Benutzer, nicht als root, mit `NoNewPrivileges`, `PrivateTmp`,
  `ProtectSystem=full` und `ProtectHome=read-only`: Test und Demo aus dem Dashboard können
  das Repository und dein Home nicht verändern.
- Er bekommt das Repository ausdrücklich (`--repo`), nicht über einen eingebauten Standardpfad.
- Er ist nur an die Tailscale-Adresse gebunden. Das Control Deck selbst lehnt jede andere Adresse ab
  (`docs/CONTROL-DECK.md`).
- Gibt es im Repository `.venv`, nimmt der Dienst dessen Python, sonst `python3` des Systems.
  Dieses Verzeichnis steht im `PATH` des Dienstes vorn, damit Test und Demo dieselben
  Abhängigkeiten finden.
- Port ändern: `GENIUS_DECK_PORT=9000 bash ops/server/genius-server deck --apply`. Die
  Firewall-Regel für den alten Port wird dabei gelöscht.
- Startet der Dienst nicht, öffnet das Werkzeug keinen Port, stoppt den Dienst wieder und
  löscht eine Firewall-Regel für diesen Port aus einem früheren Lauf, damit kein anderes
  Programm auf dem Port über Tailscale erreichbar bleibt.
- Hat sich die Tailscale-Adresse geändert, einfach `deck --apply` erneut ausführen.
- Wieder entfernen:
  ```sh
  sudo systemctl disable --now geniusnew-deck
  sudo rm /etc/systemd/system/geniusnew-deck.service && sudo systemctl daemon-reload
  sudo ufw delete allow in on tailscale0 to any port 8787 proto tcp
  ```

## Schutz vor Aussperren

- Die SSH-Härtung wird **übersprungen**, solange in `authorized_keys` kein gültiger Schlüssel steht.
- `lockdown` und `confirm` laufen nur aus einer Verbindung, deren Quelladresse in 100.64.0.0/10
  oder fd7a:115c:a1e0::/48 liegt **und** deren Zieladresse eine Tailscale-Adresse dieses Servers
  ist (IPv4 oder IPv6; MagicDNS geht also auch). Die Quelladresse allein reicht nicht, weil
  100.64.0.0/10 auch von Anbietern als CGNAT genutzt wird. Starte beide **ohne** `sudo` davor;
  sudo entfernt die Verbindungsangabe, dann bricht das Werkzeug ab (sicher, aber lästig). Das
  Werkzeug ruft `sudo` selbst auf.
- Die Firewall hat ein Sicherheitsnetz mit Zeitgeber. Ohne `confirm` ist sie nach 5 Minuten wieder aus.
- `lockdown` und `confirm` brechen ab, solange eine ufw-Regel Verkehr von außerhalb Tailscale
  zulässt (zum Beispiel `ufw allow 22/tcp`): Eine Standard-Sperre entfernt solche Regeln nicht,
  SSH bliebe also öffentlich. Das Werkzeug zeigt die Löschbefehle (`sudo ufw delete allow 22/tcp`);
  ausführen musst du sie selbst.
- `confirm` gilt nur aus einer neuen Verbindung, nicht aus der, in der `lockdown` lief.
- Andere eingehende Dienste (Web, Datenbank) sind nach `lockdown` von außen nicht erreichbar.
  Das ist gewollt; Freigaben nur gezielt und über `tailscale0`.

## Was das Werkzeug nicht tut

- Es richtet **keinen Laptop** ein. Unter WSL brechen `setup` und `lockdown` ab: Auf dem
  Laptop gehört Tailscale zu Windows, nicht in WSL, und `ufw` hat in WSL keine Wirkung.
- Es legt keinen Benutzer an und erzeugt keine Schlüssel (der Schlüssel entsteht in Termius).
- Es installiert keine GeniusNew-Dienste, keine Datenbank, keinen Reverse-Proxy, keine TLS-Zertifikate.
- Es ändert keine Zugangsdaten und speichert keine Geheimnisse.
- Es ersetzt keinen Server-Review: Release, Deployment und Zugriffsrechte brauchen Kaans OK
  (`AGENTS.md`).

## Fehlersuche

| Meldung | Bedeutung |
| --- | --- |
| `als root gestartet` | Als normaler Benutzer mit sudo starten, oder `GENIUS_USER=<benutzer>` setzen |
| `Das Dashboard läuft nicht` | Ursache mit `sudo journalctl -u geniusnew-deck -n 30` ansehen; meist ist der Port belegt (`GENIUS_DECK_PORT` ändern) |
| `Das ist WSL (Laptop), kein Server` | Auf dem Server ausführen, nicht im Laptop-WSL |
| `nur Debian oder Ubuntu` | Andere Systeme sind nicht unterstützt (fail closed) |
| `ÜBERSPRUNGEN … kein gültiger Schlüssel` | Erst `add-key`, dann `setup --apply` erneut |
| `'…' gilt nicht` | Eine andere sshd-Datei überstimmt die Härtung; zurückgenommen, nichts geändert |
| `Match-Blöcke in …` | `Match` aus der genannten Datei entfernen oder die Härtung von Hand machen |
| `ufw ist schon aktiv` | `lockdown` ändert keine aktive Firewall; Regeln mit `sudo ufw status verbose` prüfen |
| `SSH-Port nicht ermittelbar` | `sudo sshd -T \| grep ^port` liefert keinen gültigen Port |
| `Du bist nicht über Tailscale verbunden` | In Termius über die Tailscale-Adresse neu verbinden |
| `Diese ufw-Regeln lassen Verkehr auch von außerhalb …` | Gezeigte `sudo ufw delete …`-Befehle prüfen und ausführen, dann neu starten |
| `dieselbe Verbindung, aus der lockdown lief` | In Termius eine zweite Verbindung öffnen und `confirm` dort ausführen |
| `schon ausgelöst` oder `nicht aktiv` bei `confirm` | Das Sicherheitsnetz hat ausgelöst; eine Minute warten, dann `lockdown --apply` erneut |
