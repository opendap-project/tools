#!/bin/sh
# The command-line contract of odw motion (command standard §6), without a screen, a VM or a
# recording: --help, an unknown flag, error codes and --dry-run.
# Usage: tests/cli.sh   (exit 0 when every case passes; each failure is printed)
set -u
# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a tool's git would act on the repository being committed.
for v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0
fail() { echo "FAIL: $*" >&2; fails=$((fails + 1)); }
run() { out="$("$@" 2>"$tmp/err")"; rc=$?; err="$(cat "$tmp/err")"; }
exe="$root/bin/odw-motion"

summary="$(awk -F'"' '/^summary *=/ { print $2; exit }' "$root/tools.toml")"
run "$exe" --help
[ "$rc" = 0 ] && [ "$(printf '%s\n' "$out" | head -1)" = "odw motion: $summary" ] || fail "--help: exit $rc, '$(printf '%s\n' "$out" | head -1)'"
run "$exe" --odw-no-such-flag
[ "$rc" = 2 ] || fail "unknown flag: exit $rc"
case "$err" in *"error input.invalid:"*"hint: "*"fix: "*) ;; *) fail "unknown flag: stderr '$err'" ;; esac
run "$exe" analyze "$tmp/none.mp4" --json
schema="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["error"]["code"])' 2>/dev/null)"
if command -v ffprobe >/dev/null; then
  [ "$rc" = 2 ] && [ "$schema" = recording.not_found ] || fail "analyze a missing file: exit $rc, $out"
fi
run "$exe" record --qmp "$tmp/no.sock" --dry-run
if command -v ffprobe >/dev/null; then
  case "$rc:$err" in 2:*"error qmp.not_found:"*) ;; *) fail "record --dry-run, no socket: exit $rc, $err" ;; esac
fi
run "$exe" record --region 0,0,0,1
[ "$rc" = 2 ] || fail "record with an empty region: exit $rc"

[ "$fails" = 0 ] && echo "tests/cli.sh: every case passes" >&2
[ "$fails" = 0 ]
