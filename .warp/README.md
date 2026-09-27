# GeniusNew Warp Command Center

Die Repository-Workflows unter `.warp/workflows/` sind die schnelle Bedienoberfläche für
GeniusNew. Sie enthalten keine Secrets und führen keine Merges, Deployments oder
Credential-Rotationen aus.

## Start

1. Warp öffnen.
2. Unter Windows für GeniusNew eine WSL2-/Ubuntu-Sitzung verwenden.
3. Das Repository `~/GeniusNew` öffnen.
4. `Ctrl+Shift+R` drücken und nach `GeniusNew` suchen.
5. Zuerst **GeniusNew - Status Ampel** ausführen.

## Ampel

- 🟢 bereit
- 🟡 Aufmerksamkeit nötig, aber kein harter Fehler
- 🔴 fehlt oder ist nicht erreichbar

Die Ampel nennt bei typischen Problemen direkt den nächsten Workflow oder Befehl.

## Sicherheitsgrenze

Die Workflows dürfen lokale Entwicklungszustände prüfen und die im Repository
dokumentierten Tests ausführen. Serverstatus wird nur lesend über einen lokal
konfigurierten SSH-Alias abgefragt. Hostnamen, Schlüssel, Tokens und andere Secrets
gehören nicht in dieses Repository.
