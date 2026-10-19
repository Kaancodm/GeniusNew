# Laptop-Modellproxy über Tailscale — überarbeiteter Sicherheitsentwurf (HOLD)

**Status:** NUR ENTWURF, keine Implementierungs- oder Betriebsfreigabe.  
**Review:** Issue #166, Kommentar 6257014548, Claude Security-Review vom 19.10.2026: `CHANGES REQUESTED`.  
**Basis:** `main`, Commit `77106760a7c529ace45201741a68bd044b04adee`.  
**Scope:** Phase P2 des rein beratenden `tools/ai_pilot`; kein `geniusnew/`, kein LLM-Worker, kein Merge/Deploy, keine neue Freigabeautorität. Kaan entscheidet die offenen Optionen.  
**Datenhygiene:** Alle Geräte- und Tailnet-Identitäten sind abstrakt; `127.0.0.1` ist ausschließlich Loopback, **keine Tailnet-IP**. Niemals echte Tailnet-IPs, Namen, Policy-Volltexte, Secrets oder nicht redigierte Ausgaben in dieses öffentliche Repo/Issue/PR aufnehmen.

## 0. Bereits belegter Ist-Zustand und nicht belegte Punkte

| Quelle / Prüfung | Ergebnis | Belegqualität |
| --- | --- | --- |
| Server `tailscale version` | am 19.10.2026 lesend festgestellt; exakte Versionsangabe nur in privatem Gate-A-Protokoll | keine Änderung |
| Laptop `tailscale version` | am 19.10.2026 lesend festgestellt; exakte Versionsangabe nur in privatem Gate-A-Protokoll | keine Änderung |
| Laptop `tailscale serve status --json` | `null`/keine sichtbare konfigurierte Route; `9443` nicht enthalten | Nur lokaler Status am 19.10.2026, keine Aussage zur Admin-Policy |
| Laptop Ollama Listener | genau ein Listener auf `11434`, nur `127.0.0.1`/`::1` | `Get-NetTCPConnection` am Laptop (lesend), Modell-ID dabei **UNKNOWN** |
| Server `tailscale serve status --json` | lokale `TCP`-Einträge für `443` und `8443` | Lesend geprüft; **Zuordnung zu Portal/Deck/n8n UNKNOWN** |
| Server Actions-Runner | eigener bestehender Unix-Nutzer `<RUNNER_OS_USER>`; genaue Identität nur privat festhalten | Lesend `systemctl show … -p User` und Prozessliste geprüft; im Entwurf redigiert |
| Tailnet-Policy / vorhandene Grants / ACLs | **UNKNOWN** | Kein autoritativer Admin-Export vorgelegt; Gate A nicht bestanden |
| Policy-Revisions-Restore im konkreten Account | **UNKNOWN** | Dokumentierter Herstellerweg ist kein Beleg für eine für diesen Account verfügbare Wiederherstellung |
| HTTPS-Zertifikate tailnetweit, Cert-Transparency-Namen, Funnel, Laptop-Firewall | **UNKNOWN** | Vor jedem Betriebs-GO gesondert prüfen |

Kontext: Im Issue #166 ist eine funktionierende Termius-SSH-Sitzung von iOS zum Server vom 19.10.2026 berichtet. Das belegt **nicht** Port, tatsächlich verwendete Route oder die übrigen Zugriffspfade. Der Ollama-Planer am Server bleibt `qwen3.5:4b-q4_K_M`; `OLLAMA_MAX_LOADED_MODELS=1` wurde von Kaan beibehalten. Der Laptop mit LFM2.5 ist kein Security-Reviewer. Die exakte lokale Laptop-Modell-ID muss als Beleg ermittelt und gepinnt werden.

## 1. Gate A — bestehende Policy, Zugriffe und sichere Migration (B1, B2)

**Sperre:** Keine ACL-/Grant-/Tag-Änderung, solange alle folgenden Nachweise nicht privat erstellt und vom Operator geprüft sind.

1. **Policy sichern:** Administrator exportiert die **gesamte aktive** Tailnet-Policy (einschließlich `acls`, `grants`, `ssh`, `tests`, `tagOwners`, Gruppen/Hosts). Private Sicherung mit UTC-Zeit, SHA-256, sicherem Dateipfad, verantwortlichem Operator und dokumentierter Wiederherstellungsquelle; Volltext **nicht committen**. Eine separat redigierte Prüffassung ersetzt IPs/Hostnamen, Konten und Identifikatoren durch `<REDACTED>`/Rollen; Diff gegen Original privat überprüfen. **Aktueller Beleg: UNKNOWN**.
2. **Verkehrsmatrix vor der Änderung:** Für *jeden* existierenden Pfad Quellgerät/-rolle, Zielrolle, Port/Protokoll, tatsächlichen Netzwerkweg, vorhandene Policy-Regel, zuständigen Nutzer und Positivtest notieren. Keine fehlenden Werte schätzen:

   | Vorhandener Strom | Gewünschter Erhalt | Stand / nötiger Beleg |
   | --- | --- | --- |
   | iOS/Termius → Server-SSH | laufende und neue SSH-Sitzung müssen bleiben | Termius-Verbindung berichtet; Quellidentität, Tailnet-Pfad, Port und ACL-Regel **UNKNOWN**; `22/tcp` ist nur Prüfannahme |
   | Browser → Portal und Portal → Kern | Benutzerzugriff und etwaiger Server-zu-Server-Pfad müssen bleiben | Vercel-Browser- und Kern-Verbindung **getrennt** kartieren; Tailnet-Betroffenheit und Ports **UNKNOWN** |
   | Operatorgerät → Control Deck / Project Deck | Login, HTTP(S), Dashboard-Funktionen müssen bleiben | Server-Serve-Ports `443/8443` belegt, Zuordnung/Quellgeräte **UNKNOWN** |
   | Operator / Automationen → n8n | UI, Webhooks und benötigte Workflow-Ziele müssen bleiben | genaue Ein-/Ausgänge, Ports und Netzwerkweg **UNKNOWN** |
   | Sonstige heute genutzte Verbindungen | keine unbeabsichtigten Ausfälle | aus Policy/Logs und Operator-Checkliste ergänzen, derzeit **UNKNOWN** |

