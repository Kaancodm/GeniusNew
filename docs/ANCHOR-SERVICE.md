# Audit-Anker als eigener Dienst

Standardmäßig startet der Dienst den Audit-Anker als Kind-Prozess (`AnchorProcess`).
Dann kann der Dienst ihn auch beenden, und derselbe Betriebssystem-Nutzer kann seine
Zustandsdatei auf einen älteren, gültig signierten Kopf zurückschneiden (`SECURITY.md`).
Mit `serve` läuft der Anker getrennt vom Dienst, und der Dienst spricht ihn über einen
Unix-Socket mit `AnchorClient` an.

## Was der Code zusichert

- Der Anker hält nur den **öffentlichen** Audit-Schlüssel. Er kann gefälschte Köpfe
  ablehnen, aber selbst keinen ausstellen.
- Jede Antwort ist mit dem **eigenen Ed25519-Schlüssel des Ankers** über die Nonce der
  Anfrage signiert. Der Client lehnt jede Antwort ab, die unsigniert ist, mit einem
  anderen Schlüssel signiert ist oder zu einer anderen Anfrage gehört. Wer zuerst am
  Socket-Pfad lauscht, kann den Anker also nicht vertreten.
- Der Anker übernimmt keinen vorhandenen Pfad. Der Socket entsteht mit den Rechten
  `0660`, der Schlüssel mit `0600`. Ist die Schlüsseldatei für andere lesbar, zu kurz,
  zu lang oder ein Link, startet er nicht.
- Er übersteht eigene Neustarts mit derselben Zustandsdatei und demselben Schlüssel.
  Ein Neustart des Dienstes berührt ihn nicht.

## Was erst die Installation zusichert

Nur wenn der Anker unter einem **eigenen Nutzer** läuft, gilt:

- Der Dienstnutzer kann ihn nicht beenden.
- Der Dienstnutzer kann seine Zustandsdatei und seinen Schlüssel weder lesen noch
  zurückschneiden.

`root` auf demselben Host kann beides weiterhin.

## Einrichten (Beispiel mit systemd)

Beispiel-Nutzer `geniusnew-anchor` und `geniusnew`, gemeinsame Gruppe `geniusnew-audit`
für den Socket. Pfade und Namen sind Beispiele, keine Vorgabe.

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin geniusnew-anchor
sudo groupadd --system geniusnew-audit
sudo usermod -aG geniusnew-audit geniusnew-anchor
sudo usermod -aG geniusnew-audit geniusnew
sudo install -d -o geniusnew-anchor -g geniusnew-anchor -m 0700 /var/lib/geniusnew-anchor

# Schlüssel einmal erzeugen und den öffentlichen Teil für den Dienst notieren:
sudo -u geniusnew-anchor python3 -m geniusnew.anchor_process public-key \
    --key /var/lib/geniusnew-anchor/anchor.key
```

```ini
# /etc/systemd/system/geniusnew-anchor.service
[Unit]
Description=GeniusNew audit anchor

[Service]
User=geniusnew-anchor
Group=geniusnew-audit
RuntimeDirectory=geniusnew-anchor
RuntimeDirectoryMode=0750
ExecStart=/opt/geniusnew/.venv/bin/python -m geniusnew.anchor_process serve \
    --socket /run/geniusnew-anchor/anchor.sock \
    --state /var/lib/geniusnew-anchor/anchor.state \
    --key /var/lib/geniusnew-anchor/anchor.key \
    --audit-public-key <öffentlicher Audit-Schlüssel des Dienstes, hex>
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/geniusnew-anchor
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

Im Dienst:

```python
anchor = AnchorClient(socket_path="/run/geniusnew-anchor/anchor.sock",
                      reply_public_key=bytes.fromhex("<Ausgabe von public-key>"))
service = build(..., anchor=anchor)
```

Einen produktiven Startpunkt, der diese Werte aus einer Konfiguration liest, gibt es
noch nicht (`docs/BETA-READINESS.md`, B6).

## Neustart des Dienstes

Die Audit-Kette liegt bis zur Datenbank-Phase nur im Speicher des Dienstes. Nach einem
Neustart des Dienstes kennt der Anker also einen längeren Kopf als der Dienst. Er lehnt
den ersten neuen Kopf ab, und der Dienst verweigert jeden Auftrag (fail closed). Er
beginnt also nicht still eine neue Geschichte.
(`tests/test_end_to_end.py::EndToEndTest::test_a_served_anchor_outlives_the_service_and_stops_a_silent_new_history`)

Behoben wird das mit der persistenten Kette aus `docs/DATABASE.md`, nicht dadurch, dass
die Zustandsdatei des Ankers gelöscht wird. Wer sie löscht, verwirft bewusst den Beleg
der bisherigen Geschichte.
