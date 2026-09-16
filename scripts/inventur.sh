#!/usr/bin/env bash
# inventur.sh – Schritt 2: Inventur von Agent-Genius für GeniusNew
#
# Nutzung:  bash inventur.sh [QUELLE] [AUSGABE]
#   QUELLE   Repo-URL oder lokaler Klon (Standard: https://github.com/Kaancodm/Agent-Genius.git)
#   AUSGABE  Markdown-Datei (Standard: INVENTUR.md)
#   Optional: BASELINE=09496c94 MVP_BRANCH=... PR_BRANCH=... bash inventur.sh
#
# Nur lesend: arbeitet in einem temporären Klon, der am Ende gelöscht wird.
# Kein Push, kein Merge, keine Änderung an der History.
set -euo pipefail

SRC="${1:-https://github.com/Kaancodm/Agent-Genius.git}"
OUT="${2:-INVENTUR.md}"
BASELINE="${BASELINE:-09496c94}"
MVP_BRANCH="${MVP_BRANCH:-codex/mvp-v0.1}"
PR_BRANCH="${PR_BRANCH:-codex/phase-1-2-sandbox-forensics}"

OUT_ABS="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REPO="$TMP/repo"

g()  { git -C "$REPO" -c core.quotepath=false "$@"; }
md() { printf '%s' "$1" | sed 's/|/\\|/g'; }

echo "Klone $SRC (nur lesend) ..." >&2
git clone --quiet --no-checkout "$SRC" "$REPO"

g rev-parse --verify --quiet "${BASELINE}^{commit}" >/dev/null \
  || { echo "Baseline $BASELINE nicht gefunden." >&2; exit 1; }
BASE="$(g rev-parse --short=12 "${BASELINE}^{commit}")"

ref() { g rev-parse --verify --quiet "refs/remotes/origin/$1^{commit}" >/dev/null && echo "origin/$1" || true; }
MVP_REF="$(ref "$MVP_BRANCH")"
PR_REF="$(ref "$PR_BRANCH")"
if [ -z "$PR_REF" ] && g fetch --quiet origin "pull/1/head:refs/remotes/origin/pr-1" 2>/dev/null; then
  PR_REF="origin/pr-1"
fi

vorschlag() {
  case "$1" in
    .env*|*/.env*|*.pem|*.key|*id_rsa*) echo "⚠ nicht übernehmen: mögliches Secret" ;;
    staat/*)             echo "Kern – Regierung/Verfassung" ;;
    polizei/*)           echo "Kern – Polizei/Forensik" ;;
    einwohnermeldeamt/*) echo "Kern – Datenbank" ;;
    b*rgerb*ro/*)        echo "Kern – Portal" ;;
    industriegebiet/*)   echo "Dev-Schale – Sandbox" ;;
    land/*)              echo "Schalen – Worker (Zuordnung prüfen)" ;;
    tests/*|*/tests/*|test_*|*/test_*) echo "Tests – mit Modul übernehmen" ;;
    docs/*|*.md)         echo "Doku" ;;
    .github/*)           echo "CI" ;;
    *)                   echo "prüfen" ;;
  esac
}

fund() {  # $1 Ref, $2 Begriff -> erster Treffer (Dateiname, sonst Inhalt) oder –
  [ -n "$1" ] || { echo "–"; return; }
  local hit
  hit="$(g ls-tree -r --name-only "$1" | grep -i -F -m1 -- "$2" || true)"
  [ -n "$hit" ] || hit="$(g grep -l -I -i -F -e "$2" "$1" -- 2>/dev/null | head -n1 | sed "s#^$1:##" || true)"
  if [ -n "$hit" ]; then echo "\`$(md "$hit")\`"; else echo "–"; fi
}

delta() {  # $1 Ref, $2 Überschrift
  echo "## $2"; echo
  if [ -z "$1" ]; then echo "_Branch nicht gefunden._"; echo; return; fi
  if ! g diff --name-status --no-renames -z "$BASE" "$1" > "$TMP/diff" 2>/dev/null; then
    echo "_Vergleich mit der Baseline nicht möglich._"; echo; return
  fi
  if [ ! -s "$TMP/diff" ]; then echo "_Keine Änderungen gegenüber der Baseline._"; echo; return; fi
  echo "| Status | Pfad | Vorschlag neu | Entscheidung |"
  echo "|---|---|---|---|"
  while IFS= read -r -d '' st && IFS= read -r -d '' f; do
    echo "| $st | \`$(md "$f")\` | $(vorschlag "$f") | |"
  done < "$TMP/diff"
  echo
}

{
  echo "# Inventur: Agent-Genius → GeniusNew"
  echo
  echo "- **Quelle:** \`$SRC\`, Baseline \`$BASE\`"
  echo "- **MVP-Branch:** ${MVP_REF:-nicht gefunden}"
  echo "- **Draft-PR #1:** ${PR_REF:-nicht gefunden} (nur Einzelteile, nie als Ganzes)"
  echo "- **Erstellt:** $(date '+%d.%m.%Y %H:%M'), Status: ungeprüft"
  echo "- **Entscheidung** je Datei: übernehmen / anpassen / verwerfen"
  echo
  echo "## 1. Pflicht-Bausteine"
  echo
  echo "| Baustein | Baseline | MVP-Branch | PR #1 |"
  echo "|---|---|---|---|"
  for b in Aktenzeichen handoff.schema.json HandoffContract Orchestrator AuditChain \
           WorkerMonitor BugReport approval-policy.json forensik HANDOVER.md PROJECT_STATUS.md; do
    echo "| $b | $(fund "$BASE" "$b") | $(fund "$MVP_REF" "$b") | $(fund "$PR_REF" "$b") |"
  done
  echo
  echo "## 2. Dateien in der Baseline"
  echo
  echo "| Pfad | Zeilen | Letzter Commit | Vorschlag neu | Entscheidung |"
  echo "|---|---|---|---|---|"
  g ls-tree -r -z --name-only "$BASE" > "$TMP/files"
  while IFS= read -r -d '' f; do
    n="$(g show "$BASE:$f" 2>/dev/null </dev/null | wc -l | tr -d ' ')" || n="?"
    c="$(g log -1 --format=%h "$BASE" -- "$f" </dev/null)"
    echo "| \`$(md "$f")\` | $n | \`$c\` | $(vorschlag "$f") | |"
  done < "$TMP/files"
  echo
  delta "$MVP_REF" "3. Änderungen im MVP-Branch (A neu · M geändert · D gelöscht)"
  delta "$PR_REF"  "4. Änderungen in Draft-PR #1 (A neu · M geändert · D gelöscht)"
  echo "## 5. Offene Fragen"
  echo
  echo "- Wohin gehört \`land/\` (Worker): Kern-Pool oder je Schale?"
  echo "- Welche Einzelteile aus PR #1 gehen in die Dev-Schale?"
  echo "- Welche Dateien macht der Neuschnitt überflüssig?"
} > "$OUT_ABS"

echo "Inventur geschrieben: $OUT_ABS" >&2