3. **Breitregel erkennen:** Jede bestehende `{"src":["*"],"dst":["*"],"ip":["*"]}`-Freigabe, Legacy-ACL `* → *` sowie jede *andere* überlappende Regel identifizieren. Zusätzlich Node-Sharing (geteilte Geräte), `autogroup:self` / `autogroup:member`, Exit-Node-/Subnet-Router-Erlaubnisse und jede indirekte Verbindung zum Laptop prüfen. Ein Weglassen des Legacy-ACL-Abschnitts darf nicht versehentlich eine Standard-Allow-all-Policy aktivieren. Tailscale-Grants sind **additiv**: Ein enger Grant entzieht keine bereits gewährte Erlaubnis. Ohne Beseitigung sämtlicher breiterer Freigaben kann ein `deny`-Policy-**Test** für Laptop:9443 nicht bestehen. `deny` in `tests` ist eine Erwartung, **keine aktive Deny-Regel**.
4. **Zielsatz privat vorbereiten:** Für alle belegten Bestandsströme explizite, möglichst enge Grants/ACLs vorsehen. **Erst zusätzlich** die neue Verbindung `<SERVER-DEVICE> → <LAPTOP-DEVICE>:tcp/9443` planen. Beispiel nur als *nicht anwendbares Teilschema* (kein vollständiger Policy-Ersatz):
   ~~~json
   {"grants":[
     {"src":["tag:pilot-server-device"],"dst":["tag:pilot-laptop-device"],"ip":["tcp:9443"]}
   ]}
   ~~~
   Tags sind **Platzhalter und nicht eingerichtet**. Sie repräsentieren das **ganze Gerät**, nicht den Pilot-Prozess. Das Zuweisen eines Tags kann Geräte-Eigentümerschaft und Key-Expiry-Verhalten ändern; vorher ausdrücklich prüfen und von Kaan genehmigen lassen. Ohne Tags muss eine bestehende, eindeutig geprüfte Selektorform gewählt werden.
5. **Simulation vor Speichern:** In einem privaten *gesamten* Policy-Entwurf die breite(n) Regel(n) **ersetzen**, ohne erhaltene Ströme zu verlieren; Positivtests für SSH, Portal, Deck und n8n sowie Server→Laptop:9443; Negativtests für iOS/sonstige Peers→Laptop:9443, alle Peers→Laptop:11434 und nicht benötigte Ports. Die Admin-Konsole/Policy-Tests müssen den **vollständigen** Zielzustand validieren. Bei rotem Test: kein Speichern. Zusätzliche Regeln neben `* → *` sind **keine** wirksame Verengung.
6. **Anti-Lockout-Gate:** Separat erreichbare Admin-Konsole und dokumentierter Out-of-Band-Zugang prüfen (z. B. Provider-Konsole, nicht nur SSH via Tailnet); Operator während eines **gesondert genehmigten** Wartungsfensters bereithalten. Erst nach erfolgreich gesicherten Artefakten, vollständigen positiven/negativen Tests und Bestätigung des Wiederherstellungswegs darf Kaan **separat** eine Policy-Änderung freigeben. Danach *von echten Clients* alle Bestandsströme erneut testen. Bei einer Regression **sofort** die exakte gesicherte Vorher-Policy über die bestätigte Admin-Wiederherstellung restaurieren und betroffene Ströme prüfen. Nicht mehrere ungetestete Teiländerungen verteilen.
7. **Rollback-Nachweis:** Hersteller dokumentiert Policy-Diffs in Configuration Audit Logs (90 Tage) und das Zurücksetzen von Policy-Änderungen über diese Oberfläche. **Account-spezifische Revision und konkrete Wiederherstellung: UNKNOWN bis Betreiber es im eigenen Admin-UI prüft**. Falls nicht nachgewiesen: STOP, keine Policy-Verengung. Nicht blind auf ein vermeintlich vorhandenes History-Feature vertrauen.

**Gate A: HOLD**, bis Export-Hash, redigierter Diff, ausgefüllte Matrix, vollständige Testmatrix und getesteter Policy-Rückweg privat vorliegen. In der öffentlichen PR-Beschreibung nur die redigierten Belegkennungen/Ergebnisse referenzieren.

## 2. TLS- und Transportentscheidung (B3) — Kaan wählt genau eine Variante

**Gemeinsame Bedingungen:** Nur Tailnet, **kein Funnel**, explizite Quell-/Zielregel und Proxy-Secret; Server→Laptop:9443 → Proxy `127.0.0.1:11435` → lokales Ollama `127.0.0.1:11434`. Ein IP-only-HTTPS-Client darf **nicht** die Zertifikatsprüfung abschalten, um Variante A zu erzwingen. Weder eine Proxy- noch eine ACL-Änderung ist hier genehmigt.

