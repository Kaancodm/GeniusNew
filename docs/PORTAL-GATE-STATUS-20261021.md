# Portal D1–D3: Integrationsstand und verbleibende Gates

Geprüft am 21.10.2026 gegen `main`
`9385f5bd9dc43f277af8f3b0265c7b4d45cfbc71`. Reiner Code-/Vertragsabgleich;
keine Betriebsabnahme und keine Aktivierungsfreigabe. D1–D3 bleiben verbindlicher
Beta-Umfang. Keines dieser drei Gates ist durch #151/#152 abgeschlossen.

## Integriert gegenüber aktiv

| Bereich | Nachgewiesen auf main | Noch nicht nachgewiesen / offen |
| --- | --- | --- |
| D2 Passwort | [#151](https://github.com/Kaancodm/GeniusNew/pull/151), Merge `1f1c5d9096ee4a47f3bc7880e8a8e463f67c2250`: `portal_passwords.py`, Argon2id m=65536/t=3/p=4, exakte UTF-8-Bytes, 1–1024 Byte, striktes PHC-Profil; Tests und Refusal-Guard registriert | Keine Login-Route; Mindestlängenregel, Dummy-Hash-Pfad und Login-Budgets vor Integration erforderlich. Die technische Untergrenze von einem Byte ist keine freigegebene Passwortpolitik. |
| D2 Token/Cookie | [#152](https://github.com/Kaancodm/GeniusNew/pull/152), Merge `9385f5bd9dc43f277af8f3b0265c7b4d45cfbc71`: `portal_session_tokens.py`, 32 zufällige Bytes, kanonische Token, SHA-256-Digest, sessionsgebundener HMAC-CSRF-Vergleich; `__Host-`/Secure/HttpOnly/Path=/, SameSite=Lax, Max-Age=28800; Tests/Guard registriert | Keine Session-DB oder aktive Route. Cookie-Max-Age ersetzt keine serverseitige Ablaufprüfung; Digest-Helfer erzwingt keine Speicherregel. Keine Rotation, Sperrung, Origin-Prüfung oder CSRF-Key-Verwaltung im Runtime-Pfad. |
| D1 Dienstauthentisierung | [D1-Vertrag](PORTAL-CORE-D1-DRAFT.md) als HOLD-Dokument, rohe Header-Duplikatablehnung aus #162 | Kein vollständiger signierter Portal→Core-Pfad. Codec-PR #147 ist offen; kein Runtime-Nachweis aus einem Entwurf ableitbar. |
| D3 Personenbindung/Ansichten | [D3-Vertrag](PORTAL-IDENTITY-D3-DRAFT.md), [DB §5.6](DATABASE.md#56-portal-d2d3-wiring-und-eigentumsgrenzen-vorschlag-hold) als HOLD-Entwurf | Core-Registry, versionierte Nutzer-Aussage, sichere Sperr-/Restore-Transaktionen und Portal-Ansichten fehlen als abgenommener Gesamtpfad. |

Codebelege am geprüften SHA:
[Passwortmodul](https://github.com/Kaancodm/GeniusNew/blob/9385f5bd9dc43f277af8f3b0265c7b4d45cfbc71/geniusnew/portal_passwords.py),
[Tokenmodul](https://github.com/Kaancodm/GeniusNew/blob/9385f5bd9dc43f277af8f3b0265c7b4d45cfbc71/geniusnew/portal_session_tokens.py),
[HTTP-Eingang](https://github.com/Kaancodm/GeniusNew/blob/9385f5bd9dc43f277af8f3b0265c7b4d45cfbc71/geniusnew/http_entry.py).
Die beiden Portalmodule werden im Paket `geniusnew/` nur definiert, nicht von
einem Runtime-Aufrufer importiert. Ihre Tests sind Bausteintests, kein Login-E2E.
Die PR-Beschreibung von #152 nennt frische CI noch ausstehend; der bestätigte Merge
allein wird hier ausdrücklich nicht in einen CI- oder Gate-PASS umgedeutet.

## Verbleibende Implementierungsschritte und Abnahme

1. **D2 Loginvertrag schließen:** Passwort-Mindestlänge und Dummy-Hash für unbekannte
   Konten aus den Auflagen zu #151 umsetzen; persistente Login-Drosselung auch für
   unbekannte Adressen und falsche Passwörter (§5.6). Budgetzahlen, Zeit-/Restore-
   und Parallelitätsregeln festlegen und unabhängig prüfen. Vor teurem Argon2-Aufruf
   begrenzen; dieser Prüfschritt ist eine Umsetzungsempfehlung, keine neue Freigabe.
   Tests: bekannte/unbekannte Konten, erschöpftes/unklares Budget, parallele Versuche,
   Neustart und DB-Ausfall; keine Prozesslokalität als Ersatz für Persistenz.
2. **D2 Session-Lebenszyklus:** nur Digest speichern, ACTIVE-Konto und serverseitiges
   Zeitfenster prüfen; Login/Rotation atomar, alte Session und ihr CSRF-Token danach
   ablehnen. Logout/Sperrung widerrufen; DB-Ausfall und unbekannter COMMIT fail closed.
   Restore darf alte Sessions nicht reaktivieren. Session-Epoche/externes Recovery-
   Verfahren bleibt laut §5.6 HOLD und braucht vor Code einen geprüften Vertrag.
3. **D2 CSRF/Origin:** alle mutierenden Operationen einschließlich Login/Logout
   müssen Origin und sessionsgebundenen CSRF-Header prüfen; keine Mutation per GET.
   Fehlender/fremder Origin und fehlendes/fremdes/rotiertes CSRF-Token müssen scheitern.
   Das Vor-Login-CSRF-/Session-Bootstrap-Verfahren ist noch zu konkretisieren und zu
   testen. Eigenen serverseitigen CSRF-Key sicher konfigurieren; Cookie-Profil klären:
   Code Lax/8 h gegenüber Strict-Vorschlag in §5.6 ist noch keine Profilentscheidung.
4. **D1 authentisierten Dienstpfad fertigstellen:** strikt kanonische signierte Bytes,
   öffentlicher Verifier, Ablauf/Replay, getrennte Authentisierungspfade, geschützter
   Ingress vor Vorab-Budget, Quell-/globales-/Dienstbudget und tatsächlicher erster
   Proxy-Hop. Uhrprofil, 65-s-Startquarantäne, atomare Reservation/Job/Audit,
   Ablehnungs-Audit und Audit-Kapazität bleiben Aktivierungsgates.
   [#161](https://github.com/Kaancodm/GeniusNew/pull/161) am Head
   `67b11f48fe882a3814bc564702f3a8e33b3545eb` bearbeitet bereits den DB-Vertrag;
   dessen neue Details bleiben ungeprüfte HOLD-Vorschläge, keine Migration.
5. **D3 Identität und Funktionen:** Dienstsignatur beweist nur den Dienst. Für
   Personen braucht es Core-seitige eindeutige Bindung, getrennten versionierten
   Nutzervertrag und Tests gegen unbekannte/gesperrte/mehrdeutige Aliasse, parallele
   Sperrung und Selbstfreigabe. Auftrag, Verlauf, Freigaben und API-Keys benötigen
   eigene autorisierte Operationen; fremde Jobs/Verläufe liefern 404.
   D1 allein erlaubt nur die bereits entschiedene geschlossene Ein-Nutzer-Beta mit
   Kaan, risikoarmen Tools und ohne Approvals; kein zweiter Nutzer ohne D3.

Abnahme erst mit E2E-/Negativtests, Refusal-Guards, notwendigen DB-Reviews und CI am
konkreten Kandidaten-SHA. [#196](https://github.com/Kaancodm/GeniusNew/pull/196)
bereitet bereits die E2-Gesamtprüfung vor; dieser Abgleich ersetzt sie nicht.
Keine Login-Aktivierung, produktive DB-Änderung, Merge- oder Deploymentfreigabe.

## Abgrenzung der Dokumentationsarbeit

Offene PRs am 21.10.2026 geprüft: #161 besitzt den D1-DB-Entwurf, #196 die
E2-Checkliste. #190 ändert STATUS zur Sichtbarkeit; sein Inhalt ist laut aktueller
STATUS bereits über #185/#191 übernommen. Keine dieser Arbeiten wird hier geändert
oder dupliziert. Dieser Auftrag korrigiert ausschließlich Portalstatus und verlinkt
die vorhandenen Verträge; Entscheidungen und DATABASE bleiben unverändert.
