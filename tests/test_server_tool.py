"""Tests for ops/server/genius-server.

The tool changes a real machine (apt, systemd, sshd, ufw), so these tests never
let it near one: it runs with a private PATH that holds only harmless copies of the
basic tools and logging stubs for everything that would change the host. What is
verified is the tool's own logic (order, dry run, lockout guards, rollback), not
OpenSSH, Tailscale or ufw themselves.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "ops" / "server" / "genius-server"

# Real tools the script needs; copied into the private PATH as symlinks.
REAL_TOOLS = (
    "bash", "env", "cat", "cut", "head", "tail", "sed", "awk", "grep", "mktemp", "rm",
    "install", "stat", "touch", "chmod", "chown", "dirname", "basename", "tr", "wc",
    "sleep", "timeout", "python3", "hostname", "uptime", "df", "ps", "who", "free",
    "clear", "true", "date", "mkdir", "ls", "sort", "test", "realpath",
)

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIfakefakefake termius-ipad"
TAILSCALE_SESSION = "100.64.0.7 50000 100.101.102.103 22"
# The connection lockdown ran from; confirm must come from another one.
LOCKDOWN_SESSION = "100.64.0.7 49999 100.101.102.103 22"
PUBLIC_SESSION = "203.0.113.5 50000 192.0.2.10 22"

STUBS = {
    # id: only the two forms the script uses; the uid is configurable so both paths run.
    "id": """\
case "$1" in
  -u) echo "${FAKE_UID:-1000}" ;;
  -un) echo "${FAKE_USER:-tester}" ;;
  *) exit 1 ;;
esac
""",
    "getent": """\
if [ "$1" = passwd ] && [ "$2" = "${FAKE_USER:-tester}" ]; then
  echo "$2:x:1000:1000::$FAKE_HOME:/bin/bash"
else
  exit 2
fi
""",
    "sudo": """\
echo "sudo $*" >>"$LOG"
[ "${1:-}" != "-n" ] || shift
exec "$@"
""",
    "apt-get": 'echo "apt-get $*" >>"$LOG"\n',
    "systemd-run": 'echo "systemd-run $*" >>"$LOG"\necho active >"$STATE/timer"\n',
    # systemctl: the rollback timer and service keep their state in files, so a
    # test can put them anywhere between "set" and "already fired".
    "systemctl": """\
echo "systemctl $*" >>"$LOG"
case "$1" in
  is-active)
    case "$2" in
      geniusnew-lockdown-rollback.timer) state="$(cat "$STATE/timer" 2>/dev/null || echo inactive)" ;;
      geniusnew-lockdown-rollback.service) state="$(cat "$STATE/rollback_service" 2>/dev/null || echo inactive)" ;;
      geniusnew-deck) state="$(cat "$STATE/deck" 2>/dev/null || echo active)" ;;
      *) state=active ;;
    esac
    echo "$state"
    [ "$state" = active ] ;;
  stop)
    if [ "$2" = geniusnew-lockdown-rollback.timer ]; then
      [ -z "${FAKE_STOP_FAIL:-}" ] || exit 1
      [ -n "${FAKE_STOP_NOOP:-}" ] || echo inactive >"$STATE/timer"
    fi ;;
  reload) [ -z "${FAKE_RELOAD_FAIL:-}" ] || exit 1 ;;
  disable) [ -z "${FAKE_DISABLE_FAIL:-}" ] || exit 1 ;;
  daemon-reload) [ -z "${FAKE_DAEMON_RELOAD_FAIL:-}" ] || exit 1 ;;
  list-jobs) cat "$STATE/jobs" 2>/dev/null || true ;;
esac
""",
    "curl": """\
echo "curl $*" >>"$LOG"
[ -z "${FAKE_CURL_FAIL:-}" ] || exit 22
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  last="$1"; shift
done
if [ -n "${FAKE_CURL_EMPTY:-}" ]; then : >"$out"; else echo "FAKE-DOWNLOAD $last" >"$out"; fi
""",
    "tailscale": """\
echo "tailscale $*" >>"$LOG"
case "$1" in
  ip)
    [ -e "$STATE/ts_up" ] || exit 1
    [ "${2:-}" = -6 ] || echo "${FAKE_TS_IP4:-100.101.102.103}"
    [ "${2:-}" = -4 ] || echo fd7a:115c:a1e0::1 ;;
  up) touch "$STATE/ts_up" ;;
  status)
    if [ "${2:-}" = "--json" ]; then
      echo '{"Self":{"DNSName":"geniusnew-server.tail1234.ts.net."}}'
    else
      echo "100.101.102.103 geniusnew-server tester linux -"
    fi ;;
esac
""",
    "sshd": """\
echo "sshd $*" >>"$LOG"
case "$1" in
  -t) [ -z "${FAKE_SSHD_T_FAIL:-}" ] || exit 1 ;;
  -T)
    if [ -n "${FAKE_SSHD_T_OUT:-}" ]; then cat "$FAKE_SSHD_T_OUT"
    else printf 'port 22\\npasswordauthentication no\\nkbdinteractiveauthentication no\\npermitrootlogin no\\npubkeyauthentication yes\\n'; fi ;;
esac
""",
    # ssh-keygen: a key is valid when its second field starts with AAAA; the bit
    # length is 2048 when the comment says bits2048, otherwise 3072 (rsa) or 256.
    "ssh-keygen": """\
[ "$1" = "-l" ] && [ "$2" = "-f" ] || exit 1
rc=1
while IFS= read -r line; do
  set -- $line
  # Like OpenSSH, skip a leading options field: the key type and blob follow it.
  case "${2:-}" in AAAA*) ;; *) shift ;; esac
  case "${2:-}" in AAAA*) ;; *) continue ;; esac
  rc=0
  case "$line" in
    *bits2048*) bits=2048 ;;
    ssh-rsa*) bits=3072 ;;
    *) bits=256 ;;
  esac
  echo "$bits SHA256:fakefingerprint ${3:-no-comment} ($1)"
done <"$3"
exit $rc
""",
    # ufw: translates its status unless LC_ALL=C, like a localized install, and
    # keeps the rules it was given so "show added" reports them while it is off.
    "ufw": """\
echo "ufw $*" >>"$LOG"
translate() { if [ "${LC_ALL:-}" = C ]; then cat; else sed 's/Status: active/Status: aktiv/; s/Status: inactive/Status: inaktiv/'; fi; }
case "$1" in
  status)
    [ -z "${FAKE_UFW_STATUS_FAIL:-}" ] || exit 1
    { cat "$STATE/ufw_status" 2>/dev/null || echo "Status: inactive"; } | translate ;;
  show)
    echo "Added user rules (see 'ufw status' for running firewall):"
    cat "$STATE/ufw_added" 2>/dev/null || true ;;
  allow|limit) echo "ufw $*" >>"$STATE/ufw_added" ;;
  delete)
    shift
    rule="ufw $*"
    if [ -f "$STATE/ufw_added" ]; then
      grep -vxF "$rule" "$STATE/ufw_added" >"$STATE/ufw_added.new" || true
      cat "$STATE/ufw_added.new" >"$STATE/ufw_added"
      rm -f "$STATE/ufw_added.new"
    fi ;;
  --force)
    if [ "$2" = enable ]; then
      { echo "Status: active"; echo; echo "To Action From"; echo "22/tcp on tailscale0 ALLOW Anywhere"; } >"$STATE/ufw_status"
    fi ;;
esac
""",
    "ss": """\