### A: HTTPS zum Namen, Zertifikatsprüfung (Entscheidung ausstehend)

- Konzept: `tailscale serve --https=9443` leitet an lokalen HTTP-Proxy weiter; Server-Client verwendet exakt den genehmigten MagicDNS/FQDN `<LAPTOP-TLS-NAME>` mit normaler Zertifikatsketten- **und Hostnamenprüfung** (SNI). Kein `verify=False`, keine Umleitung, kein stiller Fallback auf IP/Klartext.
- **Vorteile:** zweite Transport-Authentisierung zusätzlich zu Tailscale; gut prüfbare TLS-Identität; Standard-HTTPS-Client.
- **Nachteile:** DNS-/Namenszuordnung wird Vertrauensanker; Anpassung des Clients erforderlich (`tools/ipad_llm.py` ist derzeit IP-HTTP-spezifisch); Zertifikate/Erneuerung nötig. Aktivieren von Tailscale-HTTPS-Zertifikaten ist **tailnetweit** und Zertifikatsnamen werden in öffentlichen Certificate-Transparency-Protokollen sichtbar. Vorher Namen auf Vertraulichkeit prüfen.
- **GO-Vorbedingung:** Kaan entscheidet A ausdrücklich und genehmigt *gesondert* mögliche Aktivierung des tailnetweiten HTTPS-Schalters und die CT-Sichtbarkeit. Bestehenden HTTPS-Zustand privat belegen. Kein Aktivieren durch implizite CLI-Interaktion.

### B: HTTP über WireGuard-Tailnet mit IP-Pinning (Entscheidung ausstehend)

- Konzept: Raw-TCP-Tailscale-Serve-Port `9443` wird zum lokalen HTTP-Proxy `127.0.0.1:11435` weitergeleitet (Beispielsyntax `tailscale serve --tcp=9443 tcp://127.0.0.1:11435`). Server-Client pinnt privat **Tailscale-IPv4 UND Node-ID UND Node-Public-Key** des exakt freigegebenen Laptops. **Vor jeder Anfrage** sind Tailnet-Onlinezustand, die eindeutige Zuordnung aller drei Werte im selben Peer-Record von `tailscale status --json` und der echte `tailscale0`-Egress aus `ip route get` zu bestätigen; bei Rotation/Re-Enrollment/Mismatch **fail closed**, neue Identität nur nach separater Freigabe pinnen. Die vom Gerät gemeldete OS-Angabe „Windows“ ist nur ein Zusatzindikator, **kein** Identitätsanker. Der Client bindet den Socket an `tailscale0`, deaktiviert HTTP-Proxys und Redirects — analog zu `tools/ipad_llm.py`, aber ohne den dortigen iOS-Only-Filter. Keine Tailscale-IP, Node-ID oder Node-Public-Key wird öffentlich dokumentiert.
- **Vorteile:** funktioniert konzeptionell ohne Namens-/Zertifikatsvergleich; entspricht bestehendem IP-Pinning-Prüfmuster; keine TLS-Validierung im HTTP-Client.
- **Nachteile:** kein zusätzlicher TLS-Endpunkt-Nachweis über WireGuard-Geräteidentität hinaus; Peer-/Identitäts-/Routenprüfung muss vor **jeder** Anfrage bestehen. `SO_BINDTODEVICE` wird im Pilot ohne zusätzliche Linux-Capabilities versucht; schlägt die Bindung fehl, ist das ein **HOLD**, kein Anlass, `CAP_NET_RAW`, `AmbientCapabilities` oder Root-Rechte still zu vergeben. Keine öffentlichen HTTPS-Zertifikate für diesen Endpunkt *erforderlich*, ABER: Die aktuelle Tailscale-Serve-Dokumentation nennt HTTPS-Zertifikate als **allgemeine Serve-Voraussetzung**. Ob `--tcp`/`--http` auf dem konkreten Tailnet **ohne tailnetweiten Schalter** nutzbar ist, ist **UNKNOWN** und darf nicht unterstellt werden.
- **GO-Vorbedingung:** Kaan entscheidet B ausdrücklich; vor Betrieb wird ohne Konfigurationsänderung geprüft, ob B ohne Aktivierung möglich ist. Falls der Befehl eine Aktivierung verlangt: **STOP, eigene GO-Entscheidung**, kein stiller Wechsel zu A.

**Empfehlung für P2-Durchführung, keine Freigabe:** Vorziehen von **B**, *nur sofern* die betriebliche Voraussetzung ohne neue tailnetweite Änderung positiv belegbar ist; sonst A nach ausdrücklichem separatem Zertifikats-/CT-GO. B reduziert die erforderliche Client-Umstellung, ersetzt aber keinesfalls das zusätzliche Proxy-Secret oder die Tailnet-Zugriffsregel.

## 3. Gate B — Proxy-Vertrag und überprüfbare Ablehnungen (B4)

### Schnittstellen und vollständige Allowlist

