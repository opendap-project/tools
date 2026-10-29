#!/bin/sh
# The command-line contract of every toolkit-image tool (command standard §6), run without a device,
# a VM or the network: --help, an unknown flag, --json documents and --dry-run, on temporary folders.
# Usage: tests/cli.sh   (exit 0 when every case passes; each failure is printed)
set -u
# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a tool's git would act on the repository being committed.
for v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0
fail() { echo "FAIL: $*" >&2; fails=$((fails + 1)); }

# Isolated host folders: no key, no sysroot, no cache of the person running the tests.
export XDG_DATA_HOME="$tmp/data" XDG_CACHE_HOME="$tmp/cache" ODAP_SYSROOT_DIR="$tmp/sysroot"
export ODAP_BLACKBIRD_DIR="$tmp/bb"
mkdir -p "$tmp/bb/os/image/mkosi.profiles/qemu" "$tmp/bb/os/image/mkosi.profiles/sysroot" "$tmp/bb/emulator/export/bsp" \
  "$tmp/bb/ui/export/fonts"
printf 'OTTO' > "$tmp/bb/ui/export/fonts/Test.otf"

run() { out="$("$@" 2>"$tmp/err")"; rc=$?; err="$(cat "$tmp/err")"; }
json_schema() { printf '%s' "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["schema"])' 2>/dev/null; }

for t in cross deploy dev-key image security-check sysroot; do
  exe="$root/bin/odapkit-$t"
  summary="$(awk -F'"' -v n="$t" '/^name *=/ { name = $2 } /^summary *=/ && name == n { print $2; exit }' "$root/tools.toml")"
  [ -n "$summary" ] || fail "$t: not declared in tools.toml"
  run "$exe" --help
  [ "$rc" = 0 ] || fail "$t --help: exit $rc"
  [ "$(printf '%s\n' "$out" | head -1)" = "odapkit $t: $summary" ] || fail "$t --help: first line '$(printf '%s\n' "$out" | head -1)'"
  run "$exe" --odw-no-such-flag
  [ "$rc" = 2 ] || fail "$t unknown flag: exit $rc"
  case "$err" in *"error input.invalid:"*"hint: "*"fix: "*) ;; *) fail "$t unknown flag: stderr '$err'" ;; esac
done

# The toolkit-host lookup of lib/odapkit.sh and security-check, in both toolkit layouts.
(cd "$root" && python3 -m unittest -q tests.test_host_lookup 2>"$tmp/err") || fail "host lookup: $(cat "$tmp/err")"
# Other repositories' checkouts, merged into the blackbird root or not (HL-84).
(cd "$root" && python3 -m unittest -q tests.test_repo_lookup 2>"$tmp/err") || fail "repo lookup: $(cat "$tmp/err")"

run "$root/bin/odapkit-dev-key" create --dry-run
[ "$rc" = 0 ] && [ ! -e "$tmp/data/opendap" ] || fail "dev-key create --dry-run: exit $rc or wrote $tmp/data"
run "$root/bin/odapkit-dev-key" path --json
[ "$(json_schema "$out")" = opendap.dev-key/v1 ] || fail "dev-key path --json: $out"

run "$root/bin/odapkit-sysroot" path --json
[ "$(json_schema "$out")" = opendap.sysroot/v1 ] || fail "sysroot path --json: $out"
run "$root/bin/odapkit-sysroot" install --no-build --dry-run
[ "$rc" = 1 ] && [ ! -e "$tmp/sysroot" ] || fail "sysroot install --dry-run without a build: exit $rc"
case "$err" in *"would fail"*) ;; *) fail "sysroot install --dry-run: stderr '$err'" ;; esac

run "$root/bin/odapkit-image" path qemu --json
[ "$(json_schema "$out")" = opendap.image-output/v1 ] || fail "image path --json: $out"
run "$root/bin/odapkit-image" build qemu --dry-run
[ "$rc" = 0 ] && [ ! -e "$tmp/data/opendap" ] && [ ! -e "$tmp/cache/opendap" ] || fail "image build --dry-run: exit $rc, $err"
case "$err" in *"would build qemu"*) ;; *) fail "image build --dry-run: stderr '$err'" ;; esac
run "$root/bin/odapkit-image" build nope --dry-run --json
[ "$rc" = 2 ] && [ "$(json_schema "$out")" = opendap.error/v1 ] || fail "image build nope: exit $rc, $out"

mkdir -p "$tmp/ws"
printf '[workspace]\n\n[workspace.metadata.odap]\ndeploy = ["odap-a", "odap-b"]\nrestart = ["odap-a.service"]\n' > "$tmp/ws/Cargo.toml"
out="$(cd "$tmp/ws" && "$root/bin/odapkit-deploy" --dry-run --json 2>"$tmp/err")"; rc=$?
[ "$rc" = 0 ] && [ "$(json_schema "$out")" = opendap.deploy/v1 ] || fail "deploy --dry-run --json: exit $rc, $out $(cat "$tmp/err")"
[ ! -e "$tmp/ws/target" ] || fail "deploy --dry-run built"

out="$(cd "$tmp/ws" && "$root/bin/odapkit-cross" --dry-run --json build -p odap-a 2>"$tmp/err")"; rc=$?
[ "$(json_schema "$out")" = opendap.cross-command/v1 ] || fail "cross --dry-run --json: exit $rc, $out $(cat "$tmp/err")"

run "$root/bin/odapkit-security-check" --json
[ "$rc" = 2 ] && [ "$(json_schema "$out")" = opendap.error/v1 ] || fail "security-check without manifests: exit $rc, $out"

[ "$fails" = 0 ] && echo "tests/cli.sh: every case passes" >&2
[ "$fails" = 0 ]
