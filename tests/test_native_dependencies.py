"""Execute the native-dependency installer with isolated APT/timeout stubs."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from test_resource_workflow import find_bash


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / ".github/actions/install-native-deps/install.sh"
BASH = find_bash()

# Keep filesystem operations confined to TemporaryDirectory. In particular,
# sudo's rm branch only records the requested cleanup: Python owns deletion.
STUBS = r'''
set -euo pipefail
record() { printf '%s\n' "$*" >> "$NATIVE_TEST_LOG"; }
source() {
  [[ "$#" == 1 && "$1" == /etc/os-release ]] || return 92
  ID="$NATIVE_TEST_OS"
  VERSION_CODENAME="$NATIVE_TEST_CODENAME"
}
dpkg-query() {
  [[ "$#" == 3 && "$1" == -W && "$2" == '-f=${Status}' ]] || return 93
  local package="$3"
  record $'QUERY\t'"$package"
  if [[ "$package" == "$NATIVE_TEST_INSTALLED" ]]; then
    printf 'install ok installed'
    return 0
  fi
  return 1
}
dpkg() {
  [[ "$#" == 1 && "$1" == --print-architecture ]] || return 94
  printf '%s\n' "$NATIVE_TEST_ARCH"
}
sudo() {
  [[ "${1:-}" == -n ]] || return 95
  shift
  if [[ "${1:-}" == rm ]]; then
    # Never execute a shell recursive delete, including on Git Bash/Windows.
    [[ "$#" == 4 && "$2" == -rf && "$3" == -- ]] || return 96
    [[ "$4" == "$RUNNER_TEMP"/maafgo-native-deps.* ]] || return 97
    record $'CLEANUP\t'"$4"
    return 0
  fi
  [[ "${1:-}" == timeout ]] || return 98
  "$@"
}
timeout() {
  record $'TIMEOUT\t'"$1"$'\t'"$2"
  [[ "$1" == --kill-after=5s && "$2" == 75s && "$3" == apt-get ]] || return 99
  shift 2
  "$@"
}
apt-get() {
  local phase=install source_file= lists= parts= argument allow_downgrades=false
  for argument in "$@"; do
    case "$argument" in
      update) phase=update ;;
      Dir::Etc::sourcelist=*) source_file="${argument#*=}" ;;
      Dir::Etc::sourceparts=*) parts="${argument#*=}" ;;
      Dir::State::lists=*) lists="${argument#*=}" ;;
      --allow-downgrades) allow_downgrades=true ;;
    esac
  done
  [[ -n "$source_file" && -n "$lists" && -n "$parts" ]] || return 90
  record $'APT\t'"$phase"$'\t'"$source_file"$'\t'"$lists"$'\t'"$parts"
  for argument in "$@"; do
    record $'ARG\t'"$phase"$'\t'"$argument"
  done
  if [[ "$NATIVE_TEST_SCENARIO" == downgrade_required && "$phase" == install && "$allow_downgrades" == false ]]; then
    echo 'E: Packages were downgraded and -y was used without --allow-downgrades.' >&2
    return 100
  fi
  case "$NATIVE_TEST_SCENARIO:$phase:$source_file" in
    update_fail:update:*/mirror-0/ubuntu.list) return 100 ;;
    update_timeout:update:*/mirror-0/ubuntu.list) return 124 ;;
    update_killed:update:*/mirror-0/ubuntu.list) return 137 ;;
    install_fail:install:*/mirror-0/ubuntu.list) return 100 ;;
    all_update_fail:update:*) return 100 ;;
    all_install_fail:install:*) return 100 ;;
  esac
  return 0
}
'''


def local_path(value):
    """Accept both drive paths and Git Bash's /c/... rendering of a temp path."""
    if os.name == "nt" and len(value) > 3 and value[0] == "/" and value[2] == "/":
        value = value[1] + ":" + value[2:]
    return Path(value)