- Ein neuer, von **Codex** erst nach gesondertem GO zu implementierender lokaler Prozess bindet **ausschließlich `127.0.0.1:11435`** (kein `0.0.0.0`, keine LAN-/Tailnet-Bindung, keine IPv6-Wildcard). Ollama bleibt auf `127.0.0.1:11434`. Kein direkter 11434-Forward, kein Funnel. Windows-Firewall-Regeln nicht pauschal öffnen.
- Exakte HTTP-Pfade/Methoden: **nur `POST /api/chat` oder `POST /api/generate`** mit identischem Schutzvertrag; für den P2-Piloten ist `/api/chat` vorzuziehen. `/api/pull`, `/api/delete`, `/api/create`, `/api/copy`, `/api/push`, `/api/tags`, alle sonstigen Pfade und alle anderen Methoden **nie** nach außen weiterleiten. Insbesondere `GET /api/tags` sowie `OPTIONS` und `HEAD` bleiben gesperrt. Keine Präfixregeln, keine Query-Strings, keine URL-kodierten Pfad-Aliasse, keine Slash-/Case-Aliasse, keine Redirects. **Keine CORS-Antwortheader**, keine CORS-Preflight-Freigabe. `Host` muss dem **einzigen privat konfigurierten, modusabhängigen erwarteten Host:Port** entsprechen (A: genehmigter TLS-FQDN:9443, B: gepinnte Tailnet-IP:9443); die lokale Proxy-Bindung ist kein Grund, beliebige Hostnamen zu akzeptieren. Ohne nachgewiesene Host-Weitergabe von Serve ist Gate C HOLD. DNS-Rebinding ist negativ zu testen.
- Proxy implementiert **keine** Durchleitung eines beliebigen Ollama-Bodys: requestbezogenes JSON streng parsen, validieren und aus **expliziten** Whitelist-Feldern neu aufbauen; er kann Ollama ausschließlich die beiden Inferenz-Endpunkte senden. Keine **vom Client gelieferten** `tools`, `format`, `think`, `raw`, `system`, `template` oder **irgendein unbekanntes Feld**; auch keine nicht erlaubten Unterfelder. `tool_calls`/`function_call`, JSON-Schema-Zwang und strukturierte Ausgabemodi sind nicht zulässig. Der Proxy setzt **selbst** upstream `think:false` als nicht vom Client beeinflussbare Konstante; mit dem exakten Modell und Ollama 0.40.6 muss vor GO belegt werden, dass dieser Wert akzeptiert und kein unerlaubter Thinking-Text ausgegeben wird. Sonst bleibt der betreffende Endpoint deaktiviert (HOLD).
- Gemeinsame Top-Level-Whitelist: `model`, `stream`, `options`, `keep_alive`, und je Endpoint **entweder** `messages` (chat) **oder** `prompt` (generate). `model` muss mit der **einzigen konfigurierten exakten** Laptop-Modell-ID übereinstimmen (ID derzeit **UNKNOWN**; ohne Beleg startet kein Pilot); keine Aliasse/Cloud-Fallbacks. `stream` **muss genau `false` sein** (Typ Boolean, kein String); upstream ebenfalls explizit `false`. `options` höchstens `num_ctx`, `num_predict` und `temperature`, keine weiteren Options-/Sicherheits-/GPU-Felder.
- Chat: `messages` ist **genau ein** Objekt `{"role":"user","content":"<TEXT>"}`, ohne zusätzliche Keys; keine eingebetteten Bilder, Tools, System-/Assistant-/Tool-Rollen. Generate: `prompt` ist nur nichtleerer UTF-8-Text. Die Proxyschicht interpretiert den Inhalt **nicht** als Anweisung.
- **Explizite Werte-Grenzen:** `num_ctx`: Integer 512–4096 (Standard 2048), `num_predict`: Integer 1–256 (Standard 128), `temperature`: Integer/Float 0.0–1.0 (Standard 0), `keep_alive`: Integer 0–120 Sekunden (Standard 0). In Python bool strikt **nicht** als int akzeptieren. Werte außerhalb oder Sonderzahlen (NaN/Infinity) **ablehnen**, **nicht** still clampen. Ein nicht spezifizierter Optionalwert wird vom Proxy selbst auf sicheren Standard gesetzt; keine freien Felder an Ollama weiterreichen.
- **Ressourcenlimits** (Startwerte, noch nicht empirisch auf Laptop verifiziert): **max. 4 gleichzeitig offene TCP-Verbindungen insgesamt** (einschließlich Header-/Body-Phase; 5. sofort abbrechen), max. 32 Request-Header; gesamter Headerblock max. 8 KiB **einschließlich weitergeleiteter Serve-Header**; `Content-Length` zwingend und max. 16 KiB; Prompt max. 8 KiB UTF-8; Antwort-JSON max. 64 KiB und Modelltext max. 32 KiB UTF-8; Request-Kopf-/Body-Frist je 5 s, upstream Inferenz-Fenster max. 90 s, absolute End-to-End-Deadline max. 120 s; max. **1** Inferenz zugleich, **keine Warteschlange**. Bei Grenzverletzung, unvollständigem Body, Timeout oder belegtem Slot: fail closed. Unbekannte Chunked-/Transfer-Encoding, Content-Encoding, inkompatible Content-Type, Duplikat-JSON-Keys sowie doppelte `Host`/`Content-Length`/`Transfer-Encoding`/`Authorization`-Header (case-insensitiv) ablehnen. Erhöhen der Limits erfordert Messung und Review; der Server-Wechselbetrieb `MAX_LOADED_MODELS=1` bleibt unverändert.
- **Header-Vertrauensgrenze:** `X-Forwarded-*`, `Tailscale-User-*` und ähnliche Proxy-Identitäts-Header dürfen **nie** zur Anmeldung oder Autorisierung dienen; sie zählen trotzdem zum Header-Budget. Der Proxy vertraut allein dem geprüften Bearer-Secret und dem getrennten Pilot-Auftragsvertrag. Vor GO Host- und Headerverhalten gegen echte Serve-Verbindungen nachmessen.
- **Logging:** Nur UTC-Zeit, erlaubter Pfad/Methodencode, HTTP-Status, feste Refusal-Reason-ID, Dauer und Byte-Zähler; **niemals** Prompt, Antwort, Secret, Bearer-Header, Node-ID oder Peer-IP. Lokale begrenzte Rotation und maximal **7 Tage** Aufbewahrung als Entwurfsziel; separater Test auf redigierte Logs und deren Berechtigungen.
- **Auth:** Zusätzlich zur Geräte-ACL ein separates, kryptografisch zufälliges (später **nur nach GO** erzeugtes) 256-Bit-Proxy-Secret, niemals Git/URL/Argument/Log. Ein `Authorization: Bearer <SECRET>`-Header, genau einmal; fehlendes, falsch kodiertes, falsches, leeres oder doppeltes Secret wird vor jeglichem Ollama-Aufruf abgelehnt. Geheimnisdateien an beiden Enden mit Besitzer und Modus `0600`; Verzeichnis nur für den jeweiligen Dienstnutzer. Parser begrenzt Länge vor Vergleich; Wertevergleich mit `hmac.compare_digest` oder geeignetem Äquivalent in **konstanter Zeit** für gültig geformte, gleich lange Token; keine Verzweigung aufgrund des ersten abweichenden Bytes. Bei Ablehnung nur generische Fehlermeldung, nie Secret oder Prompt protokollieren.
- **Antwort:** nur validierter nicht-streamender Ollama-Abschluss mit exakter Modell-ID, `done=true`, Assistant-Rolle bei Chat, Textlänge begrenzt, kein `tool_calls`/`thinking`/`function_call`. An Pilot nur reinen Antworttext und minimale Metadaten/Digests, keine rohen internen Objekte. Leere, unvollständige, ungültige, überlange oder falsch zugeordnete Antworten werden verworfen. Kein Aufruf von GeniusNew-Gateway, Approvals, Tools oder OS-Kommandos durch das Modell.

