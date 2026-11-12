#!/usr/bin/env bash
set -euo pipefail

# Noble renamed the native package for the time_t transition. Either installed
# name already satisfies this action; do not make a working runner depend on APT.
for package in libpipewire-0.3-0 libpipewire-0.3-0t64; do
  if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null)" == 'install ok installed' ]]; then
    echo "Native dependency already installed: $package"
    exit 0
  fi
done

source /etc/os-release
if [[ "${ID:-}" != ubuntu || ! "${VERSION_CODENAME:-}" =~ ^[a-z][a-z0-9-]*$ ]]; then
  echo '::error::Native dependency installation requires an Ubuntu release codename.'
  exit 1
fi
architecture=$(dpkg --print-architecture)

work_dir=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/maafgo-native-deps.XXXXXX")
cleanup() {
  # This is exclusively the directory created above, never a system APT directory.
  sudo -n rm -rf -- "$work_dir"
}
trap cleanup EXIT
chmod 755 "$work_dir"
mkdir "$work_dir/empty-sources"

# The previous mirror+file setup retried every Packages/Translation URI before
# falling back. A request timeout is not a deadline for the entire apt process.
# Three attempts, each with 75s update + 75s install and two 5s kill graces,
# take at most 480s, leaving room inside the callers' 10-minute step timeout.
mirrors=(
  https://mirrors.mit.edu/ubuntu/
  https://archive.ubuntu.com/ubuntu/
  https://mirrors.edge.kernel.org/ubuntu/
)
for index in "${!mirrors[@]}"; do
  mirror=${mirrors[$index]}
  attempt_dir="$work_dir/mirror-$index"
  mkdir -p "$attempt_dir/lists"
  for suite in "$VERSION_CODENAME" "$VERSION_CODENAME-updates" "$VERSION_CODENAME-security"; do
    printf 'deb [arch=%s signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] %s %s main universe\n' \
      "$architecture" "$mirror" "$suite"
  done > "$attempt_dir/ubuntu.list"

  # Only this mirror and the native architecture participate in this attempt.
  # Ignore unrelated preinstalled sources (e.g. Microsoft), retain signature
  # checks, and never reuse partial indices from a failed mirror.
  apt_options=(
    -o "Dir::Etc::sourcelist=$attempt_dir/ubuntu.list"
    -o "Dir::Etc::sourceparts=$work_dir/empty-sources"
    -o "Dir::State::lists=$attempt_dir/lists"
    -o Acquire::Languages=none
    -o Acquire::Retries=0
    -o Acquire::http::Timeout=10
    -o Acquire::https::Timeout=10
    -o DPkg::Lock::Timeout=10
  )
  echo "Installing native dependency from $mirror"
  if sudo -n timeout --kill-after=5s 75s apt-get "${apt_options[@]}" -o APT::Update::Error-Mode=any update; then
    if sudo -n timeout --kill-after=5s 75s apt-get "${apt_options[@]}" install -y --no-install-recommends libpipewire-0.3-0; then
      exit 0
    else
      status=$?
      echo "::warning::Native dependency install failed for $mirror (exit $status); trying the next mirror."
    fi
  else
    status=$?
    echo "::warning::APT index update failed for $mirror (exit $status); trying the next mirror."
  fi
done

echo '::error::Native dependency installation failed on all mirrors. Retry the workflow and inspect the APT errors above.'
exit 1
