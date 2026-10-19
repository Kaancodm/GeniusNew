# A2/C5: kostenlose Wegwerf-VM für die synthetische Systemprobe

**Status: reproduzierbare Testvorbereitung. Kein A2-, C5- oder E3-Betriebs-PASS.**

## Warum diese Variante?

Am 19.10.2026 wurde auf dem bestehenden Debian-13-Entwicklungsserver kein
`/dev/kvm` gefunden. Eine echte KVM-VM auf diesem Server ist damit nicht
ohne gesonderte Infrastrukturänderung nutzbar; ein Docker-Container darf den
geforderten Host-Nachweis nicht ersetzen.

Der eingeschränkt aktivierte Workflow
`.github/workflows/a2-c5-vm-probe.yml` verwendet einen standardmässigen
GitHub-hosted `ubuntu-24.04`-Runner, also eine kurzlebige Linux-VM mit
separatem systemd und cgroup v2. Standard-Runner in öffentlichen
GitHub-Repositories sind nach GitHubs Preisdokumentation kostenlos; **keine
Larger Runner, externen VMs oder CI-Artefaktuploads** werden benutzt.
Bei Änderung der Repository-Sichtbarkeit oder des Preismodells vor weiteren
Läufen Kosten erneut prüfen.

Quellen:
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- https://docs.github.com/en/billing/concepts/product-billing/github-actions

## Genau geprüfter Umfang

- Die **unveränderte synthetische** Systemprobe aus
  [PR #144](https://github.com/Kaancodm/GeniusNew/pull/144), gebunden an den
  Commit `55d5e2605d829bb44b2d9967066747ce48042019`.
- Checkout ohne persistentes GitHub-Token; Workflow-Berechtigung nur
  `contents: read`, keine übermittelten Secrets oder privaten Daten.
- Vorbedingungen: systemd als PID 1, D-Bus, cgroup v2, non-root CI-User,
  getrennte Test-UIDs und root-kontrollierte Python-Laufzeit.
- Testnutzer, Gruppen und Units werden nur auf der Wegwerf-VM angelegt und
  beim Abschluss geprüft entfernt.
- Abweichender SHA, unclean Checkout, fehlende Hostfunktion oder unbekannter
  Zustand ergeben HOLD/CI-Fehlschlag, nicht PASS.
- Nur redigiertes Urteil, Ergebniszahlen und Hash des bereits sanitisierten
  Reports werden in die öffentliche CI-Zusammenfassung aufgenommen.
  Es werden **keine Artefakte hochgeladen**.

Der Quellcode und die CI-Laufzeit liegen bei GitHub; Workflow-Metadaten und
CI-Protokolle werden extern gespeichert. **Dies ist kein Offline-Test** und
keine geeignete Umgebung für private Experimente oder Kundendaten.

## Ausführung und Beweiskette

Ein Push auf `chatgpt/a2-c5-ephemeral-vm-20261019` mit Änderung an diesem
Workflow oder diesem Dokument startet die Probe. Nach genehmigtem Merge kann
der Workflow manuell über `workflow_dispatch` erneut ausgelöst werden.
Die CI-Run-URL und der exakte SHA sind Teil des Nachweises.

Die Probe belegt ausschließlich synthetische Betriebssystem-Eigenschaften:
Kernel-Credentials, UID-/Socket-Trennung, Serialisierung, systemd-Lifecycle,
Bereinigung und Test-Secret-Schutz aus PR #144. Sie belegt **nicht** die echte
installierte GeniusNew-Runtime aus PR #140, nicht das Debian-13-Zielsystem,
nicht die Tests am echten Worker aus PR #150/#155 und nicht C5/E3.

**Gate:** Selbst bei PASS bleibt A2/C5 HOLD, bis der produktionsnahe
Worker-Pfad, Boot-Hostreferenz, geprüfte Core-Unit, Peer-DB-Negativprüfung,
Crash-Recovery und unabhängiger Security-Review belegt sind. Installation,
Secret-/DB-/Firewall-Änderungen, Merge, Deployment und Release benötigen
gesonderte Freigaben.
