# Entwurf: Audit-Kette inkrementell (P-01) — DRAFT

**Status:** Entwurf, keine Entscheidung. Er ist die in `docs/STATUS.md` Punkt 7 vorgesehene
Design-Stufe nach der Messung (`docs/BENCH-AUDIT-2026-10-19.md`) und Eingabe für den
`DATABASE.md`-Abschnitt (ChatGPT in Kaans Auftrag, Sicherheitsreview Claude,
Design-Vorprüfung Gemini, Freigabe Kaan). Kein Code in diesem Dokument ist gebaut;
Abschnitt 7 listet die offenen Entscheidungen.

## 1. Problem (gemessen, nicht geschätzt)

Jedes committete Event bezahlt heute die ganze Historie mehrfach:

| Pfad | Voll-Ketten-Pass pro Event |
| --- | --- |
| `wiring._AnchoredAudit` | Snapshot aller Records + Signieren |
| `AuditAnchor.commit` (Kind/Client) | `verify()` über alle Records |
| `audit_store._append` → `_read` | alle Records + alle Prefix-Köpfe aus der DB lesen und validieren (inkl. Ed25519 pro Kopf) |
| `anchor_process._check_commit_size` | ganze Kette kanonisch serialisieren |

Gemessen (`scripts/bench_audit.py`, DB-Read darin noch nicht enthalten): Anker-
Verifikation stabil **7,6 µs/Record** ⇒ bei 62 137 Records ≈ 473 ms pro Event, Tendenz
linear weiter. Zwei harte Decken beenden den Betrieb vorher: Anker-Zustand (16 MiB
Kopfzeilen à 270 Byte ⇒ **62 137 Records Gesamtlebensdauer**) und Commit-Frame (64 MiB).

## 2. Kernidee: verifizierte Prefixe verlängern statt neu prüfen

Alle vier Pässe teilen dieselbe Verschwendung: Sie prüfen einen Prefix erneut, der
bereits vollständig verifiziert **und** durch einen signierten Kopf gebunden wurde.
Das Design ersetzt „alles prüfen" durch **Prefix-Verlängerung**:

> Wer `(count, head_hash)` eines einmal vollständig verifizierten Prefix kennt, muss
> für neue Records nur prüfen: (a) der erste neue Record hängt an `head_hash`
> (`previous_hash`), (b) jeder neue Record-Hash stimmt, (c) jeder neue Prefix-Kopf ist
> gültig signiert und bindet seinen Record, (d) der Endkopf bindet den letzten Record.
> Kosten: O(delta) statt O(n). Die vollständige Prüfung findet weiterhin statt — beim
> Start, periodisch und bei jeder Abweichung.

Unveränderte Invarianten: byte-exakte kanonische Events; signierter Kopf pro Record
in der DB (`audit_heads`, Migration 0004 bleibt); Anker akzeptiert nur Verlängerungen;
Neustart segnet nie einen unsignierten DB-Suffix (B5); B6-Atomarität (Ledger + Event in
einer Transaktion, Anker danach) bleibt; alle neuen Ablehnungen kommen in den
Refusal-Guard (`geniusnew/anchor_process.py`, `geniusnew/audit_store.py` sind guarded).

## 3. Baustein A: Delta-Commit zum Anker (Protokoll v2)

Neuer Request `{"kind": "commit2", ...}` neben dem heutigen `commit`:

| Feld | Bedeutung |
| --- | --- |
| `from_count` | muss exakt dem committeten Stand des Ankers entsprechen |
| `records` | nur die Records `from_count … head.count-1` (Delta) |
| `head` | der neue signierte Kopf über die Gesamtkette |

Der Anker verifiziert: Kopf-Signatur; `head.count == from_count + len(records)`;
`records[0].previous_hash == <sein head_hash>`; lückenlose Indizes und Record-Hashes;
letzter Record-Hash == `head.head_hash`. Erst dann rückt sein Stand vor und wird
persistiert (Baustein C). Grenzen: Delta ≤ 8 192 Records und Frame ≤ 8 MiB (eigene,
kleinere v2-Grenzen); Delta 0 mit identischem Kopf ist idempotent erlaubt
(Re-Ankern nach Absturz). Bei `from_count`-Abweichung antwortet der Anker ablehnend;
der Client erfragt `committed()` und sendet ab dort erneut (Bestandsprotokoll hat
dafür alles). Der v1-`commit` (ganze Kette) bleibt in der Übergangszeit akzeptiert
(Entscheidung D3) — er ist zugleich der Fallback für den allerersten Sync.