### Gate-B-Testkatalog (jedes Refusal einzeln rot/grün)

**Implementierungsauftrag, nicht ausgeführt**: Fake-Ollama, Fake-Serve und isolierter HTTP-Client; für **jede** Ablehnung Test, der ohne Guard fehlschlägt (Refusal-Guard-Stil, `AGENTS.md`). Getesteter Fake-Ollama muss bei jedem negativen Fall **0 Upstream-Aufrufe** sehen, soweit vor der Weiterleitung entscheidbar.

| ID | Negativ-/Grenzfall | Soll |
| --- | --- | --- |
| B01 | GET/PUT/DELETE/HEAD/OPTIONS, auch `GET /api/tags`, CORS-Preflight | refuse, kein Forward, keine CORS-Header |
| B02 | `/api/pull` | refuse |
| B03 | `/api/delete` | refuse |
| B04 | `/api/create` | refuse |
| B05 | `/api/copy` | refuse |
| B06 | `/api/push` | refuse |
| B07 | unbekannter Pfad, Query, encoded Slash, Slash-Suffix, Case-Alias | refuse je Unterfall |
| B08 | fehlende/falsche/doppelte Authorization; unpassende Länge | refuse, constant-time-Vergleich bei formgerechtem Token |
| B09 | zusätzliches `tools`-Feld | refuse |
| B10 | zusätzliches `format`-Feld | refuse |
| B11 | unbekanntes Top-Level-/Option-/Message-Feld; clientseitiges `think`/`raw`/`template` | refuse je Feldklasse |
| B12 | `stream=true`, `"false"`, `null`, fehlend | refuse; gültiger Fall muss exakt `false` weitergeben |
| B13 | `num_ctx` unter/über Grenze, Bool, Float, String | refuse je Typ/Grenze |
| B14 | `num_predict` unter/über Grenze, Bool, Float, String | refuse je Typ/Grenze |
| B15 | `keep_alive` unter/über Grenze, Bool, String | refuse je Typ/Grenze |
| B16 | `temperature` außerhalb, NaN/Infinity oder Bool | refuse |
| B17 | andere Modell-ID, Alias, fehlendes Modell | refuse |
| B18 | System-/Assistant-/Tool-Rolle, mehrere Nachrichten, non-text, Bilder | refuse |
| B19 | zusätzliches `prompt` bei Chat / `messages` bei Generate | refuse |
| B20 | doppelte JSON-Keys, JSON-Müll, falscher Content-Type, Content-Encoding | refuse |
| B21 | doppelte Host-/Content-Length-/Transfer-Encoding-/Authorization-Header, Chunked-Encoding | refuse je Header; Connection schließen |
| B22 | Headeranzahl/-größe, Body-/Prompt-Limit, fehlende/ungültige Länge, abgeschnittener Body | refuse je Limit |
| B23 | langsamer Header/Body, upstream Timeout, absolute Deadline | refuse, begrenzte Zeit |
| B24 | zweiter gleichzeitiger Request | refuse ohne Queue; aktiver Request bleibt isoliert |
| B25 | Upstream falsches Modell / Rolle / `done` / Tool-Calls / Thinking | Antwort verwerfen, kein Pilotauftrag |
| B26 | Upstream kaputtes, leeres, überlanges oder streamendes Ergebnis | Antwort verwerfen |
| B27 | Proxy bindet an LAN-/Tailnet-/IPv6-Wildcard, Ollama bindet außerhalb von Loopback (Start **und Laufzeit**), oder Ollama wird direkt weitergereicht | Start/Integration/laufender Pilot verweigert |
| B28 | HTTP-Redirect oder unerwarteter Proxy-/Netzpfad | verweigern, kein Fallback |
| B29 | Fünfte simultane Verbindung, Slowloris-Header/Body, volle Slots | sofort schließen; bestehende 4 max. und Deadlines bleiben wirksam |
| B30 | vom Client gesetztes `think`, falscher upstream-Think-Wert, unzulässiges Host:Port, DNS-Rebinding, fehlende CORS-Sperre | jede Variante fail closed; upstream erzwungenes `think:false` nachweisen |

