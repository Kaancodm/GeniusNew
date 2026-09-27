#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AUDIT="$SCRIPT_DIR/audit.sh"

[[ -f "$AUDIT" ]] || { echo "FAIL: audit.sh missing"; exit 1; }

out="$(bash "$AUDIT" --self-test)"

grep -Fq 'GeniusNew Server Gate 0' <<<"$out"
grep -Fq 'MODE=self-test' <<<"$out"
grep -Fq 'CHECK git' <<<"$out"
grep -Fq 'CHECK python3' <<<"$out"
grep -Fq 'CHECK tmux' <<<"$out"
grep -Fq 'CHECK docker' <<<"$out"
grep -Fq 'CHECK tailscale' <<<"$out"
grep -Fq 'CHECK claude' <<<"$out"
grep -Fq 'CHECK codex' <<<"$out"
grep -Fq 'CHECK gemini' <<<"$out"
grep -Fq 'NO_SECRETS=1' <<<"$out"

if grep -Eqi '(token|password|secret=|api[_-]?key=)' <<<"$out"; then
  echo "FAIL: self-test output contains secret-like fields"
  exit 1
fi

echo 'PASS: server audit self-test'
