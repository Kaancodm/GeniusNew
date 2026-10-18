# iPad als privater Modellserver

`tools/ipad_llm.py` fragt ein OpenAI-kompatibles Modell auf einem iPad über Tailscale
ab. Es ist ein eigenständiges Werkzeug für einen einzelnen Textaufruf. Seine Ausgabe
ist nicht vertrauenswürdig und hat keine Wirkung auf GeniusNew-Jobs, Freigaben,
Worker, Signaturen oder Audit. Ein LLM-Worker ist in `docs/ROADMAP-V02.md` noch
ausdrücklich ausgenommen.

## iPad vorbereiten

Die App auf dem Beispielbild sieht nach PocketPal AI aus. PocketPal kann derzeit
einen entfernten Modellserver **nutzen**, stellt selbst aber keinen bestätigten
HTTP-Server bereit. Eine dokumentierte Alternative ist [LLM Server von
Linosec](https://linosec.lu/apps/llm_server.html): Sie bietet GGUF/Gemma,
`/v1/models`, `/v1/chat/completions`, Bearer-Authentisierung und eine wählbare
Netzwerkschnittstelle. Die App ist kostenpflichtig. Das bereits in PocketPal
gespeicherte Modell muss gegebenenfalls in der Server-App erneut geladen werden.

1. iPad und Linux-Server im selben Tailscale-Netz anmelden.
2. In der iPad-Server-App ein passendes Gemma-4-GGUF laden und den API-Server starten.
   Die App nur an die Tailscale-Schnittstelle binden, sofern diese in der App
   auswählbar ist. `localhost` allein genügt nicht; `alle Schnittstellen` kann
   den Dienst auch im WLAN freigeben. Vor der Nutzung prüfen, dass der Port an
   der WLAN-Adresse des iPads nicht erreichbar ist. Die API-Schlüssel-Funktion
   einschalten. Keine öffentliche Portfreigabe einrichten. Falls die App keine
   auf Tailscale beschränkte Bindung ermöglicht, ist diese Einrichtung offen.
3. Den bereits freigegebenen API-Schlüssel **außerhalb** des Repositories in einer
   Datei ablegen, die dem aufrufenden Linux-Nutzer gehört und Modus `0600` hat.
   Der Schlüssel muss aus 32 bis 256 druckbaren ASCII-Zeichen bestehen.
   Den Schlüssel weder als Befehlsargument noch in Git speichern. Das Erzeugen
   oder Rotieren eines dauerhaft genutzten Schlüssels braucht Kaans GO.
4. Die Tailscale-IPv4-Adresse des iPads und den Port aus der App bzw. Tailscale
   ablesen. Der Server muss das iPad als online iOS/iPadOS-Peer sehen.

Auf dem Linux-Server im Repository:

```sh
.venv/bin/python -m tools.ipad_llm models \
  --host 100.x.y.z --port 8080 --key-file /privater/pfad/ipad-api-key

printf '%s\n' 'Kurze Testfrage' | .venv/bin/python -m tools.ipad_llm chat \
  --host 100.x.y.z --port 8080 --key-file /privater/pfad/ipad-api-key \
  --model '<genaue ID aus models>'
```

Die Werte `100.x.y.z`, Port, Pfad und Modell-ID sind Platzhalter. Das Werkzeug
akzeptiert nur die exakte IPv4-Adresse eines online iOS/iPadOS-Peers aus
`tailscale status --json` und prüft mit `ip route get`, dass der Verkehr über
`tailscale0` geht. Es verwendet keine HTTP-Proxys, folgt keinen Redirects und
begrenzt Eingabe, Ausgabe und die HTTP-Laufzeit. Tailscale-Status und
Routenprüfung haben jeweils eine eigene Frist von fünf Sekunden. HTTP läuft
innerhalb des verschlüsselten Tailscale-Netzes; dieser Client unterstützt derzeit
kein separates HTTPS-Zertifikat der iPad-App. Ein nicht erreichbares iPad, eine
unerwartete API-Antwort oder eine abweichende Modell-ID führt zu einer Ablehnung
ohne Ausweichmodell.

Der Linux-Nutzer muss einen Socket an `tailscale0` binden dürfen
(`SO_BINDTODEVICE`). Wo der Kernel dafür ein Recht verlangt, ist `CAP_NET_RAW`
erforderlich. Scheitert die Bindung, lehnt der Client den Aufruf ab, bevor ein
API-Schlüssel gesendet wird.

**Gerätestatus:** Die lokale Testsuite nutzt einen HTTP-Testserver. Ein echter
Aufruf zum iPad ist erst nach Installation, Modellstart, API-Konfiguration und
Angabe des privaten Endpunkts prüfbar. iPadOS kann die Server-App im Hintergrund
anhalten; sie ist daher kein zugesicherter Dauerbetrieb.