**Positivtest:** genau ein autorisierter `POST /api/chat` mit streng validiertem JSON, exakter Modell-ID und `stream=false` führt zu genau einer Ollama-Inferenz, gibt ausschließlich begrenzten Text zurück. Dokumentations-PR enthält **keinen** Code und meldet keine Tests als ausgeführt.

## 4. Gate C — Tailscale-Route, negativer WLAN-Test, selektiver Rollback

**Nur nach eigenständigem Betriebs-GO; folgende Befehle sind ausschließlich Beispiele, jetzt NICHT ausführen.** Vor Start: tatsächlichen `tailscale version`- und `tailscale serve status --json`-Stand, Ziel-Portfreiheit, bestehende Serve-Routen, `tailscale funnel status` und genaue CLI-`off`-Syntax privat sichern. Auf dem Laptop wurde die Tailscale-Version lesend festgestellt und bewusst nicht öffentlich ausgeschrieben; ein korrektes Rücknahmekommando muss vor Freigabe **an der privat dokumentierten installierten Version** gegen Hilfe/Herstellerbeschreibung verifiziert werden.

Variante A (nach ausdrücklicher Entscheidung und TLS-GO), Windows als Administrator:

~~~powershell
tailscale serve --bg --https=9443 http://127.0.0.1:11435
# Rollback NUR dieser Serve-Route:
tailscale serve --https=9443 off
~~~

Variante B (nach ausdrücklicher Entscheidung und Nachweis, dass TCP-Serve ohne zusätzliche globale Änderung zulässig ist), Windows als Administrator:

~~~powershell
tailscale serve --bg --tcp=9443 tcp://127.0.0.1:11435
# Rollback NUR dieser Serve-Route:
tailscale serve --tcp=9443 off
~~~

**Kein** `tailscale serve reset`, `tailscale down`, `tailscale set`, `tailscale funnel` oder pauschales Entfernen vorhandener 443-/8443-Serverrouten. Falls für 9443 bereits eine andere Route besteht: **HOLD**, nicht überschreiben. Nach Route-Off müssen alle *anderen* vorher belegten Serve-Routen unverändert vorhanden sein.

Nachrichtlich geplanter Gate-C-Test mit redigierten Belegen:

1. Laptop: `tailscale serve status --json` zeigt genau die freigegebene Route, `tailscale funnel status` **keinen** öffentlich getunnelten Port; Proxy nur `127.0.0.1:11435`, Ollama nur `127.0.0.1:11434`.
2. Vom getrennten **Server-Pilot-OS-Nutzer**: nur korrekte Anfrage auf `9443` ist möglich; falsches Secret, falsche Modell-ID und verbotene Pfade werden abgelehnt; DNS/TLS-Zertifikat (A) bzw. Windows-Peer+IP/Route (B) wird verifiziert.
3. Von iPad, fremdem Tailnet-Peer und unberechtigtem Gerät: `9443` ist nicht erreichbar; `11434` bleibt von außen unerreichbar. Unter Variante B wird außerdem ein bewusst veränderter Node-ID-/Public-Key-/IP-Pin vor der HTTP-Anfrage mit **0** Secret-Übertragungen abgelehnt. Ergebnis nicht allein aus `serve status` behaupten.
4. **WLAN/LAN-Negativtest:** Von einem *anderen Gerät im gleichen lokalen WLAN/LAN*, mit Tailscale für diesen Test deaktiviert und auf die **lokale LAN-Adresse des Laptops** gerichtet, dürfen **weder `9443` noch `11435` noch `11434`** erreichbar sein. Für lokalen Bericht nur Port/Resultat, **keine** echten IPs im Repo. Wenn ein Port erreichbar ist: FAIL und Rollback.
5. Nach einem **genehmigten** Neustart des Laptops erneut prüfen, dass die Hintergrund-Serve-Route ausschließlich wie freigegeben besteht und Funnel aus ist; ein unerwarteter Zustand führt zu HOLD. Selektiver Rollback nur der genehmigten `9443`-Route: Port danach unerreichbar, vorhandene Routen erhalten, Bestandsströme SSH/Portal/Deck/n8n weiterhin erreichbar. Die Policy wird nur dann zurückgenommen, wenn sie in einem **eigenen** GO geändert wurde, über den vorher bestätigten Revision-/Restore-Weg.

**Gate C bleibt HOLD**, bis echte Positiv-/Negativtests auf den Geräten inklusive WLAN und Rückweg dokumentiert sind.

## 5. Gate D — separater OS-Nutzer, Geheimnisse und Rotation (B5)

