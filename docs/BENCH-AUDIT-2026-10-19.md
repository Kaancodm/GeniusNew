# P-01 Messung: Wachstum der Audit-Kosten (19.10.2026)

Messung zu Befund P-01 aus `docs/CODE-SECURITY-REVIEW-2026-10-19.md`, ausgeführt mit
`scripts/bench_audit.py` am Stand nach `9c3a533` (Arena-Branch). Umgebung: Python 3.11.2,
Linux 6.1 x86_64, Intel Xeon @ 2,60 GHz, 2 vCPU (Sandbox-VM). Absolute Zeiten sind
maschinenabhängig; die *Form* des Wachstums und die Decken sind es nicht.

## Was gemessen wurde — und was nicht

Gemessen (in-process, synthetische Demo-Events wie in `tests/test_audit_chain.py`):
`AuditChain.append`, Snapshot + Signieren, `AuditAnchor.commit` (verifiziert die *gesamte*
Kette) und `anchor_process._check_commit_size` (serialisiert die gesamte Kette). Phase 1
committed nach **jedem** Append — das ist die Produktionskadenz von
`wiring._AnchoredAudit.append`.

**Nicht** gemessen: der PostgreSQL-Pfad. `audit_store._append` liest pro Append zusätzlich
alle Records *und* alle signierten Prefix-Köpfe und validiert sie (`_read` →
`_stored_snapshot`, inklusive einer Ed25519-Prüfung pro historischem Kopf). Alle Zahlen
hier sind daher **Untergrenzen** für den Dienstpfad.

## Ergebnisse

Phase 1 — Commit nach jedem Event (Fensterdurchschnitt):

| Records | ms pro committetem Event | ein Commit (ms) |
| ---: | ---: | ---: |
| 100 | 0,65 | 1,0 |
| 250 | 1,60 | 2,1 |
| 500 | 3,15 | 4,1 |
| 1 000 | 6,29 | 9,0 |
| 1 500 | 10,44 | 12,3 |

Phase 2 — nur Append, eine Vollverifikation pro Checkpoint:

| Records | Append ms/Event | Commit ms | µs/Record | Size-Check ms | Request KiB |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 000 | 0,0068 | 7,7 | 7,74 | 4,9 | 643 |
| 2 000 | 0,0070 | 15,5 | 7,77 | 10,1 | 1 288 |
| 5 000 | 0,0067 | 37,4 | 7,47 | 27,9 | 3 225 |
| 10 000 | 0,0067 | 76,7 | 7,67 | 58,7 | 6 452 |
| 20 000 | 0,0071 | 152,4 | 7,62 | 125,6 | 12 937 |

Decken (aus den Anker-Grenzen, keine Messung nötig):

| Grenze | Records |
| --- | ---: |
| Anker-Zustandsdatei: 16 MiB ÷ 270 Byte Kopfzeile | **62 137** |
| Commit-Request: 64 MiB ÷ ~660 Byte/Record | ~101 000 |
| **Bindend** | **62 137 Records Gesamtlebensdauer** |

Projektion (linear mit 7,6 µs/Record, nur Anker-Verifikation): bei 10 000 Records
76 ms, bei 30 000 Records 229 ms, an der Decke **473 ms pro committetem Event** — plus
Size-Check (an der Decke ≈ 390 ms) plus der nicht gemessene DB-Read. Die kumulierte
Anker-Verifikations-CPU bis zur Decke beträgt ≈ 4,1 Stunden. Die Demo erzeugt 4 Events
pro Job; bei dieser Kadenz entsprechen 62 137 Records ≈ **15 500 Jobs**, danach verweigert
der Dienst jedes weitere Event (fail closed, aber Betriebsende).

## Schlussfolgerung für das Inkrementell-Design

1. Der Append selbst ist flach (0,007 ms) — das Problem sind ausschließlich die
   Voll-Ketten-Pässe pro Event: Snapshot-Kopie, Anker-Verifikation, Size-Check und im
   Dienst der DB-Read. Der Koeffizient ist über 1 000–20 000 Records konstant
   (7,5–7,8 µs/Record): sauber linear, also ehrlich projizierbar.
2. Latenz-Budget als Zielmarke: Soll der Audit-Anteil unter ~50 ms pro Event bleiben,
   ist der heutige Pfad bei wenigen tausend Records erschöpft. Das inkrementelle
   Protokoll (nur neues Record + alter Kopf an den Anker; Verifikation gegen den
   gespeicherten signierten Prefix statt Vollscan) wird damit **vor** jeder
   Beta mit Dauerbetrieb gebraucht, nicht danach.
3. Die 62 137-Decke hebt nur ein Epochen-/Checkpoint-Design auf (Köpfe pro Epoche
   verkettet, Ankerzustand kompaktiert auf letzten Kopf + Zähler). Beides gehört in
   denselben `DATABASE.md`-Entwurf (Reihenfolge in `docs/STATUS.md` Punkt 7).
4. Abnahmekriterium für die Umsetzung, mit diesem Skript prüfbar: Phase-1-Kosten pro
   Event bei 1 500 Records im selben Bereich wie bei 100 (statt Faktor 16), und die
   Decken-Tabelle zeigt keinen bindenden 62 137-Wert mehr.
