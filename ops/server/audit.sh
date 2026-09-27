#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' '=== GeniusNew Server Gate 0 ==='

if [[ "${1:-}" == "--self-test" ]]; then
  printf '%s\n' 'MODE=self-test'
  for cmd in git python3 tmux docker tailscale claude codex gemini; do
    printf 'CHECK %s SELFTEST\n' "$cmd"
  done
  printf '%s\n' 'NO_SECRETS=1'
  exit 0
fi

printf '%s\n' 'MODE=audit'
printf 'UTC=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'USER=%s\n' "$(id -un)"
printf 'KERNEL=%s\n' "$(uname -srmo)"

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  printf 'OS=%s %s\n' "${NAME:-unknown}" "${VERSION_ID:-unknown}"
else
  printf '%s\n' 'OS=UNKNOWN'
fi

printf '%s\n' '--- resources ---'
if command -v nproc >/dev/null 2>&1; then printf 'CPU_COUNT=%s\n' "$(nproc)"; fi
if command -v free >/dev/null 2>&1; then free -h | sed -n '1,2p'; fi
df -h "$HOME" | sed -n '1,2p'

check_cmd() {
  local name="$1"
  shift
  if command -v "$name" >/dev/null 2>&1; then
    local version
    version="$($@ 2>/dev/null | head -n 1 || true)"
    printf 'CHECK %s PRESENT %s\n' "$name" "${version:-version-unknown}"
  else
    printf 'CHECK %s MISSING\n' "$name"
  fi
}

printf '%s\n' '--- tools ---'
check_cmd git git --version
check_cmd python3 python3 --version
check_cmd tmux tmux -V
check_cmd node node --version
check_cmd npm npm --version
check_cmd docker docker --version
check_cmd tailscale tailscale version
check_cmd claude claude --version
check_cmd codex codex --version
check_cmd gemini gemini --version
check_cmd gh gh --version

printf '%s\n' '--- services ---'
for svc in docker tailscaled ssh; do
  if command -v systemctl >/dev/null 2>&1; then
    state="$(systemctl is-active "$svc" 2>/dev/null || true)"
    printf 'SERVICE %s %s\n' "$svc" "${state:-unknown}"
  fi
done

if sudo -n true >/dev/null 2>&1; then
  printf '%s\n' 'SUDO_NONINTERACTIVE=YES'
else
  printf '%s\n' 'SUDO_NONINTERACTIVE=NO'
fi

repo="${GENIUSNEW_DIR:-$HOME/GeniusNew}"
printf '%s\n' '--- repository ---'
printf 'REPO_PATH=%s\n' "$repo"
if [[ -d "$repo/.git" ]]; then
  printf '%s\n' 'REPO_PRESENT=YES'
  printf 'REPO_BRANCH=%s\n' "$(git -C "$repo" branch --show-current 2>/dev/null || true)"
  printf 'REPO_HEAD=%s\n' "$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)"
  printf 'REPO_DIRTY_COUNT=%s\n' "$(git -C "$repo" status --porcelain=v1 2>/dev/null | wc -l | tr -d ' ')"
else
  printf '%s\n' 'REPO_PRESENT=NO'
fi

printf '%s\n' 'NO_SECRETS=1'
printf '%s\n' '=== END Gate 0 ==='