- Server: eigener **dedizierter OS-Nutzer** `<PILOT_OS_USER>` für `tools/ai_pilot`, strikt verschieden vom belegten Actions-Runner-Nutzer `<RUNNER_OS_USER>` und von Core-/DB-/Anker-Nutzern; keine Gruppenmitgliedschaft, die Runner-/Core-Secrets lesbar macht; `nologin` und kein sudo; eigener kontrollierter Dienstprozess mit `NoNewPrivileges`, enger Dateisystem- und Netzwerkberechtigung. Ein Tailscale-Grant identifiziert weiterhin **das Servergerät**, nicht den Prozess: die zweite Anwendungsauthentisierung ist zwingend.
- **Zusätzliche Option, kein Bestandteil dieses Entwurfs-GO:** eine zielgenaue lokale `nftables`-Ausgangsregel mit `meta skuid` könnte Zugriffe auf Laptop:9443 auf den Pilot-OS-Nutzer begrenzen. Vor Entscheidung Interaktion mit Tailscale-Routing, Erreichbarkeit von Ollama und vorhandenem Firewall-Regelsatz sowie eigenständigen Rollback prüfen; nur nach separatem Kaan-GO, **keine** Firewalländerung hier. Ein Tailscale-Grant allein bietet keine Prozessisolation.
- Server-Secret-Datei: owner `<PILOT_OS_USER>`, Mode `0600`, privates übergeordnetes Verzeichnis `0700`. Laptop-Proxy-Secret: ausschließlich Windows-Dienstidentität mit NTFS-ACL, keine Lesbarkeit für allgemeine Benutzer oder Runner; Windows benutzt kein POSIX-`0600`. Kein Token in Kommandozeilenargumenten, Environment-Dumps, CI-Artefakten, Logs, Repository oder Shell-History.
- **Prüfweg vor einer separaten Umsetzungsfreigabe** (hier nicht ausgeführt): `ps -eo user,args | grep -i '[r]unner'`; `systemctl show <PILOT_UNIT> -p User -p NoNewPrivileges`; `id <PILOT_OS_USER>`; `stat -c '%U %a %n' <PRIVATE_SECRET_FILE>` (nur Metadaten); als Runner-Nutzer negativer `test -r <PRIVATE_SECRET_FILE>`, als Pilot-Nutzer positiver Lesetest **ohne** Wertausgabe; `systemd-analyze security <PILOT_UNIT>` als ergänzender Check. Auf Windows `Get-Acl <PRIVATE_SECRET_FILE>` zur Rechteprüfung, ohne Dateiinhalt. Prüfen, dass keine gemeinsam lesbare Kopie existiert. Alle Ergebnisse privat ohne Nutzernamen/Dateipfade in PR redigieren.
- **Rotation, gesondertes Kaan-GO erforderlich:** (1) Verantwortlichen, Wartungsfenster, aktuellen Secret-Bezeichner/Fingerprint und Rollback-Plan privat sichern, **keinen Secretwert**; (2) Pilot stoppen und keine neuen Requests zulassen (fail closed), vorhandene Requests auslaufen/abbrechen; (3) erst nach GO ein neues unabhängiges 256-Bit-Secret über CSPRNG generieren, nur in private Dateien schreiben; (4) Proxy- und Pilot-Seite mit Dateisystemrechten und Versionskennung vorbereitet atomar aktualisieren, ohne beide Keys gleichzeitig unbegrenzt zu akzeptieren; (5) zuerst Proxy, dann Client starten, Positivtest mit neuem Secret, **Negativtest mit altem**; (6) alten Key nach verifiziertem Erfolg aus aktiven Stores entfernen, Logs nur mit Metadaten; (7) bei Fehlfunktion Verbindungen stoppen und entweder in einem **neu autorisierten** Vorgang den geprüften alten Stand aus gesicherter privater Quelle wiederherstellen oder fail closed bleiben. Nie Schlüssel oder Freigaberechte zurücksetzen. Separater Ablauf für Kompromittierung: sofort sperren und incident response, kein stiller Rückgriff auf potenziell kompromittierten alten Key.
- Bestehender CI-/Actions-Runner darf das Secret nicht lesen; der Proxy erhält keine GitHub-/DB-/Signier- oder Freigaberechte. Das Modell kann keine Rolle, Approval, Signatur oder Deployment ausüben.

**Gate D: HOLD**, bis Nutzer-, Gruppen-, Prozess- und Secret-Permissions-Nachweise sowie Rotations-Rollback genehmigt und getestet sind.

## 6. Gate E — Pilot-Code, nicht Proxy: Prompt-Injection und Auftragsgrenzen

Der Client/pilot verwirft bei Variante B jede nicht passende Node-ID, jeden nicht passenden Node-Public-Key und jede IP-Rezuordnung vor Auth-/Secretübertragung. Er arbeitet ohne zusätzliche Linux-Capabilities und verweigert die Bindung, falls sie nicht gelingt. Negativtests dazu gehören **in den Pilot-Code** und nicht in die Proxy-Testtabelle.

