# Betrieb und Recovery — Gate C5, prüfbarer Entwurf

Dieser Entwurf aktiviert keinen Dienst. B3/B4/B5/B6/B7, unabhängige Reviews und
der frische Host-Nachweis müssen am selben vollständigen Commit bestanden sein.
Der bestehende Server ist ein Entwicklungsserver. Der vorhandene PostgreSQL-
Testcluster ist keine Produktivdatenbank.

## Installationsvertrag

Die Installation hält vier Verantwortungen getrennt: Anker, Kern, Worker und
Portal. Ankerzustand und Anker-Antwortschlüssel gehören ausschließlich dem
Ankernutzer. Die Core-Runtime darf sie weder lesen, schreiben noch zurücksetzen.
Die gemeinsame Socketgruppe gewährt nur Kommunikation mit dem Anker.

Die Beispiel-Units liegen unter docs/examples/. Vor Aktivierung sind die Pfade,
der geprüfte vollständige SHA und der öffentliche Audit-Schlüssel einzusetzen.
Das Programm liegt unter /opt/geniusnew, Secrets in privaten Dateien außerhalb
von Git. Migrationen laufen separat mit der Migrationsrolle; der Kern bekommt
ausschließlich die geprüfte Runtime-DSN. Ein fehlendes Feld verhindert den Start.

Die Worker laufen heute noch als Kindprozess des Kerns. Eine eigene systemd-
User-Zeile allein ändert ihre Identität nicht. Der gesonderte Worker-OS-Nutzer
braucht einen tatsächlich unterstützten Startpfad und einen Negativtest.
Bis dahin ist lediglich das Landlock-Mindestziel belegt. Eine Verschiebung der
vollen Nutzertrennung ist nach ROADMAP-V02 ausdrücklich Kaans Entscheidung.

Vor der Aktivierung nachweisen:

1. Kern und Anker haben verschiedene UIDs; Kern kann Ankerdateien nicht öffnen.
2. Worker liest weder Root-Secret noch Runtime-DSN noch Ankerzustand.
3. Der Kern bindet nur an Loopback; TLS und äußere Limits liegen am Proxy.
4. Alle Tabellenrechte entsprechen DATABASE §10; Startprüfung verweigert
   Superuser, Owner, Rollen-Bypass und zusätzliche Rechte.
5. Der Anker läuft unabhängig. SIGTERM des Kerns beendet ihn nicht.
6. Die TOML enthält explizite HTTP-Limits; dieselben Werte gelten am Proxy.

## Backup ohne unvollständiges Paar

Ein Backup umfasst Core-Datenbank, getrennten Ankerzustand, öffentliche
Schlüsselfingerprints, Migrationschecksums, Code-SHA und einen privaten Manifest.
Produktiv-Secrets und private Schlüssel werden gesondert verschlüsselt gesichert;
sie gehören nie in ein Git- oder öffentliches Evidenzartefakt.

Reihenfolge für einen konsistenten Wartungsbackup:

1. Neue Aufträge sperren und laufende Requests geordnet beenden.
2. Kern stoppen; Ankerzustand erhalten, Anker nicht zurücksetzen.
3. Persistierte Kette und gespeicherten signierten Kopf prüfen.
4. Den signierten Ankerstand unabhängig abfragen und gegen denselben Kopf prüfen.
5. Erst bei gleichem count/head_hash DB-Snapshot und Ankersnapshot sichern.
6. Manifest mit beiden Hashes, SHA, UTC und Ergebnis erzeugen; getrennten
   unveränderlichen Ablageort für den zuletzt bestätigten Ankerstand verwenden.
7. Backup verschlüsselt an den bestätigten externen Speicher übertragen und
   Lesbarkeit/Integrität dort prüfen. Fehlende Bestätigung ergibt kein Backup-PASS.
8. Kern nur nach erneut erfolgreicher Startprüfung freigeben.

Das konkrete Backupziel, Retention, Zugang und verantwortlicher Operator sind
noch nicht angegeben. Der Entwurf eröffnet keine neue externe Speicherung.

## Restore und Anker-Vorlauf

Vorschlag zur Architekturentscheidung: Der neueste unabhängig festgehaltene
Ankerstand bleibt eine nicht absenkbare Untergrenze. Ein älteres DB-Backup
berechtigt niemals zum Zurücksetzen oder Neuerzeugen des Ankers.

1. Dienst bleibt offline; zuerst Restore in eine isolierte Prüf-DB.
2. Code, Migrationen, bytegenaue Wires, Signaturen und bidirektionale Ledger-/
   Audit-Zuordnung vollständig prüfen.
3. DB-Stand hinter dem festgehaltenen Anker: Start muss verweigern.
4. Fehlende Historie aus verifizierter Replikation oder Wiederherstellungslogs
   wiederherstellen, bis sie den bisherigen Ankerstand vollständig enthält.
5. DB vor dem Anker: ausschließlich bereits gespeicherte und verifizierte
   signierte Köpfe nachverankern. Recovery signiert keine neuen DB-Inhalte.
6. Gleichstand und vollständige Prüfung: Restore-Protokoll erzeugen; anschließend
   kontrolliert starten und einen neuen Job mit neuer ID ausführen.
7. Fehlende Historie nicht rekonstruierbar: HOLD. Kein stiller Epochenwechsel,
   kein neuer Anker und keine Freigabe alter job_id-Werte.

Eine andere Loss-/Epoch-Recovery würde einen eigenen Sicherheitsvertrag mit
Kaans ausdrücklicher Architekturentscheidung benötigen.

## Schlüsselrotation

Rotation ist eine gesonderte Betriebsentscheidung. Der aktuelle Kern leitet
Rollenschlüssel aus einem Root-Secret ab. Ein einfacher Austausch würde
gespeicherte alte Handoffs, Ergebnisse und Köpfe unverifizierbar machen.
Darum sind vor einer Rotation ein implementierter historischer Schlüsselbund,
ein überprüfbarer Übergangsvertrag und passende Start-/Recovery-Tests nötig.
Ein geänderter Root-Schlüssel wird bis dahin fail closed abgelehnt; vorhandene
historische Daten werden nicht neu signiert.

## Abnahmeprotokoll

Das private Protokoll nennt Host, OS/Kernel/Landlock, vollständigen Code-SHA,
Migrationen, UIDs, Listener, Tests/Demo/Guards, Review-SHAs und Backupmanifest.
Secretinhalte und DSN-Werte werden nicht ausgegeben.

Erforderliche Restore-Proben: gültiger Gleichstand startet; älteres DB-Backup
hinter Anker verweigert; DB-Vorlauf wird ausschließlich mit vorhandenem gültig
signierten Kopf nachverankert; manipulierte Bytes oder Kopf-Signatur verweigern.
Nach jeder Probe bleibt jede bereits reservierte ID verbrannt.

E3 folgt dieser Anleitung auf einem frischen Linux-Host. Ein neuer Worktree
auf dem Entwicklungsserver ersetzt diesen Betriebsnachweis nicht.

## Noch offene konkrete Freigaben

Nach den fertigen Code-/Review-Nachweisen: Anker/Core-Installation mit eigenen
OS-Nutzern, tatsächlicher Worker-Startpfad oder ausdrücklich verschobene volle
Nutzertrennung, bestätigtes Backupziel und die oben vorgeschlagene Recoveryregel.
AGENTS.md verlangt für Deployment, Zugriffsrechte und Secret-Rotation Kaans OK.