echo "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"
echo "LISTEN 0 128 [::]:22 [::]:*"
echo "LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*"
echo "LISTEN 0 128 100.101.102.103:22 0.0.0.0:*"
echo "LISTEN 0 128 100.128.0.1:80 0.0.0.0:*"
echo "LISTEN 0 128 [fd7a:115c:a1e0::1]:22 [::]:*"
echo "LISTEN 0 128 [fd7a:1::1]:8080 [::]:*"
""",
}


class Sandbox:
    """A private PATH, a log of every stubbed call, and throw-away target paths."""

    def __init__(self, tmp: Path, *, without: tuple[str, ...] = ()) -> None:
        """Build isolated command stubs, synthetic host state, and disposable target paths."""
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.home = tmp / "home"
        self.home.mkdir()
        self.state = tmp / "state"
        self.state.mkdir()
        self.log = tmp / "calls.log"
        self.log.write_text("")
        for name in REAL_TOOLS:
            real = shutil.which(name)
            if real and name not in without:
                (self.bin / name).symlink_to(real)
        for name, body in STUBS.items():
            if name in without:
                continue
            path = self.bin / name
            path.write_text("#!/bin/bash\n" + body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.os_release = tmp / "os-release"
        self.os_release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n")
        self.keyring = tmp / "keyrings" / "tailscale.gpg"
        self.apt_list = tmp / "apt" / "tailscale.list"
        self.dropin = tmp / "sshd_config.d" / "00-geniusnew.conf"
        self.ufw_conf = tmp / "ufw.conf"
        self.authorized_keys = self.home / ".ssh" / "authorized_keys"
        # Like /run on a real host, the directory exists before the tool runs.
        (tmp / "run").mkdir()
        self.lockdown_session = tmp / "run" / "lockdown-session"
        self.deck_unit = tmp / "systemd" / "geniusnew-deck.service"
        self.harpa_credential = tmp / "etc" / "geniusnew" / "harpa-token"
        self.repo = tmp / "repo"
        (self.repo / "tools" / "control_deck").mkdir(parents=True)
        (self.repo / "tools" / "control_deck" / "__main__.py").write_text("")
        self.sshd_config = tmp / "sshd_config"
        # The host's own kernel would make the result depend on where the tests run.
        self.kernel_release = tmp / "osrelease"
        self.kernel_release.write_text("6.8.0-45-generic\n")
        self.sshd_config.write_text("Include /etc/ssh/sshd_config.d/*.conf\n#Match User anoncvs\n")

    def env(self, **extra: str) -> dict[str, str]:
        """Return the sandbox environment with optional overrides for a command invocation."""
        env = {
            "PATH": str(self.bin),
            "HOME": str(self.home),
            "TERM": "dumb",
            "LOG": str(self.log),
            "STATE": str(self.state),
            "FAKE_HOME": str(self.home),
            "GENIUS_OS_RELEASE": str(self.os_release),
            "GENIUS_TS_KEYRING": str(self.keyring),
            "GENIUS_TS_APT_LIST": str(self.apt_list),
            "GENIUS_SSHD_DROPIN": str(self.dropin),
            "GENIUS_UFW_CONF": str(self.ufw_conf),
            "GENIUS_SSHD_CONFIG": str(self.sshd_config),
            "GENIUS_LOCKDOWN_SESSION": str(self.lockdown_session),
            "GENIUS_KERNEL_RELEASE": str(self.kernel_release),
            "GENIUS_DECK_UNIT": str(self.deck_unit),
            "GENIUS_HARPA_CREDENTIAL": str(self.harpa_credential),
            "GENIUS_DECK_SETTLE": "0",
            "GENIUS_REPO_ROOT": str(self.repo),
        }
        env.update(extra)
        return env

    def run(self, *args: str, stdin: str = "", cwd: Path | None = None,
            **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [shutil.which("bash") or "bash", str(SCRIPT), *args],
            input=stdin,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=self.env(**extra),
            timeout=60,
            check=False,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def called(self, prefix: str) -> bool:
        return any(line.startswith(prefix) for line in self.calls())

    def index(self, prefix: str) -> int:
        for i, line in enumerate(self.calls()):
            if line.startswith(prefix):
                return i
        raise AssertionError(f"no call starting with {prefix!r} in {self.calls()}")

    def effective(self, text: str) -> str:
        path = self.tmp / "effective.txt"
        path.write_text(text)
        return str(path)

    def connect_tailscale(self) -> None:
        (self.state / "ts_up").touch()

    def add_key(self, line: str = KEY) -> None:
        self.authorized_keys.parent.mkdir(mode=0o700, exist_ok=True)
        self.authorized_keys.write_text(line + "\n")


MUTATING = ("apt-get", "curl", "systemd-run")


class ServerToolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sb = Sandbox(Path(self._tmp.name))

    def assertNothingChanged(self) -> None:
        for prefix in MUTATING:
            self.assertFalse(self.sb.called(prefix), f"{prefix} ran: {self.sb.calls()}")
        for forbidden in (
            "systemctl enable", "systemctl reload", "systemctl stop", "tailscale up", "sshd -t",
            "ufw default", "ufw allow", "ufw --force",
        ):
            self.assertFalse(self.sb.called(forbidden), f"{forbidden} ran: {self.sb.calls()}")
        self.assertFalse(self.sb.keyring.exists())
        self.assertFalse(self.sb.dropin.exists())


class BasicsTest(ServerToolTestCase):
    def test_the_script_is_executable_and_shell_syntax_is_valid(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_lists_every_command(self):
        result = self.sb.run("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for word in ("status", "setup", "add-key", "lockdown", "confirm", "termius", "--apply"):
            self.assertIn(word, result.stdout)

    def test_an_unknown_command_is_refused(self):
        result = self.sb.run("format-disk")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unbekannter Befehl", result.stderr)

    def test_an_unknown_option_is_refused(self):
        result = self.sb.run("setup", "--force")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unbekannte Option", result.stderr)
        self.assertNothingChanged()

    def test_root_is_refused_as_target_user(self):
        result = self.sb.run("setup", FAKE_USER="root", GENIUS_USER="root")
        self.assertEqual(result.returncode, 1)
        self.assertIn("root", result.stderr)
        self.assertNothingChanged()

    def test_an_invalid_user_name_is_refused(self):
        for name in ("Bad;User", "-x", "1abc", "a" * 40, "a b"):
            with self.subTest(name=name):
                result = self.sb.run("setup", GENIUS_USER=name)
                self.assertEqual(result.returncode, 1)
                self.assertIn("ungültiger Benutzername", result.stderr)
        self.assertNothingChanged()

    def test_a_missing_user_is_refused(self):
        result = self.sb.run("setup", GENIUS_USER="nobodyhome")
        self.assertEqual(result.returncode, 1)
        self.assertIn("existiert nicht", result.stderr)

    def test_an_unsupported_system_is_refused(self):
        self.sb.os_release.write_text("ID=fedora\nVERSION_CODENAME=\n")
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nur Debian oder Ubuntu", result.stderr)
        self.assertNothingChanged()

    def test_a_missing_os_release_is_refused(self):
        self.sb.os_release.unlink()
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Betriebssystem nicht erkennbar", result.stderr)
        self.assertNothingChanged()

    def test_a_system_without_systemd_is_refused(self):
        sb = Sandbox(Path(tempfile.mkdtemp(dir=self._tmp.name)), without=("systemctl",))
        result = sb.run("setup")
        self.assertEqual(result.returncode, 1)
        self.assertIn("systemd wird gebraucht", result.stderr)
        self.assertNotIn("[1/6]", result.stdout)

    def test_wsl_is_refused_before_any_step(self):
        wsl = {
            "kernel": ({}, "5.15.153.1-microsoft-standard-WSL2\n"),
            "distro variable": ({"WSL_DISTRO_NAME": "Ubuntu"}, None),
            "interop variable": ({"WSL_INTEROP": "/run/WSL/1_interop"}, None),
        }
        for name, (env, kernel) in wsl.items():
            for args in (("setup",), ("setup", "--apply"), ("lockdown", "--apply")):
                with self.subTest(name, args=args):
                    if kernel:
                        self.sb.kernel_release.write_text(kernel)
                    self.sb.connect_tailscale()
                    result = self.sb.run(*args, SSH_CONNECTION=TAILSCALE_SESSION, **env)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Das ist WSL", result.stderr)
                    self.assertNotIn("[1/", result.stdout)
                    self.sb.kernel_release.write_text("6.8.0-45-generic\n")
        self.assertNothingChanged()
        self.assertFalse(self.sb.called("ufw"))

    def test_a_missing_codename_is_refused(self):
        self.sb.os_release.write_text("ID=debian\n")
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("VERSION_CODENAME", result.stderr)
        self.assertNothingChanged()

    def test_a_hostile_codename_is_refused(self):
        self.sb.os_release.write_text("ID=debian\nVERSION_CODENAME='x/../y'\n")
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ungültiger Codename", result.stderr)
        self.assertNothingChanged()


class SetupTest(ServerToolTestCase):
    def test_a_dry_run_shows_every_step_and_changes_nothing(self):
        self.sb.add_key()
        result = self.sb.run("setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        for n in range(1, 7):
            self.assertIn(f"[{n}/6]", result.stdout)
        for text in ("TROCKENLAUF", "Befehl:", "apt-get install", "tailscale up", "Warum:", "PasswordAuthentication no"):
            self.assertIn(text, result.stdout)
        self.assertNothingChanged()
        self.assertFalse(self.sb.called("sudo"))
        self.assertFalse(self.sb.called("sshd"))

    def test_apply_runs_the_steps_in_order_and_writes_the_dropin(self):
        self.sb.add_key()
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        order = [
            self.sb.index("apt-get update"),
            self.sb.index("apt-get install -y ca-certificates"),
            self.sb.index("curl"),
            self.sb.index("apt-get install -y tailscale"),
            self.sb.index("systemctl enable --now tailscaled"),
            self.sb.index("tailscale up --hostname=geniusnew-server"),
            self.sb.index("sshd -t"),
            self.sb.index("sshd -T"),
            self.sb.index("systemctl reload ssh"),
        ]
        self.assertEqual(order, sorted(order))
        self.assertEqual(self.sb.keyring.read_text().split()[0], "FAKE-DOWNLOAD")
        text = self.sb.dropin.read_text()
        for line in ("PasswordAuthentication no", "PermitRootLogin no", "KbdInteractiveAuthentication no"):
            self.assertIn(line, text)
        self.assertIn("100.101.102.103", result.stdout)
        self.assertIn("geniusnew-server.tail1234.ts.net", result.stdout)
        self.assertIn("lockdown --apply", result.stdout)

    def test_downloads_use_https_and_a_temp_file(self):
        self.sb.add_key()
        self.sb.run("setup", "--apply")
        curls = [c for c in self.sb.calls() if c.startswith("curl")]
        self.assertEqual(len(curls), 2)
        for call in curls:
            self.assertIn("--proto =https", call)
            self.assertIn("https://pkgs.tailscale.com/stable/ubuntu/noble.", call)
            self.assertIn("-o ", call)

    def test_a_failed_download_leaves_no_key_and_stops(self):
        self.sb.add_key()
        result = self.sb.run("setup", "--apply", FAKE_CURL_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Download fehlgeschlagen", result.stderr)
        self.assertFalse(self.sb.keyring.exists())
        self.assertFalse(self.sb.called("apt-get install -y tailscale"))
        self.assertFalse(self.sb.dropin.exists())

    def test_an_empty_download_leaves_no_key_and_stops(self):
        self.sb.add_key()
        result = self.sb.run("setup", "--apply", FAKE_CURL_EMPTY="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("leere Antwort", result.stderr)
        self.assertFalse(self.sb.keyring.exists())
        self.assertFalse(self.sb.called("apt-get install -y tailscale"))

    def test_a_rejected_auth_key_stops_the_setup(self):
        self.sb.add_key()
        keyfile = Path(self._tmp.name) / "authkey"
        keyfile.write_text("tskey-auth-EXPIRED\n")
        keyfile.chmod(0o600)
        (self.sb.bin / "tailscale").write_text("#!/bin/bash\necho \"tailscale $*\" >>\"$LOG\"\nexit 1\n")
        result = self.sb.run("setup", "--apply", GENIUS_TS_AUTHKEY_FILE=str(keyfile))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Auth-Key abgelaufen oder ungültig", result.stderr)
        self.assertFalse(self.sb.dropin.exists())

    def test_apply_as_root_needs_no_sudo(self):
        self.sb.add_key()
        result = self.sb.run("setup", "--apply", FAKE_UID="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.sb.called("sudo"))

    def test_apply_as_a_normal_user_goes_through_sudo(self):
        self.sb.add_key()
        self.sb.run("setup", "--apply")
        self.assertTrue(self.sb.called("sudo env DEBIAN_FRONTEND=noninteractive apt-get install"))

    def test_hardening_is_skipped_without_a_valid_key(self):
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ÜBERSPRUNGEN", result.stdout)
        self.assertFalse(self.sb.dropin.exists())
        self.assertFalse(self.sb.called("sshd -t"))
        self.assertFalse(self.sb.called("systemctl reload"))
        self.assertIn("add-key", result.stdout)

    def test_hardening_is_skipped_for_an_authorized_keys_file_without_a_key(self):
        self.sb.add_key("# just a comment, and a line that is not a key")
        result = self.sb.run("setup", "--apply")
        self.assertIn("ÜBERSPRUNGEN", result.stdout)
        self.assertFalse(self.sb.dropin.exists())

    def test_hardening_is_rolled_back_when_sshd_rejects_the_config(self):
        self.sb.add_key()
        self.sb.connect_tailscale()
        result = self.sb.run("setup", "--apply", FAKE_SSHD_T_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("zurückgenommen", result.stderr)
        self.assertFalse(self.sb.dropin.exists())
        self.assertFalse(self.sb.called("systemctl reload"))

    def test_an_existing_dropin_is_restored_on_failure(self):
        self.sb.add_key()
        self.sb.connect_tailscale()
        self.sb.dropin.parent.mkdir()
        self.sb.dropin.write_text("# old content\n")
        result = self.sb.run("setup", "--apply", FAKE_SSHD_T_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.sb.dropin.read_text(), "# old content\n")

    def test_hardening_is_rolled_back_when_another_file_overrides_it(self):
        self.sb.add_key()
        self.sb.connect_tailscale()
        override = Path(self._tmp.name) / "effective.txt"
        override.write_text("port 22\npasswordauthentication yes\npermitrootlogin no\n")
        result = self.sb.run("setup", "--apply", FAKE_SSHD_T_OUT=str(override))
        self.assertEqual(result.returncode, 1)
        self.assertIn("zurückgenommen", result.stderr)
        self.assertFalse(self.sb.dropin.exists())
        self.assertFalse(self.sb.called("systemctl reload"))

    def test_hardening_is_rolled_back_when_root_login_stays_possible(self):
        self.sb.add_key()
        self.sb.connect_tailscale()
        override = Path(self._tmp.name) / "effective.txt"
        override.write_text("port 22\npasswordauthentication no\npermitrootlogin yes\n")
        result = self.sb.run("setup", "--apply", FAKE_SSHD_T_OUT=str(override))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.sb.dropin.exists())
        self.assertFalse(self.sb.called("systemctl reload"))

    def test_an_already_connected_server_is_not_logged_in_again(self):
        self.sb.add_key()
        self.sb.connect_tailscale()
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Schon verbunden", result.stdout)
        self.assertFalse(self.sb.called("tailscale up"))

    def test_the_auth_key_file_must_be_private(self):
        self.sb.add_key()
        keyfile = Path(self._tmp.name) / "authkey"
        keyfile.write_text("tskey-auth-SECRET\n")
        keyfile.chmod(0o644)
        result = self.sb.run("setup", "--apply", GENIUS_TS_AUTHKEY_FILE=str(keyfile))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Modus 600", result.stderr)
        self.assertFalse(self.sb.called("tailscale up"))

    def test_a_missing_auth_key_file_is_refused(self):
        self.sb.add_key()
        result = self.sb.run("setup", "--apply", GENIUS_TS_AUTHKEY_FILE=str(Path(self._tmp.name) / "nope"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("fehlt", result.stderr)
        self.assertFalse(self.sb.called("tailscale up"))

    def test_the_auth_key_is_passed_as_a_file_and_never_printed(self):
        self.sb.add_key()
        keyfile = Path(self._tmp.name) / "authkey"
        keyfile.write_text("tskey-auth-SECRET\n")
        keyfile.chmod(0o600)
        result = self.sb.run("setup", "--apply", GENIUS_TS_AUTHKEY_FILE=str(keyfile))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.sb.called(f"tailscale up --hostname=geniusnew-server --auth-key=file:{keyfile}"))
        everything = result.stdout + result.stderr + "\n".join(self.sb.calls())
        self.assertNotIn("tskey-auth-SECRET", everything)

    def test_a_failing_tailscale_login_stops_the_setup(self):
        self.sb.add_key()
        (self.sb.bin / "tailscale").write_text("#!/bin/bash\necho \"tailscale $*\" >>\"$LOG\"\nexit 1\n")
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Anmeldung bei Tailscale fehlgeschlagen", result.stderr)
        self.assertFalse(self.sb.dropin.exists())

    def test_tailscale_without_an_address_fails_the_setup(self):
        self.sb.add_key()
        (self.sb.bin / "tailscale").write_text(
            "#!/bin/bash\necho \"tailscale $*\" >>\"$LOG\"\n[ \"$1\" = up ] || exit 1\n"
        )
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("keine Adresse", result.stderr)
        self.assertFalse(self.sb.dropin.exists())


class HardeningGuardsTest(ServerToolTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sb.add_key()
        self.sb.connect_tailscale()

    def test_a_match_block_in_sshd_config_is_refused_before_any_change(self):
        self.sb.sshd_config.write_text("Match User tester\n  PasswordAuthentication yes\n")
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Match", result.stderr)
        self.assertFalse(self.sb.dropin.exists())
        self.assertFalse(self.sb.called("sshd -t"))
        self.assertFalse(self.sb.called("systemctl reload"))

    def test_a_match_block_in_another_dropin_is_refused(self):
        self.sb.dropin.parent.mkdir()
        (self.sb.dropin.parent / "50-cloud-init.conf").write_text("  match Address 10.0.0.0/8\n")
        result = self.sb.run("setup", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("50-cloud-init.conf", result.stderr)
        self.assertFalse(self.sb.dropin.exists())

    def test_a_dry_run_warns_about_a_match_block(self):
        self.sb.sshd_config.write_text("Match User tester\n")
        result = self.sb.run("setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNUNG: Match-Blöcke", result.stdout)
        self.assertNothingChanged()

    def test_every_written_value_must_hold_after_the_change(self):
        good = {
            "passwordauthentication": "no",
            "kbdinteractiveauthentication": "no",
            "permitrootlogin": "no",
            "pubkeyauthentication": "yes",
        }
        for key, bad in (("passwordauthentication", "yes"), ("kbdinteractiveauthentication", "yes"),
                         ("permitrootlogin", "prohibit-password"), ("pubkeyauthentication", "no")):
            with self.subTest(key=key):
                values = dict(good, **{key: bad})
                text = "port 22\n" + "".join(f"{k} {v}\n" for k, v in values.items())
                result = self.sb.run("setup", "--apply", FAKE_SSHD_T_OUT=self.sb.effective(text))
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"'{key} {good[key]}' gilt nicht", result.stderr)
                self.assertFalse(self.sb.dropin.exists())
        self.assertFalse(self.sb.called("systemctl reload"))

    def test_a_failed_reload_restores_the_previous_dropin(self):
        self.sb.dropin.parent.mkdir()
        self.sb.dropin.write_text("# old content\n")
        result = self.sb.run("setup", "--apply", FAKE_RELOAD_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("neu laden", result.stderr)
        self.assertEqual(self.sb.dropin.read_text(), "# old content\n")

    def test_a_failed_reload_removes_a_new_dropin(self):
        result = self.sb.run("setup", "--apply", FAKE_RELOAD_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.sb.dropin.exists())


class AddKeyTest(ServerToolTestCase):
    def test_a_valid_key_is_stored_privately_once(self):
        for _ in range(2):
            result = self.sb.run("add-key", stdin=KEY + "\n")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sb.authorized_keys.read_text().splitlines(), [KEY])
        self.assertEqual(stat.S_IMODE(self.sb.authorized_keys.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.sb.authorized_keys.parent.stat().st_mode), 0o700)
        self.assertIn("schon eingetragen", result.stdout)
        self.assertIn("SHA256:", result.stdout)

    def test_a_windows_line_ending_is_stripped(self):
        result = self.sb.run("add-key", stdin=KEY + "\r\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sb.authorized_keys.read_text(), KEY + "\n")

    def test_things_that_are_not_a_plain_public_key_are_refused(self):
        refused = {
            "empty": "",
            "words": "hello world",
            "private header": "-----BEGIN OPENSSH PRIVATE KEY-----",
            "options before the key": 'command="/bin/sh" ' + KEY,
            "from option": 'from="*" ' + KEY,
            "key without a body": "ssh-ed25519",
        }
        for name, text in refused.items():
            with self.subTest(name):
                result = self.sb.run("add-key", stdin=text + "\n")
                self.assertEqual(result.returncode, 1)
                expected = "kein Schlüssel eingegeben" if name == "empty" else "kein öffentlicher Schlüssel"
                self.assertIn(expected, result.stderr)
                self.assertFalse(self.sb.authorized_keys.exists())

    def test_an_unreadable_key_is_refused(self):
        result = self.sb.run("add-key", stdin="ssh-ed25519 notbase64 comment\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nicht lesbar", result.stderr)
        self.assertFalse(self.sb.authorized_keys.exists())

    def test_a_short_rsa_key_is_refused_and_a_long_one_accepted(self):
        short = self.sb.run("add-key", stdin="ssh-rsa AAAAB3fakefake bits2048\n")
        self.assertEqual(short.returncode, 1)
        self.assertIn("3072", short.stderr)
        self.assertFalse(self.sb.authorized_keys.exists())
        long = self.sb.run("add-key", stdin="ssh-rsa AAAAB3fakefake bits3072\n")
        self.assertEqual(long.returncode, 0, long.stderr)

    def test_a_last_line_without_newline_is_kept_apart(self):
        self.sb.add_key("ssh-ed25519 AAAAC3oldkey laptop")
        self.sb.authorized_keys.write_text("ssh-ed25519 AAAAC3oldkey laptop")
        result = self.sb.run("add-key", stdin=KEY + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.sb.authorized_keys.read_text().splitlines(), ["ssh-ed25519 AAAAC3oldkey laptop", KEY]
        )

    def test_adding_a_key_never_touches_the_system(self):
        self.sb.run("add-key", stdin=KEY + "\n")
        self.assertNothingChanged()


class LockdownTest(ServerToolTestCase):
    def test_it_needs_tailscale(self):
        sb = Sandbox(Path(tempfile.mkdtemp(dir=self._tmp.name)), without=("tailscale",))
        result = sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Tailscale fehlt", result.stderr)
        self.assertFalse(sb.called("ufw"))

    def test_it_needs_ufw(self):
        sb = Sandbox(Path(tempfile.mkdtemp(dir=self._tmp.name)), without=("ufw",))
        sb.connect_tailscale()
        result = sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ufw fehlt", result.stderr)
        self.assertFalse(sb.called("systemd-run"))

    def test_an_unreadable_firewall_state_is_refused(self):
        self.sb.connect_tailscale()
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION, FAKE_UFW_STATUS_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ufw-Status nicht lesbar", result.stderr)
        self.assertFalse(self.sb.called("systemd-run"))

    def test_it_needs_a_connected_tailscale(self):
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("nicht verbunden", result.stderr)
        self.assertFalse(self.sb.called("ufw"))

    def test_it_refuses_a_session_that_is_not_over_tailscale(self):
        self.sb.connect_tailscale()
        for name, env in {
            "public address": {"SSH_CONNECTION": PUBLIC_SESSION},
            "no ssh session": {},
            "garbage": {"SSH_CONNECTION": "not-an-ip 1 2 3"},
            "just outside the range": {"SSH_CONNECTION": "100.128.0.1 1 2 3"},
        }.items():
            with self.subTest(name):
                result = self.sb.run("lockdown", "--apply", **env)
                self.assertEqual(result.returncode, 1)
                self.assertIn("nicht über Tailscale", result.stderr)
        self.assertFalse(self.sb.called("systemd-run"))
        self.assertFalse(self.sb.called("ufw default"))
        self.assertFalse(self.sb.called("ufw --force"))

    def test_a_dry_run_outside_tailscale_warns_and_changes_nothing(self):
        self.sb.connect_tailscale()
        result = self.sb.run("lockdown", SSH_CONNECTION=PUBLIC_SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNUNG", result.stdout)
        self.assertIn("ufw allow in on tailscale0 to any port 22 proto tcp", result.stdout)
        self.assertNothingChanged()

    def test_apply_sets_the_safety_net_before_the_rules_and_the_rules_before_enabling(self):
        self.sb.connect_tailscale()
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        order = [
            self.sb.index("systemctl stop geniusnew-lockdown-rollback.timer"),
            self.sb.index("systemd-run --unit=geniusnew-lockdown-rollback --on-active=300"),
            self.sb.index("ufw default deny incoming"),
            self.sb.index("ufw default allow outgoing"),
            self.sb.index("ufw allow in on tailscale0 to any port 22 proto tcp"),
            self.sb.index("ufw --force enable"),
        ]
        self.assertEqual(order, sorted(order))
        self.assertTrue(self.sb.called("systemd-run --unit=geniusnew-lockdown-rollback --on-active=300 " + str(self.sb.bin / "ufw") + " --force disable"))
        self.assertIn("confirm", result.stdout)

    def test_apply_never_opens_ssh_to_everybody(self):
        self.sb.connect_tailscale()
        self.sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION)
        for call in self.sb.calls():
            if call.startswith("ufw allow"):
                self.assertIn("on tailscale0", call)

    def test_a_rule_that_keeps_ssh_public_is_refused_before_any_change(self):
        self.sb.connect_tailscale()
        (self.sb.state / "ufw_added").write_text(
            "ufw allow 22/tcp\nufw allow out 53\nufw allow in on tailscale0 to any port 22 proto tcp\n"
        )
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("sudo ufw delete allow 22/tcp", result.stderr)
        self.assertNotIn("delete allow out 53", result.stderr)
        self.assertNotIn("delete allow in on tailscale0", result.stderr)
        self.assertFalse(self.sb.called("systemd-run"))
        self.assertFalse(self.sb.called("ufw default"))
        self.assertFalse(self.sb.called("ufw --force"))

    def test_a_dry_run_warns_about_a_public_rule(self):
        self.sb.connect_tailscale()
        (self.sb.state / "ufw_added").write_text("ufw limit 22/tcp\n")
        result = self.sb.run("lockdown", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("außerhalb Tailscale", result.stdout)
        self.assertNothingChanged()

    def test_a_cgnat_client_on_a_public_server_address_is_refused(self):
        self.sb.connect_tailscale()
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION="100.64.0.7 50000 192.0.2.10 22")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nicht über Tailscale", result.stderr)
        self.assertFalse(self.sb.called("systemd-run"))

    def test_apply_records_the_connection_it_ran_from(self):
        self.sb.connect_tailscale()
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sb.lockdown_session.read_text(), TAILSCALE_SESSION + "\n")
        self.assertEqual(stat.S_IMODE(self.sb.lockdown_session.stat().st_mode), 0o600)
        self.assertLess(self.sb.index("sudo install -m 0600"), self.sb.index("systemd-run"))


    def test_an_ipv6_tailscale_session_is_accepted(self):
        self.sb.connect_tailscale()
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION="fd7a:115c:a1e0::5 50000 fd7a:115c:a1e0::1 22")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.sb.called("ufw --force enable"))

    def test_an_ipv6_session_outside_tailscale_is_refused(self):
        self.sb.connect_tailscale()
        for peer in ("2001:db8::5", "fd7a:115c:a1e1::5", "::ffff:100.64.0.7"):
            with self.subTest(peer=peer):
                result = self.sb.run("lockdown", "--apply", SSH_CONNECTION=f"{peer} 50000 x 22")
                self.assertEqual(result.returncode, 1)
        self.assertFalse(self.sb.called("systemd-run"))

    def test_an_active_firewall_is_left_alone(self):
        self.sb.connect_tailscale()
        (self.sb.state / "ufw_status").write_text("Status: active\n\n22/tcp ALLOW Anywhere\n")
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("schon aktiv", result.stderr)
        self.assertFalse(self.sb.called("systemd-run"))
        self.assertFalse(self.sb.called("ufw default"))
        self.assertFalse(self.sb.called("ufw --force"))

    def test_the_rules_follow_the_ports_sshd_listens_on(self):
        self.sb.connect_tailscale()
        effective = self.sb.effective("port 2222\nport 22\npasswordauthentication no\n")
        result = self.sb.run("lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION, FAKE_SSHD_T_OUT=effective)
        self.assertEqual(result.returncode, 0, result.stderr)
        rules = [c for c in self.sb.calls() if c.startswith("ufw allow")]
        self.assertEqual(rules, [
            "ufw allow in on tailscale0 to any port 22 proto tcp",
            "ufw allow in on tailscale0 to any port 2222 proto tcp",
        ])

    def test_an_unreadable_ssh_port_is_refused(self):
        self.sb.connect_tailscale()
        for text in ("passwordauthentication no\n", "port abc\n", "port 70000\n"):
            with self.subTest(text=text):
                result = self.sb.run(
                    "lockdown", "--apply", SSH_CONNECTION=TAILSCALE_SESSION, FAKE_SSHD_T_OUT=self.sb.effective(text)
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("SSH-Port", result.stderr)
        self.assertFalse(self.sb.called("systemd-run"))


class ConfirmTest(ServerToolTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sb.connect_tailscale()

    def arm(self) -> None:
        """The state right after lockdown --apply: firewall on, safety net set."""
        (self.sb.state / "ufw_status").write_text("Status: active\n")
        (self.sb.state / "timer").write_text("active\n")
        self.sb.lockdown_session.write_text(LOCKDOWN_SESSION + "\n")

    def test_it_refuses_a_session_outside_tailscale(self):
        self.arm()
        result = self.sb.run("confirm", SSH_CONNECTION=PUBLIC_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.sb.called("systemctl stop"))

    def test_it_refuses_when_the_firewall_is_not_active(self):
        result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("nicht aktiv", result.stderr)
        self.assertFalse(self.sb.called("systemctl stop"))

    def test_it_removes_the_safety_net_from_a_new_tailscale_connection(self):
        self.arm()
        result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.sb.called("systemctl stop geniusnew-lockdown-rollback.timer"))
        self.assertIn("Bestätigt", result.stdout)
        self.assertFalse(self.sb.called("ufw --force"))
        self.assertEqual((self.sb.state / "timer").read_text().strip(), "inactive")
        self.assertFalse(self.sb.lockdown_session.exists())

    def test_the_connection_lockdown_ran_from_cannot_confirm(self):
        self.arm()
        result = self.sb.run("confirm", SSH_CONNECTION=LOCKDOWN_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dieselbe Verbindung", result.stderr)
        self.assertFalse(self.sb.called("systemctl stop"))
        self.assertNotIn("Bestätigt", result.stdout)

    def test_an_unknown_lockdown_connection_is_not_confirmed(self):
        self.arm()
        self.sb.lockdown_session.unlink()
        result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unbekannt", result.stderr)
        self.assertFalse(self.sb.called("systemctl stop"))

    def test_a_public_rule_keeps_the_safety_net(self):
        self.arm()
        (self.sb.state / "ufw_added").write_text("ufw allow 22/tcp\n")
        result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("sudo ufw delete allow 22/tcp", result.stderr)
        self.assertFalse(self.sb.called("systemctl stop"))

    def test_a_translated_ufw_does_not_break_confirmation(self):
        self.arm()
        result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION, LC_ALL="de_DE.UTF-8", LANG="de_DE.UTF-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Bestätigt", result.stdout)

    def test_it_needs_ufw(self):
        sb = Sandbox(Path(tempfile.mkdtemp(dir=self._tmp.name)), without=("ufw",))
        sb.connect_tailscale()
        result = sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ufw fehlt", result.stderr)

    def test_a_timer_still_set_after_the_stop_is_not_confirmed(self):
        self.arm()
        result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION, FAKE_STOP_NOOP="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("noch gestellt", result.stderr)
        self.assertNotIn("Bestätigt", result.stdout)

    def test_a_failing_stop_is_not_reported_as_confirmed(self):
        self.arm()
        result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION, FAKE_STOP_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nicht stoppen", result.stderr)
        self.assertNotIn("Bestätigt", result.stdout)

    def test_a_rollback_that_already_fired_is_not_reported_as_confirmed(self):
        (self.sb.state / "ufw_status").write_text("Status: active\n")
        for name, path, content in (
            ("running", "rollback_service", "activating\n"),
            ("queued", "jobs", "42 geniusnew-lockdown-rollback.service start waiting\n"),
        ):
            with self.subTest(name):
                (self.sb.state / path).write_text(content)
                result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION)
                self.assertEqual(result.returncode, 1)
                self.assertIn("schon ausgelöst", result.stderr)
                self.assertNotIn("Bestätigt", result.stdout)
                (self.sb.state / path).unlink()

    def test_an_already_confirmed_firewall_is_confirmed_without_a_stop(self):
        (self.sb.state / "ufw_status").write_text("Status: active\n")
        result = self.sb.run("confirm", SSH_CONNECTION=TAILSCALE_SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.sb.called("systemctl stop"))


class StatusTest(ServerToolTestCase):
    def test_it_only_reads(self):
        self.sb.connect_tailscale()
        result = self.sb.run("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        for title in ("System", "Tailscale", "SSH", "Sitzungen", "Firewall", "Offene Ports", "Dienste",
                      "Was gerade rechnet", "Nächster Schritt"):
            self.assertIn(f"== {title} ==", result.stdout)
        self.assertNothingChanged()

    def test_it_labels_how_far_each_port_reaches(self):
        result = self.sb.run("status")
        lines = {line.split()[1]: line for line in result.stdout.splitlines() if line.strip().startswith("tcp ")}
        self.assertIn("nur lokal", lines["127.0.0.1:5432"])
        self.assertIn("nur Tailscale", lines["100.101.102.103:22"])
        self.assertIn("ALLE ADRESSEN", lines["0.0.0.0:22"])
        self.assertIn("ALLE ADRESSEN", lines["[::]:22"])
        self.assertIn("ALLE ADRESSEN", lines["100.128.0.1:80"])
        self.assertIn("nur Tailscale", lines["[fd7a:115c:a1e0::1]:22"])
        self.assertIn("ALLE ADRESSEN", lines["[fd7a:1::1]:8080"])

    def test_it_works_without_tailscale_and_says_what_to_do(self):
        sb = Sandbox(Path(tempfile.mkdtemp(dir=self._tmp.name)), without=("tailscale",))
        result = sb.run("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nicht installiert", result.stdout)
        self.assertIn("setup --apply", result.stdout.split("Nächster Schritt")[1])

    def test_it_works_as_root_without_a_target_user(self):
        result = self.sb.run("status", FAKE_UID="0", FAKE_USER="root")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("== Nächster Schritt ==", result.stdout)

    def test_the_next_step_follows_the_state_of_the_server(self):
        def next_step() -> str:
            out = self.sb.run("status", GENIUS_UFW_CONF=str(self.sb.ufw_conf)).stdout
            return out.split("== Nächster Schritt ==")[1]

        self.assertIn("setup --apply", next_step())
        self.sb.connect_tailscale()
        self.assertIn("add-key", next_step())
        self.sb.add_key()
        self.assertIn("setup --apply", next_step())
        self.sb.dropin.parent.mkdir()
        self.sb.dropin.write_text("PasswordAuthentication no\n")
        self.assertIn("lockdown --apply", next_step())
        self.sb.ufw_conf.write_text("ENABLED=yes\n")
        (self.sb.state / "timer").write_text("active\n")
        self.assertIn("confirm", next_step())
        (self.sb.state / "timer").write_text("inactive\n")
        self.assertIn("Alles eingerichtet", next_step())

    def test_it_rejects_bad_watch_arguments(self):
        for args, message in ((("status", "--watch", "abc"), "Zahl"), (("status", "--watch", "0"), "mindestens 1"),
                              (("status", "--bogus"), "unbekannte Option")):
            with self.subTest(args=args):
                result = self.sb.run(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertNotIn("== System ==", result.stdout)


class TermiusTest(ServerToolTestCase):
    def test_it_prints_the_host_profile(self):
        self.sb.connect_tailscale()
        result = self.sb.run("termius")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("geniusnew-server.tail1234.ts.net", result.stdout)
        self.assertIn("100.101.102.103", result.stdout)
        self.assertIn("Port         : 22\n", result.stdout)
        self.assertIn("Benutzer     : tester", result.stdout)
        self.assertNothingChanged()


    def test_it_shows_the_port_sshd_really_uses(self):
        self.sb.connect_tailscale()
        result = self.sb.run("termius", FAKE_SSHD_T_OUT=self.sb.effective("port 2222\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Port         : 2222\n", result.stdout)


class DeckTest(ServerToolTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sb.connect_tailscale()

    def test_a_dry_run_shows_the_unit_and_changes_nothing(self):
        result = self.sb.run("deck")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TROCKENLAUF", result.stdout)
        self.assertIn("--host 100.101.102.103 --port 8787", result.stdout)
        self.assertIn("--allow-host geniusnew-server.tail1234.ts.net", result.stdout)
        self.assertFalse(self.sb.deck_unit.exists())
        self.assertFalse(self.sb.called("ufw allow"))
        self.assertFalse(self.sb.called("systemctl enable"))
        self.assertFalse(self.sb.called("systemctl restart"))

    def test_apply_writes_the_unit_opens_tailscale_only_and_starts(self):
        """Install the hardened deck unit and private HARPA token before opening Tailscale access."""
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        unit = self.sb.deck_unit.read_text()
        self.assertIn("User=tester\n", unit)
        self.assertIn(f"WorkingDirectory={self.sb.repo}\n", unit)
        self.assertIn(f"-m tools.control_deck --host 100.101.102.103 --port 8787 --repo {self.sb.repo}", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("ProtectHome=read-only\n", unit)
        self.assertIn(f"LoadCredential=harpa-token:{self.sb.harpa_credential}\n", unit)
        self.assertIn("StateDirectory=geniusnew-control-deck\n", unit)
        self.assertIn("--harpa-token-file %d/harpa-token", unit)
        self.assertIn("--harpa-inbox /var/lib/geniusnew-control-deck/harpa.jsonl", unit)
        self.assertTrue(self.sb.harpa_credential.is_file())
        self.assertEqual(self.sb.harpa_credential.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.sb.called(f"sudo realpath -m -- {self.sb.harpa_credential}"))
        self.assertTrue(self.sb.called(f"sudo realpath -m -- {self.sb.repo}"))
        python_dir = Path(shutil.which("python3", path=str(self.sb.bin))).parent
        self.assertIn(f"Environment=PATH={python_dir}:/usr/local/sbin:", unit)
        self.assertFalse(self.sb.called("ufw delete"))
        order = [
            self.sb.index("systemctl daemon-reload"),
            self.sb.index("systemctl enable geniusnew-deck"),
            self.sb.index("systemctl restart geniusnew-deck"),
            self.sb.index("systemctl is-active geniusnew-deck"),
            self.sb.index("ufw allow in on tailscale0 to any port 8787 proto tcp"),
        ]
        self.assertEqual(order, sorted(order))
        for call in self.sb.calls():
            if call.startswith("ufw allow"):
                self.assertIn("on tailscale0", call)
        self.assertIn("http://100.101.102.103:8787", result.stdout)

    def test_reapplying_the_deck_keeps_the_existing_harpa_token(self):
        """Preserve the original HARPA credential when the deck installation is repeated."""
        self.assertEqual(self.sb.run("deck", "--apply").returncode, 0)
        original = self.sb.harpa_credential.read_text()
        self.assertEqual(self.sb.run("deck", "--apply").returncode, 0)
        self.assertEqual(self.sb.harpa_credential.read_text(), original)

    def test_unsafe_harpa_credential_paths_are_refused(self):
        """Reject relative or unsafe credential paths before writing the service unit."""
        for path, message in (("relative-token", "absolut"),
                              (str(self.sb.tmp / "bad token"), "unsichere Zeichen")):
            with self.subTest(path=path):
                result = self.sb.run("deck", "--apply", GENIUS_HARPA_CREDENTIAL=path)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_empty_generated_harpa_token_is_refused(self):
        """Abort installation before writing the unit if token generation produces no data."""
        python = self.sb.bin / "python3"
        python.unlink()
        real_python = shutil.which("python3")
        python.write_text(f'#!/bin/bash\ncase "$2" in *secrets*) exit 0;; esac\nexec {real_python} "$@"\n')
        python.chmod(0o700)
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("HARPA-Eingangsschlüssel fehlt", result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_harpa_credential_path_resolution_failure_is_refused(self):
        resolver = self.sb.bin / "realpath"
        resolver.unlink()
        resolver.write_text("#!/bin/bash\nexit 0\n")
        resolver.chmod(0o700)
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("kann nicht geprüft werden", result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_harpa_path_probe_requires_coreutils_realpath(self):
        (self.sb.bin / "realpath").unlink()
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("realpath aus coreutils fehlt", result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_harpa_credentials_inside_repository_or_alias_are_refused(self):
        alias = self.sb.tmp / "repo-alias"
        alias.symlink_to(self.sb.repo, target_is_directory=True)
        for root, path in ((self.sb.repo, self.sb.repo),
                           (self.sb.repo, self.sb.repo / "token"),
                           (self.sb.repo, alias / "token"),
                           (alias, self.sb.repo / "token")):
            with self.subTest(root=root, path=path):
                result = self.sb.run("deck", "--apply", GENIUS_HARPA_CREDENTIAL=str(path),
                                     GENIUS_REPO_ROOT=str(root))
                self.assertEqual(result.returncode, 1)
                self.assertIn("nicht im Repository", result.stderr)
        self.assertFalse((self.sb.repo / "token").exists())
        self.assertFalse(self.sb.deck_unit.exists())

    def test_harpa_existing_insecure_modes_are_refused_without_repair(self):
        for directory_mode, token_mode in ((0o700, 0o644), (0o755, 0o600)):
            with self.subTest(directory=directory_mode, token=token_mode):
                self.sb.harpa_credential.parent.mkdir(parents=True, exist_ok=True)
                self.sb.harpa_credential.parent.chmod(directory_mode)
                self.sb.harpa_credential.write_text("private-credential-canary")
                self.sb.harpa_credential.chmod(token_mode)
                result = self.sb.run("deck", "--apply")
                self.assertEqual(result.returncode, 1)
                self.assertIn("privaten Besitz", result.stderr)
                self.assertNotIn("private-credential-canary", result.stdout + result.stderr)
                self.assertEqual(self.sb.harpa_credential.stat().st_mode & 0o777, token_mode)
                self.assertEqual(self.sb.harpa_credential.parent.stat().st_mode & 0o777, directory_mode)
                self.assertEqual(self.sb.harpa_credential.read_text(), "private-credential-canary")
        self.assertFalse(self.sb.deck_unit.exists())

    def test_harpa_existing_foreign_ownership_is_refused(self):
        self.assertEqual(self.sb.run("deck", "--apply").returncode, 0)
        self.sb.deck_unit.unlink()
        original = self.sb.harpa_credential.read_bytes()
        real_stat = shutil.which("stat")
        tool = self.sb.bin / "stat"
        tool.unlink()
        for target in (self.sb.harpa_credential, self.sb.harpa_credential.parent):
            with self.subTest(target=target):
                mode = "600" if target == self.sb.harpa_credential else "700"
                tool.write_text(f'#!/bin/bash\nif [ "$2" = "%u:%a" ] && [ "$3" = "{target}" ]; then echo 999:{mode}; else exec {real_stat} "$@"; fi\n')
                tool.chmod(0o700)
                result = self.sb.run("deck", "--apply")
                self.assertEqual(result.returncode, 1)
                self.assertIn("privaten Besitz", result.stderr)
                self.assertEqual(self.sb.harpa_credential.read_bytes(), original)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_harpa_symlink_credential_and_directory_are_refused(self):
        directory = self.sb.tmp / "private"
        directory.mkdir(mode=0o700)
        token = directory / "token"
        token.write_text("private-credential-canary")
        token.chmod(0o600)
        alias = self.sb.tmp / "private-alias"
        alias.symlink_to(directory, target_is_directory=True)
        token_alias = directory / "token-alias"
        token_alias.symlink_to(token)
        for path in (alias / "token", token_alias):
            with self.subTest(path=path):
                result = self.sb.run("deck", "--apply", GENIUS_HARPA_CREDENTIAL=str(path))
                self.assertEqual(result.returncode, 1)
                self.assertIn("Symlinks", result.stderr)
        self.assertEqual(token.read_text(), "private-credential-canary")
        self.assertFalse(self.sb.deck_unit.exists())

    def test_harpa_privileged_checks_reject_symlinks_after_path_resolution(self):
        """Recheck leaf and parent if the path changes after the canonical-path probe."""
        directory = self.sb.tmp / "private"
        directory.mkdir(mode=0o700)
        token = directory / "token"
        token.write_text("private-credential-canary")
        token.chmod(0o600)
        alias = self.sb.tmp / "private-alias"
        alias.symlink_to(directory, target_is_directory=True)
        token_alias = directory / "token-alias"
        token_alias.symlink_to(token)
        resolver = self.sb.bin / "realpath"
        resolver.unlink()
        # Simulate the path resolver seeing ordinary paths before their replacement.
        resolver.write_text('#!/bin/bash\necho "$3"\n')
        resolver.chmod(0o700)
        for path, message in ((alias / "token", "Schlüsselverzeichnis darf kein Symlink"),
                              (token_alias, "Schlüssel darf kein Symlink")):
            with self.subTest(path=path):
                result = self.sb.run("deck", "--apply", GENIUS_HARPA_CREDENTIAL=str(path))
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
        self.assertEqual(token.read_text(), "private-credential-canary")
        self.assertFalse(self.sb.deck_unit.exists())

    def test_harpa_nonregular_credential_and_parent_are_refused(self):
        self.sb.harpa_credential.parent.mkdir(parents=True, mode=0o700)
        self.sb.harpa_credential.mkdir(mode=0o600)
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("keine reguläre Datei", result.stderr)
        self.sb.harpa_credential.rmdir()
        self.sb.harpa_credential.parent.rmdir()
        self.sb.harpa_credential.parent.write_text("not a directory")
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("kein Verzeichnis", result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_another_port_is_used_everywhere(self):
        result = self.sb.run("deck", "--apply", GENIUS_DECK_PORT="9000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--port 9000", self.sb.deck_unit.read_text())
        self.assertTrue(self.sb.called("ufw allow in on tailscale0 to any port 9000 proto tcp"))

    def test_the_project_venv_is_preferred(self):
        venv = self.sb.repo / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python3").symlink_to(shutil.which("python3"))
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        unit = self.sb.deck_unit.read_text()
        self.assertIn(f"ExecStart={venv / 'python3'} -m tools.control_deck", unit)
        self.assertIn(f"Environment=PATH={venv}:", unit)

    def test_a_port_change_removes_the_rule_for_the_old_port(self):
        self.assertEqual(self.sb.run("deck", "--apply").returncode, 0)
        result = self.sb.run("deck", "--apply", GENIUS_DECK_PORT="9000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.sb.called("ufw delete allow in on tailscale0 to any port 8787 proto tcp"))
        added = (self.sb.state / "ufw_added").read_text()
        self.assertNotIn("port 8787 ", added)
        self.assertIn("port 9000 ", added)

    def test_rerunning_on_the_same_port_keeps_its_rule(self):
        self.assertEqual(self.sb.run("deck", "--apply").returncode, 0)
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.sb.called("ufw delete"))
        self.assertIn("port 8787 ", (self.sb.state / "ufw_added").read_text())

    def test_bad_ports_are_refused(self):
        for port, message in (("abc", "Zahl"), ("80", "zwischen"), ("70000", "zwischen")):
            with self.subTest(port=port):
                result = self.sb.run("deck", "--apply", GENIUS_DECK_PORT=port)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())
        self.assertFalse(self.sb.called("systemctl restart"))

    def test_an_unsafe_repository_path_is_refused(self):
        spaced = Path(self._tmp.name) / "my repo"
        (spaced / "tools" / "control_deck").mkdir(parents=True)
        (spaced / "tools" / "control_deck" / "__main__.py").write_text("")
        for root, message in ((str(spaced), "Zeichen"), ("repo", "absolut")):
            with self.subTest(root=root):
                result = self.sb.run("deck", "--apply", GENIUS_REPO_ROOT=root)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_a_repository_without_the_deck_is_refused(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        result = self.sb.run("deck", "--apply", GENIUS_REPO_ROOT=str(empty))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Control Deck fehlt", result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_an_unsafe_python_path_is_refused(self):
        odd = Path(self._tmp.name) / "py thon"
        odd.mkdir()
        (odd / "python3").symlink_to(shutil.which("python3"))
        relative = Path(self._tmp.name) / "rel"
        relative.mkdir()
        (relative / "python3").symlink_to(shutil.which("python3"))
        for path, cwd, message in ((f"{odd}:{self.sb.bin}", None, "Zeichen"),
                                   (f"rel:{self.sb.bin}", Path(self._tmp.name), "absolut")):
            with self.subTest(message):
                result = self.sb.run("deck", "--apply", PATH=path, cwd=cwd)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"python3-Pfad muss absolut sein" if message == "absolut"
                              else "python3-Pfad enthält Zeichen", result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_missing_python_is_refused(self):
        sb = Sandbox(Path(tempfile.mkdtemp(dir=self._tmp.name)), without=("python3",))
        sb.connect_tailscale()
        result = sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("python3 fehlt", result.stderr)
        self.assertFalse(sb.deck_unit.exists())

    def test_it_needs_systemd_ufw_and_a_connected_tailscale(self):
        for without, connect, message in ((("systemctl",), True, "systemd wird gebraucht"),
                                          (("tailscale",), False, "Tailscale fehlt"),
                                          (("ufw",), True, "ufw fehlt"),
                                          ((), False, "nicht verbunden")):
            with self.subTest(message):
                sb = Sandbox(Path(tempfile.mkdtemp(dir=self._tmp.name)), without=without)
                if connect:
                    sb.connect_tailscale()
                result = sb.run("deck", "--apply")
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertFalse(sb.deck_unit.exists())

    def test_wsl_is_refused(self):
        self.sb.kernel_release.write_text("5.15.153.1-microsoft-standard-WSL2\n")
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Das ist WSL", result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_root_is_refused_as_service_user(self):
        result = self.sb.run("deck", "--apply", FAKE_USER="root", GENIUS_USER="root")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_a_deck_that_does_not_stay_up_is_reported(self):
        (self.sb.state / "deck").write_text("failed\n")
        result = self.sb.run("deck", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("journalctl -u geniusnew-deck", result.stderr)
        self.assertIn("Firewall-Regel für Port 8787 entfernt", result.stderr)
        self.assertNotIn("Dashboard läuft", result.stdout)
        self.assertTrue(self.sb.called("systemctl disable --now geniusnew-deck"))
        self.assertTrue(self.sb.called("ufw delete allow in on tailscale0 to any port 8787 proto tcp"))
        self.assertFalse(self.sb.called("ufw allow"))

    def test_a_failure_before_the_start_opens_no_firewall_rule(self):
        result = self.sb.run("deck", "--apply", FAKE_DAEMON_RELOAD_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.sb.called("ufw allow"))
        self.assertNotIn("Dashboard läuft", result.stdout)

    def test_an_unexpected_tailscale_address_is_refused(self):
        result = self.sb.run("deck", "--apply", FAKE_TS_IP4="100.1.2.3 --repo /tmp")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unerwartete Tailscale-Adresse", result.stderr)
        self.assertFalse(self.sb.deck_unit.exists())

    def test_a_failed_cleanup_after_a_failed_start_is_reported(self):
        (self.sb.state / "deck").write_text("failed\n")
        result = self.sb.run("deck", "--apply", FAKE_DISABLE_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Aufräumen fehlgeschlagen", result.stderr)
        self.assertNotIn("entfernt", result.stderr)

    def test_status_suggests_the_deck_once_everything_else_is_done(self):
        self.sb.add_key()
        self.sb.dropin.parent.mkdir()
        self.sb.dropin.write_text("PasswordAuthentication no\n")
        self.sb.ufw_conf.write_text("ENABLED=yes\n")
        out = self.sb.run("status").stdout.split("== Nächster Schritt ==")[1]
        self.assertIn("deck --apply", out)
        self.sb.deck_unit.parent.mkdir()
        self.sb.deck_unit.write_text("x")
        out = self.sb.run("status").stdout.split("== Nächster Schritt ==")[1]
        self.assertNotIn("deck --apply", out)


if __name__ == "__main__":
    unittest.main()