Prompt-Injection ist **kein** sinnvoller Proxy-Inhaltsfilter. Der Proxy muss lediglich Text als Daten durchleiten und gefährliche *API-Felder* ablehnen. Die deterministische Behandlung von untrusted Text gehört in die gesonderte Pilot-Code-PR von Codex (Issue #166): nur `task_id` (einmaliger 128-Bit-Wert), `helper ∈ {laptop, ipad}`, `kind ∈ {summary, code_draft}` und `input`; genau ein Helferauftrag; alle übrigen Felder verweigern, Antworten niemals als Tool-Call, Approval, Signatur, Commit, Merge oder Deployment behandeln. Negativtests im Pilot-Code: Prompt verlangt Override, Tool-Aufruf oder Selbstfreigabe; gefälschte Antwort/Feld, Replay-/Doppelantwort, abweichende Modell-ID, Timeout, kaputtes Ergebnis, Netzfehler. Dokumentation verweist nur darauf; **kein Pilot-Code und keine Tests** werden in diesem PR verändert.

## 7. GO-Liste — alles ausdrücklich separat, keine stillen Nebenwirkungen

| Gegenstand | Erforderliche Entscheidung/Evidenz | Stand |
| --- | --- | --- |
| Gate A: Policy-Sicherung, aktuelle Zugriffe, positives+negatives Policy-Testset, Revisions-Rückweg | Belege, keine Sperrung des Operators | **HOLD** |
| Transport **A oder B** | **Kaan wählt genau eine Variante**, nicht ChatGPT/Claude | **OFFEN** |
| Aktivieren von HTTPS-Zertifikaten **tailnetweit** | Kaan-GO auch dann, wenn der CLI-Aufruf dies anbietet; bestehender Zustand prüfen | **OFFEN/UNKNOWN** |
| Veröffentlichung von Laptop-/Tailnet-Namen in **Certificate Transparency** | Kaan-GO zur Offenlegung der FQDNs; vorab Namen vertraulich prüfen | **OFFEN** |
| Neuvergabe von Tag-Eigentümern/Tag-Nutzung/Key-Expiry-Effekt | eigene belegte Prüfung und Kaan-GO | **OFFEN** |
| Proxy-Implementierung und Refusal-Tests, neue Prozess-/Netzgrenze | Codex, danach unabhängiges Claude-Review am **exakten neuen Code-Head** und Gemini-Zweitmeinung | **NICHT BEAUFTRAGT** |
| Dedizierter Server-Pilot-OS-Nutzer, Dienst/NTFS-/Unix-Berechtigungen | separate Betriebsfreigabe, Rechte-Tests | **HOLD** |
| Secret-Erzeugung, Ablage, Rotation | **eigene ausdrückliche** Freigabe von Kaan, kein Key in PR | **HOLD** |
| Serve nur für Laptop:9443, keine Funnel-Freigabe, Rollback | gesondertes GO, Version/Status vorher/nachher | **HOLD** |
| Änderung an aktiven ACLs/Grants/Tags, Firewall, SSH, systemd, Ollama-Bindung | **nie** aus diesem Dokumentations-GO ableiten | **HOLD** |
| Gate C inkl. WLAN/LAN-Negativtest und Bestandsdienste | reproduzierbare Gerätetests mit redigierten Belegen | **HOLD** |
| Gate E Pilot-Code, Negativtests, Prompt-Injection und Node-ID/Public-Key-/Routen-Pinning | gesonderter Code-PR mit Review | **HOLD** |
| Optionales OS-UID-Egress-Filtering via nftables | nur bei nachgewiesener Notwendigkeit, eigene Firewall-/Rollback-Freigabe | **NICHT GENEHMIGT** |
| `SECURITY.md`-Eintrag für neue Server→Laptop-Netzgrenze | separater von Kaan zu genehmigender PR | **OFFEN** |
| Merge, Deployment, Release, Tag | nur Kaan nach allen unabhängigen Gates | **KEIN GO** |

Die reine Erstellung dieses Draft-PR **gibt nichts frei**. Claude führt als unabhängiger Prüfer den erneuten Sicherheitsreview **am exakten aktuellen vollständigen Commit-SHA dieses PR** durch. Nach `CHANGES REQUESTED` bleibt der Entwurf HOLD; selbst bei positivem Review trifft nur Kaan die offenen Entscheidungen.

## 8. Hersteller-/Projektquellen (Dokumentationsstand)

- Review-Evidenz: [Issue #166, Kommentar 6257014548](https://github.com/Kaancodm/GeniusNew/issues/166#issuecomment-6257014548); Rollen/Fail-Closed: `AGENTS.md`, `docs/COLLABORATION.md`, `SECURITY.md`.
- Bestehendes IP-Pinning: `docs/IPAD-LLM.md` und `tools/ipad_llm.py` (kein getrennter HTTPS-Zertifikatpfad, Windows-Peerprüfung ist neu zu spezifizieren).
- [Tailscale Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve): `--https`, `--tcp`, `off` für gezielte Route; **nicht** `reset`; konkrete Version vor GO prüfen.
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve): HTTPS-Tailnet-Voraussetzung und Grenzen von Funnel; B ohne globalen Schalter nicht als Tatsache darstellen.
- [Tailscale HTTPS-Zertifikate](https://tailscale.com/docs/how-to/set-up-https-certificates): Certificate Transparency publiziert Zertifikatsnamen; globales Aktivieren ist gesonderter Vorgang.
- [Tailscale Grants](https://tailscale.com/docs/reference/syntax/grants): Grants sind additiv, keine einschränkende Überlagerung einer Wildcard.
- [Tailscale Konfigurations-Audit](https://tailscale.com/kb/1203/audit-logging/): Policy-Diffs/90-Tage-Log; [ACL-Beispiele](https://tailscale.com/docs/reference/examples/acls): Policy-Änderungen können aus Konfigurationslogs zurückgesetzt werden; Account-Option bleibt bis Geräte-/Admin-Beleg UNKNOWN.

**Abgrenzung:** Dies ist ausschließlich ein Design-Vertrag und keine Anleitung zur unmittelbaren Aktivierung. Kein lokaler Produktionsdienst, keine Route, kein Zertifikat, kein Secret und keine aktive Policy wird hier verändert.