Client-Seite (`AnchorProcess.commit`, `AnchorClient.commit`,
`wiring._AnchoredAudit`): Die öffentliche Form `commit(head, records)` bleibt, aber
gesendet wird `records[committed_count:]`. Dazu bekommt die Ketten-Schnittstelle
`tip() -> (count, head_hash)` und `records_from(count)`; das heutige
Snapshot-Kopieren aller Records für jeden Commit entfällt auf dem Dienstpfad.

## 4. Baustein B: DB-Append ohne Voll-Read (`audit_store`)

Der Docstring heute — „Read committed storage on every call; never fall back to
process memory" — schützt vor Mehr-Instanz-Drift. Das Design erhält diese Eigenschaft,
ohne alles zu lesen:

1. **Start:** unverändert voller `_read` (alle Records + Köpfe), Ergebnis wird als
   `VerifiedTip(count, head_hash)` gemerkt.
2. **Append (unter dem bestehenden Advisory-Lock):** Tip-Abfrage in O(1)
   (letzter Kopf per PK + letzter Record). Drei Fälle:
   - *Tip == Cache:* direkt einfügen wie heute; Cache erst **nach** erfolgreichem
     Commit fortschreiben. Beim B6-Pfad (fremde Transaktion committet später) wird der
     Cache stattdessen als *dirty* markiert — der nächste Append geht über Fall 2.
   - *Tip vor Cache* (andere Instanz war schneller): **Verlängerungs-Read** — nur
     Records/Köpfe ab `cache.count` laden (begrenzt, z. B. ≤ 100 000, sonst Voll-Read)
     und exakt die `_stored_snapshot`-Prüfungen auf das Delta anwenden (Verkettung ab
     `cache.head_hash`, Byte-Exaktheit, Kopf-Signaturen, Zeitstempel-Bindung).
   - *Tip abweichend/zurück:* Voll-Read; bleibt die Abweichung, Ablehnung wie heute.
3. **Periodische Vollprüfung:** jeder P-te Append (Vorschlag P = 4 096, Entscheidung
   D1) führt einen Voll-Read aus (amortisiert O(n)/P), zusätzlich zum Start.
4. `_check_commit_size` entfällt auf dem DB-Pfad: Die Frame-Grenze gilt dem Delta und
   wird dort erzwungen, wo geframt wird (Anker-Client). Als DB-seitige Schranke bleibt
   eine erklärte Maximalzahl gespeicherter Records (Abschnitt 5).

**Die ehrliche Sicherheitsverschiebung (Kern der Entscheidung D1):** Eine Manipulation
in der *Mitte* der gespeicherten Kette (Event-Bytes geändert, Hash-Spalte belassen)
fällt heute beim nächsten Append auf, mit diesem Design erst bei Start oder
periodischer Vollprüfung. Einordnung: Die Runtime-Rolle `genius_core` hat auf
`audit_chain`/`audit_heads` nur `SELECT, INSERT` (Migration 0004) — der Dienst selbst
*kann* Historie nicht umschreiben; nötig wäre Owner-/Superuser-Zugriff, gegen den auch
heute nur der unabhängige Anker (eigener Nutzer, C2) und Erkennungszeitpunkt stehen.
Der Anker selbst ist vom Baustein unberührt: Er bindet weiterhin jeden Kopf, den er je
bestätigt hat; ein DB-Stand, der nicht von seinem Kopf abstammt, wird nie committet.

## 5. Baustein C: Anker-Zustand mit Epochen (Decke anheben)

Heute: eine signierte Kopfzeile pro Commit, `_load` verifiziert jede Zeile ⇒ 62 137.
Neu: **Epochen** der Länge E (Vorschlag E = 4 096, D2).

- Innerhalb einer Epoche hängt der Anker Kopfzeilen an wie heute (≤ E Zeilen).
- Am Epochenende schreibt er eine **Epoch-Zeile**: den signierten Kopf bei
  `count = k·E` (gleiches Format, `_verify_head`-prüfbar) — und kompaktiert: Die Datei
  wird als `[Epoch-Zeilen …, Zeilen der laufenden Epoche]` neu geschrieben.
- Dateigröße damit ≤ (n/E + E) Zeilen: bei 1 Mio. Records ≈ 4 340 Zeilen ≈ 1,2 MiB.
  Die 16-MiB-Grenze bindet erst jenseits von ~200 Mio. Records; bindend wird die
  erklärte Systemgrenze **`_MAX_COUNT` = 1 000 000** (D4) — benannt statt versteckt.
