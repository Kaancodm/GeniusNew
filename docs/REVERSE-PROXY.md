# Reverse-Proxy vor dem Kern (Gate C4)

Der HTTP-Eingang des Kerns spricht kein TLS und begrenzt unauthentifizierten Verkehr nur
über seine Verbindungsgrenze (`SECURITY.md`). Beides gehört vor den Kern, in einen
Reverse-Proxy auf demselben Host. Dieses Dokument legt fest, was dort liegt und was im
Kern bleibt. Eine geprüfte Beispielkonfiguration für nginx steht in
[`docs/examples/nginx-geniusnew.conf`](examples/nginx-geniusnew.conf).

Das ist eine Vorlage, kein Deployment. Ein öffentlicher Endpunkt braucht Kaans
ausdrückliches OK (`AGENTS.md`).

## Aufteilung

| Aufgabe | Proxy | Kern |
| --- | --- | --- |
| TLS, HSTS, Zertifikate | ja | nein |
| Identität (API-Key → Principal) | nein, reicht `Authorization` unverändert durch | ja, allein |
| Anfragen pro Principal (`429 TOO_MANY_REQUESTS`) | nein | ja, Token-Bucket |
| Anfragen pro Client-Adresse, auch ohne gültigen Key | ja, `limit_req`/`limit_conn` | nein |
| Gleichzeitig laufende Jobs (`503 SERVICE_BUSY`) | nein | ja |
| Offene Verbindungen | ja, pro Adresse | ja, gesamt (`max_connections`) |
| Body-Größe | ja, verwirft früher | ja, 16 KiB, bleibt maßgeblich |
| Methoden und Routen | ja, nur `POST /jobs` und `POST /jobs/<id>/approve` | ja, gleiche Regel |

Der Kern liest keinen `X-Forwarded-*`-Header und soll es auch nicht: Die Client-Adresse
ist für ihn keine Identität. Er bekommt also nichts vom Proxy, dem er vertrauen müsste.

## Regeln

1. **Der Kern lauscht nur auf Loopback.** Der Proxy läuft auf demselben Host und
   erreicht ihn über `127.0.0.1`.
2. **Kein Port 80.** Ein Client, der seinen API-Key unverschlüsselt geschickt hat, hat
   ihn schon preisgegeben; eine Weiterleitung käme erst danach.
3. **Keine Zugangsdaten im Log.** Das Standard-Log von nginx enthält keine
   Request-Header. `$http_authorization` und `$http_x_approval_token` gehören in kein
   `log_format`.
4. **Chunked Bodies nicht durchreichen.** Der Kern lehnt `Transfer-Encoding` ab. nginx
   liest den Body vorher ganz (`proxy_request_buffering on`, Standard) und schickt ihn
   mit `Content-Length` weiter.
5. **Der Proxy ist so vertrauenswürdig wie der Kern.** Hinter TLS sieht er jeden Key im
   Klartext. Er läuft deshalb auf demselben Host unter einem eigenen Nutzer, nicht bei
   einem Dritten.

## Werte, die zusammenpassen müssen

| Proxy | Kern | Warum |
| --- | --- | --- |
| `client_max_body_size 16k` | `_MAX_BODY_BYTES` = 16 KiB | Größeres wird schon am Proxy abgelehnt (`413`) |
| `max_conns=64` am Upstream | `max_connections` = 64 | Darüber antwortet der Proxy selbst mit `502`, statt dem Kern Verbindungen zu geben, die er ungelesen schließt |
| `proxy_read_timeout 45s` | `wall_seconds` ≤ 30 s | Ein Job antwortet erst, wenn sein Worker fertig ist |
| `limit_req` 120/min, Burst 20 pro Adresse | 60/min, Burst 20 pro Principal | Pro Principal entscheidet der Kern; das Limit pro Adresse liegt darüber |

Ändert sich ein Wert im Kern, ändert sich die Vorlage im selben PR.

## Grenzen

- Das Limit pro Adresse trifft alle Principals hinter derselben Adresse (NAT, ein
  Portal-Backend). Das Portal (Gate D) braucht deshalb ein eigenes Kontingent oder
  eine eigene Route.
- Zertifikate, ihre Erneuerung und die Nutzertrennung zwischen Proxy und Kern gehören
  in die Betriebsanleitung (Gate C5).
- Kein mTLS. Wie sich das Portal gegenüber dem Kern ausweist, ist eine offene
  Entscheidung für Kaan (Roadmap v0.2, #59).