@unittest.skipUnless(BASH, "bash required for native dependency shell verification")
class NativeDependencyInstallerTests(unittest.TestCase):
    def execute(self, scenario="success", *, installed="", os_id="ubuntu",
                codename="noble", architecture="amd64"):
        with tempfile.TemporaryDirectory(prefix="maafgo native deps ") as directory:
            folder = Path(directory)
            log = folder / "commands.log"
            environment = dict(os.environ, RUNNER_TEMP=folder.as_posix(),
                               NATIVE_TEST_LOG=log.as_posix(),
                               NATIVE_TEST_SCENARIO=scenario,
                               NATIVE_TEST_INSTALLED=installed,
                               NATIVE_TEST_OS=os_id,
                               NATIVE_TEST_CODENAME=codename,
                               NATIVE_TEST_ARCH=architecture)
            result = subprocess.run(
                [BASH, "--noprofile", "--norc"], input=STUBS + INSTALLER.read_text("utf-8"),
                cwd=folder, env=environment, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10,
            )
            events = [line.split("\t") for line in log.read_text("utf-8").splitlines()] if log.exists() else []
            apt = [event[1:] for event in events if event[0] == "APT"]
            sources = {event[1]: local_path(event[1]).read_text("utf-8") for event in apt}
            parts = {event[3]: list(local_path(event[3]).iterdir()) for event in apt}
            lists_exist = {event[2]: local_path(event[2]).is_dir() for event in apt}
            return SimpleNamespace(process=result, events=events, apt=apt, sources=sources,
                                   parts=parts, lists_exist=lists_exist, temp=folder.as_posix())

    def assert_code(self, outcome, code):
        self.assertEqual(outcome.process.returncode, code,
                         outcome.process.stdout + outcome.process.stderr)

    def test_installed_legacy_or_t64_package_skips_all_network_work(self):
        for package, queries in (("libpipewire-0.3-0", 1), ("libpipewire-0.3-0t64", 2)):
            with self.subTest(package=package):
                outcome = self.execute(installed=package)
                self.assert_code(outcome, 0)
                self.assertEqual(len(outcome.events), queries)
                self.assertTrue(all(event[0] == "QUERY" for event in outcome.events))
                self.assertEqual(outcome.apt, [])

    def test_resolute_can_align_preinstalled_libraries_with_mirror_dependencies(self):
        outcome = self.execute("downgrade_required", codename="resolute")
        self.assert_code(outcome, 0)
        self.assertEqual([event[0] for event in outcome.apt], ["update", "install"])
        install = [event[2] for event in outcome.events if event[0] == "ARG" and event[1] == "install"]
        self.assertIn("--allow-downgrades", install)
        self.assertIn("--no-remove", install)

    def test_success_updates_then_installs_once_and_cleans_only_its_temp_directory(self):
        outcome = self.execute()
        self.assert_code(outcome, 0)
        self.assertEqual([event[0] for event in outcome.apt], ["update", "install"])
        self.assertEqual(outcome.apt[0][1:], outcome.apt[1][1:])
        cleanup = [event[1] for event in outcome.events if event[0] == "CLEANUP"]
        self.assertEqual(len(cleanup), 1)
        self.assertTrue(cleanup[0].startswith(outcome.temp + "/maafgo-native-deps."))

    def test_update_failure_skips_install_and_moves_to_an_independent_mirror(self):
        outcome = self.execute("update_fail")
        self.assert_code(outcome, 0)
        self.assertEqual([event[0] for event in outcome.apt], ["update", "update", "install"])
        self.assertIn("exit 100", outcome.process.stdout)
        first, second = [outcome.sources[event[1]] for event in outcome.apt[:2]]
        self.assertIn("https://mirrors.mit.edu/ubuntu/", first)
        self.assertIn("https://archive.ubuntu.com/ubuntu/", second)

    def test_timeout_and_forced_kill_statuses_survive_set_e_and_trigger_failover(self):
        for scenario, status in (("update_timeout", 124), ("update_killed", 137)):
            with self.subTest(status=status):
                outcome = self.execute(scenario)
                self.assert_code(outcome, 0)
                self.assertEqual([event[0] for event in outcome.apt], ["update", "update", "install"])
                self.assertIn(f"exit {status}", outcome.process.stdout)
                self.assertNotIn("exit 0", outcome.process.stdout)

    def test_install_failure_reloads_indices_from_the_next_mirror(self):
        outcome = self.execute("install_fail")
        self.assert_code(outcome, 0)
        self.assertEqual([event[0] for event in outcome.apt], ["update", "install", "update", "install"])
        first, second = outcome.apt[0], outcome.apt[2]
        self.assertNotEqual(first[1], second[1])
        self.assertNotEqual(first[2], second[2])
        self.assertIn("exit 100", outcome.process.stdout)

    def test_all_failed_updates_return_failure_without_attempting_install(self):
        outcome = self.execute("all_update_fail")
        self.assert_code(outcome, 1)
        self.assertEqual([event[0] for event in outcome.apt], ["update"] * 3)
        self.assertEqual(len(outcome.sources), 3)
        self.assertIn("::error::", outcome.process.stdout)
        self.assertEqual(sum(event[0] == "CLEANUP" for event in outcome.events), 1)

    def test_all_failed_installs_remain_failure_and_fit_the_step_budget(self):
        outcome = self.execute("all_install_fail")
        self.assert_code(outcome, 1)
        self.assertEqual([event[0] for event in outcome.apt], ["update", "install"] * 3)
        deadlines = [event[1:] for event in outcome.events if event[0] == "TIMEOUT"]
        self.assertEqual(len(deadlines), 6)
        worst_case = sum(int(duration.removesuffix("s")) + int(kill.split("=", 1)[1].removesuffix("s"))
                         for kill, duration in deadlines)
        self.assertLess(worst_case, 10 * 60)
        mirrors = [outcome.sources[event[1]] for event in outcome.apt[::2]]
        self.assertIn("https://mirrors.mit.edu/ubuntu/", mirrors[0])
        self.assertIn("https://archive.ubuntu.com/ubuntu/", mirrors[1])
        self.assertIn("https://mirrors.edge.kernel.org/ubuntu/", mirrors[2])

    def test_attempts_use_signed_native_architecture_sources_without_third_parties(self):
        for codename, architecture in (("noble", "amd64"), ("jammy", "amd64"), ("noble", "arm64")):
            with self.subTest(codename=codename, architecture=architecture):
                outcome = self.execute("all_update_fail", codename=codename, architecture=architecture)
                self.assert_code(outcome, 1)
                self.assertEqual(len({event[2] for event in outcome.apt}), 3)
                self.assertTrue(all(outcome.lists_exist.values()))
                self.assertTrue(all(not contents for contents in outcome.parts.values()))
                for contents in outcome.sources.values():
                    lines = contents.splitlines()
                    self.assertEqual(len(lines), 3)
                    self.assertEqual([line.split()[-3] for line in lines],
                                     [codename, codename + "-updates", codename + "-security"])
                    for line in lines:
                        self.assertIn(f"[arch={architecture} signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg]", line)
                        self.assertTrue(line.endswith("main universe"))
                        self.assertNotIn("packages.microsoft.com", line)
                        self.assertNotIn("trusted=yes", line)

    def test_apt_request_and_lock_options_are_bounded_on_both_phases(self):
        outcome = self.execute()
        self.assert_code(outcome, 0)
        for phase in ("update", "install"):
            arguments = [event[2] for event in outcome.events if event[0] == "ARG" and event[1] == phase]
            for option in ("Acquire::Languages=none", "Acquire::Retries=0", "Acquire::http::Timeout=10",
                           "Acquire::https::Timeout=10", "DPkg::Lock::Timeout=10"):
                self.assertIn(option, arguments)
            self.assertTrue(any(value.startswith("Dir::Etc::sourceparts=") for value in arguments))
            self.assertEqual("APT::Update::Error-Mode=any" in arguments, phase == "update")
        install = [event[2] for event in outcome.events if event[0] == "ARG" and event[1] == "install"]
        self.assertIn("--no-install-recommends", install)
        self.assertIn("--allow-downgrades", install)
        self.assertIn("--no-remove", install)
        self.assertNotIn("--force-yes", install)
        self.assertNotIn("--allow-unauthenticated", install)
        self.assertIn("libpipewire-0.3-0", install)

    def test_unsupported_os_or_invalid_codename_fails_without_apt(self):
        for os_id, codename in (("debian", "bookworm"), ("ubuntu", ""), ("ubuntu", "noble; id")):
            with self.subTest(os_id=os_id, codename=codename):
                outcome = self.execute(os_id=os_id, codename=codename)
                self.assert_code(outcome, 1)
                self.assertEqual(outcome.apt, [])
                self.assertFalse(any(event[0] == "TIMEOUT" for event in outcome.events))


class NativeDependencyActionTests(unittest.TestCase):
    def test_action_executes_the_reviewed_installer(self):
        action = (ROOT / ".github/actions/install-native-deps/action.yml").read_text("utf-8")
        self.assertIn('bash "$GITHUB_ACTION_PATH/install.sh"', action)
        self.assertNotIn("mirror+file:", action)


if __name__ == "__main__":
    unittest.main()
