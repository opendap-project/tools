# shellcheck shell=sh
# The command-line behaviour every odapkit shell tool shares (command standard §5, §6), sourced (POSIX sh).
# The Python tools use the same contract through odw.clikit. Spec: docs/specs/cli-conformance.md.
#
#   CLI_TOOL                       the tool as people type it (`odapkit qemu`); the default comes from $0
#   CLI_JSON=1                     errors also print an opendap.error/v1 document on stdout
#   cli_fail CODE MSG HINT FIX [STATUS]
#                                  an error an agent can act on: a stable dotted code, a hint and a fix
#                                  (a command to run) on stderr; exits STATUS (default 1, 2 for usage)
#   cli_usage MSG                  cli_fail "input.invalid" MSG, pointing at --help; exits 2
#   cli_json_str TEXT              TEXT as a JSON string
#   cli_interactive                true only when a person can answer a prompt: stdin and stdout are
#                                  terminals, and neither CI nor ODAP_NONINTERACTIVE is set

: "${CLI_TOOL:=odapkit ${0##*/odapkit-}}"
: "${CLI_JSON:=0}"

cli_json_str() {
  printf '%s' "$1" | awk 'BEGIN { ORS = "" } {
    gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); gsub(/\t/, "\\t"); gsub(/\r/, "\\r")
    if (NR > 1) print "\\n"; print
  } END { }' | { printf '"'; cat; printf '"\n'; }
}

cli_fail() {
  printf '%s: error %s: %s\n  hint: %s\n  fix: %s\n' "$CLI_TOOL" "$1" "$2" "$3" "$4" >&2
  if [ "$CLI_JSON" = 1 ]; then
    printf '{"schema": "opendap.error/v1", "ok": false, "error": {"code": %s, "message": %s, "hint": %s, "fix": [{"command": %s}]}}\n' \
      "$(cli_json_str "$1")" "$(cli_json_str "$2")" "$(cli_json_str "$3")" "$(cli_json_str "$4")"
  fi
  exit "${5:-1}"
}

cli_usage() { cli_fail "input.invalid" "$1" "the usage is in $CLI_TOOL --help" "$CLI_TOOL --help" 2; }

cli_interactive() { [ -t 0 ] && [ -t 1 ] && [ -z "${CI:-}" ] && [ -z "${ODAP_NONINTERACTIVE:-}" ]; }

# cli_summary NAME FILE            the summary FILE (a root's tools.toml) declares for tool NAME
cli_summary() {
  awk -v n="$1" '
    /^\[\[tool\]\]/ { name = "" }
    /^name *= *"/ { v = $0; sub(/^name *= *"/, "", v); sub(/".*$/, "", v); name = v }
    /^summary *= *"/ && name == n { v = $0; sub(/^summary *= *"/, "", v); sub(/" *$/, "", v); print v; exit }
  ' "$2" 2>/dev/null || true
}

# cli_help FILE LAST               --help: `<front> <tool>: <summary>` from the root's tools.toml (falling back to
#                                  the header's first line), then lines 3 to LAST of FILE's header comment
cli_help() {
  s="$(cli_summary "${CLI_TOOL##* }" "$(dirname "$1")/../tools.toml")"
  [ -n "$s" ] || s="$(sed -n '2s/^# \{0,1\}//p' "$1")"
  printf '%s: %s\n' "$CLI_TOOL" "$s"
  sed -n "3,${2}p" "$1" | sed 's/^# \{0,1\}//'
}