- **Crash-Sicherheit der Kompaktierung** ist der heikelste Teil: Schreiben in eine
  Temp-Datei im selben Verzeichnis, `fsync`, `rename`, Verzeichnis-`fsync`. Der
  heutige Inode-Lease (`_state_lock`) bindet aber gerade die Datei-Identität —
  `rename` wechselt den Inode. Der Entwurf muss hier eines von zwei Modellen wählen
  (Teil von D2): (i) Lease auf eine stabile Lock-Datei neben dem Zustand umstellen,
  oder (ii) zwei Zustandsdateien (Generation A/B) im Wechsel beschreiben und beim
  Laden die höchste vollständig verifizierende Generation nehmen. Beide ändern eine
  Grenze aus `SECURITY.md` (Hardlink-/Bind-Mount-Lease) ⇒ eigener PR mit Kaans OK.
- **Migration:** Beim ersten v2-Start wird eine alte Mehrzeilen-Datei einmalig voll
  verifiziert (wie heute) und beim ersten Epochenende in die neue Form überführt; ein
  Downgrade auf v1-Code liest Epoch-Zeilen als normale Kopfzeilen (strikt steigend) —
  Abwärtskompatibilität des Formats ist Designziel, zu testen.
- Unverändert: Wer die Datei schreiben darf, kann auf eine ältere Epoch-Grenze
  zurückschneiden (die offene Grenze aus `SECURITY.md`/S-03 wird durch Epochen
  gröber sichtbar, nicht geschlossen — Zeugen-Konzept bleibt eigenes Thema).

## 6. Ablauf in PRs (je ein Thema, je mit Tests + Guard)

1. **Protokoll v2 im Anker:** `verify_extension` + `commit2` (Anker-Kind und
   `anchor_process serve`), v1 bleibt. Tests: Lücken-/Replay-/Fremdketten-Deltas,
   Delta-Grenzen, Idempotenz.
2. **Delta-Versand:** `tip()`/`records_from()` an beiden Ketten, Client/Wiring senden
   Deltas; B7-Crashpunkte um „zwischen DB-Commit und Delta-ACK" erweitern.
3. **DB Tip-Cache + Verlängerungs-Read** (`audit_store`) — **Claude DB Review**
   Pflicht. Tests: Zweit-Instanz-Verlängerung, `dirty` nach B6-Join + Rollback,
   Mitten-Manipulation wird spätestens bei der periodischen Prüfung rot,
   Start bleibt Vollprüfung.
4. **Anker-Epochen + Kompaktierung** inkl. Lease-Modell und Altformat-Migration;
   Kill-Tests mitten in der Kompaktierung (B7-Muster).
5. **Nachweis + Doku:** `bench_audit.py` auf v2-Pfade, `SECURITY.md`-Grenzen
   (62 137-Decke, Erkennungszeitpunkt, Lease) mit Kaans OK anpassen, Boundary-Tests
   umkehren, `STATUS.md`-Messwerte.

**Abnahmekriterien** (an `scripts/bench_audit.py` gebunden): Phase-1-Kosten pro Event
bei 1 500 Records ≤ 2× der Kosten bei 100 (heute: Faktor 16); Commit-Request-Größe
unabhängig von n; projizierte CPU bis 1 Mio. Records < 10 min; Decken-Tabelle nennt
1 000 000 als erklärte Grenze statt 62 137.

## 7. Offene Entscheidungen (Kaan)

| # | Entscheidung | Vorschlag des Entwurfs |
| --- | --- | --- |
| D1 | Erkennungszeitpunkt Mitten-Manipulation: Verlängerungs-Modell + periodische Vollprüfung akzeptieren? P = ? | Ja, P = 4 096, plus Vollprüfung bei jedem Start (bleibt) |
| D2 | Epochengröße E und Lease-Modell für die Kompaktierung (Lock-Datei vs. A/B-Generation) | E = 4 096; Modell nach Gemini-Vorprüfung |
| D3 | v1-`commit` (Vollkette) wie lange weiter akzeptieren? | Bis Schritt 5, dann entfernen |
| D4 | Erklärte Obergrenze 1 000 000 Records; Archivierung (Export + signierter Genesis-Checkpoint) als späteres v3-Thema | Ja |
| D5 | Delta-Grenzen 8 192 Records / 8 MiB Frame | Ja |

## 8. Verworfene Alternativen

- **Nur Konstanten anheben** (64 MiB/16 MiB vergrößern): verschiebt Decke und Schmerz,
  das O(n²)-Verhalten bleibt — vom Benchmark widerlegt.
- **Köpfe nicht mehr pro Record in der DB signieren:** würde B5/B6-Beweiswert
  (jede Annahme an ihren Kopf gebunden) aufgeben; nicht nötig für die Kostenziele.
- **Hintergrund-Verifikation statt periodisch im Pfad:** bräuchte eine zweite
  DB-Verbindung und bricht das einspurige B6-Modell (eigenes Thema P-03).
