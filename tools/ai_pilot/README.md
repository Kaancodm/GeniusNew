# Lokaler KI-Pilot P1 (nur Server)

**Status:** experimentelles Operator-Werkzeug, standardmäßig AUS. Kein Dienst,
kein LLM-Worker und keine Integration in das Core-Paket. Keine neue
Netzwerkgrenze; beide Aufrufe gehen nur an POST http://127.0.0.1:11434/api/chat.
Kein alternativer Endpoint, kein anderes Modell, kein Cloud-Fallback.

## Vertrag

1. Planer: exakt qwen3.5:4b-q4_K_M; danach lokaler Helfer: exakt
   qwen2.5-coder:1.5b. **Nacheinander**, nie parallel. Helferwert in P1:
   ausschließlich server. Laptop (P2) und iPad (P3) sind ausgeschlossen.
2. Der Server erzeugt eine 128-Bit-task_id. Im Plan sind nur task_id, helper,
   kind und input erlaubt. kind ist summary oder code_draft. Alle Werte
   müssen mit dem ursprünglichen Auftrag übereinstimmen. Extrafelder,
   andere Helfer und fremde/erneute IDs führen zur Ablehnung.
3. Die Helferausgabe ist **nur Text**. Angebliche Admin-Anweisungen darin
   besitzen keine Rechte. Weder Shell noch Freigaben, Signaturen, Tools,
   Dateien, Deployments oder Core-APIs sind an den Pilot angebunden.
4. Ein Thread-Lock und ein pro Linux-UID eindeutiger abstrakter Unix-Socket
   sperren den Pilot-Slot bis beide Aufrufe abgeschlossen sind. Bei Belegung
   sofort REFUSE SLOT_BUSY. Das legt keine Datei an und öffnet keinen
   Netzwerkport. Bestehende Einstellungen OLLAMA_MAX_LOADED_MODELS=1,
   OLLAMA_NUM_PARALLEL=1, OLLAMA_KEEP_ALIVE=2m sowie das cgroup-Limit bleiben
   unverändert. Andere, nicht am Pilot beteiligte Ollama-Clients werden
   durch dessen Lock **nicht** kontrolliert.
5. Grenzen: Auftrag 4096 UTF-8-Bytes, Antwort 4096 UTF-8-Bytes,
   HTTP-Envelope 16384 Bytes, 120 Sekunden je Modell inklusive
   Kaltstart, num_ctx=2048, num_predict=256, temperature=0,
   stream=false, think=false. Fehler führen zu REFUSE ohne Fallback.
6. Task-ID und erwarteter Ergebnisdigest sind pro Prozess nur einmal gültig.
   Result-Submission ist **keine** externe API. Nach einem Neustart
   bleiben alte Task-IDs unbekannt; das ist kein persistenter Job-Ledger.
7. JSONL an stderr enthält nur Ereignis, Grundcodes, erlaubte Metadaten und
   SHA-256-Digests. Keine Prompts, Planinhalte, Antworten, Task-IDs oder
   Schlüssel. stdout enthält nur den finalen Antworttext. Das Protokoll
   ersetzt keine GeniusNew-Audit-Kette.

## Bedienung auf dem Server (bewusstes Opt-in)

    cd /pfad/zu/GeniusNew
    printf '%s' 'Fasse zwei harmlose Sätze zusammen.' |
      python3 -m tools.ai_pilot --enable --kind summary

Ohne --enable verweigert der Pilot den Lauf vor dem Ollama-Aufruf:

    printf '%s' 'Test' | python3 -m tools.ai_pilot

Keine geheimen oder produktiven Daten als Probe verwenden. Die CLI liest
nur stdin, führt keine Modellantwort als Befehl aus, bindet keinen Port und
ändert weder Tailscale noch Firewall, SSH, systemd, Ollama oder Deployment.

## Tests und Evaluation

    python3 -W error::ResourceWarning -m unittest discover -s tests -p test_ai_pilot.py -v
    python3 scripts/refusals.py tools/ai_pilot/pilot.py
    ./scripts/demo.sh

Optional: explizite Live-Evaluation mit zehn fest definierten, ungefährlichen
Aufträgen pro Modell (Modellwechsel benötigt zusätzliche Zeit):

    python3 -m tools.ai_pilot --enable --eval

Die CI-Suite nutzt einen lokalen HTTP-Fake-Server: zehn Planer- und zehn
Helferaufrufe in genau dieser Reihenfolge. Sie prüft Verträge und Ablehnungen,
**nicht** die inhaltliche Güte echter Antworten. Kein CI-/Installationsschritt
lädt Modelle nach.

Der Pilot autorisiert niemals Jobs. Für echte Aufgaben bleiben die
unveränderten GeniusNew-Approval-, Permit-, Verifier- und Audit-Gates
zuständig. Merge/Deployment braucht eine gesonderte Freigabe.
