# shellcheck shell=sh
# toolkit-host's command-line helpers (lib/cli.sh) for this repository's shell tools, sourced (POSIX sh):
# cli_fail CODE MSG HINT FIX [STATUS], cli_usage MSG, cli_json_str, cli_summary NAME FILE (command standard §6).
# toolkit-host is host/ of this repository when this checkout is one of its folders (the merged tools root), else
# ODAP_TOOLKIT_HOST_DIR (odapkit sets it), else the workspace's repos.tsv; without it,
# the same error format in its smallest form. cli_root is this checkout.
cli_root="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." && pwd)"
: "${CLI_TOOL:=odapkit ${0##*/odapkit-}}"
CLI_JSON="${CLI_JSON:-0}"

# True when FILE belongs to the user running the tool (POSIX: no test -O).
_cli_mine() { [ -n "$(find "$1" -prune -user "$(id -u)" 2>/dev/null)" ]; }

# host/ beside this folder in the same repository (the merged tools root): the nearest folder at or above
# cli_root with a .git is the repository's top; when that is not cli_root itself, its host/ holds the helpers.
# The .git and the helpers must belong to this user.
_cli_sibling() {
  d="$cli_root"
  while [ ! -e "$d/.git" ]; do
    [ "$d" = / ] && return 1
    d="$(dirname "$d")"
  done
  [ "$d" != "$cli_root" ] && _cli_mine "$d/.git" && [ -f "$d/host/lib/cli.sh" ] && _cli_mine "$d/host/lib/cli.sh" &&
    echo "$d/host"
}

_cli_host() {
  _cli_sibling && return 0
  [ -n "${ODAP_TOOLKIT_HOST_DIR:-}" ] && { echo "$ODAP_TOOLKIT_HOST_DIR"; return 0; }
  d="$cli_root"
  while [ "$d" != / ]; do
    # Only a workspace this user owns: a repos.tsv someone else wrote never names code to source.
    if [ -f "$d/repos.tsv" ] && _cli_mine "$d/repos.tsv"; then
      p="$(awk -F'\t' 'NR > 1 { n = split($2, a, "/"); if (a[n] == "toolkit-host" && $1 !~ /(^\/|\.\.)/) print $1 }' "$d/repos.tsv")"
      [ -n "$p" ] && echo "$d/$p"
      return 0
    fi
    d="$(dirname "$d")"
  done
}
_cli_h="$(_cli_host || true)"
if [ -n "$_cli_h" ] && [ -f "$_cli_h/lib/cli.sh" ] && _cli_mine "$_cli_h/lib/cli.sh"; then
  # shellcheck source=/dev/null
  . "$_cli_h/lib/cli.sh"
else
  cli_fail() { printf '%s: error %s: %s\n  hint: %s\n  fix: %s\n' "$CLI_TOOL" "$1" "$2" "$3" "$4" >&2; exit "${5:-1}"; }
  cli_usage() { cli_fail "input.invalid" "$1" "the usage is in $CLI_TOOL --help" "$CLI_TOOL --help" 2; }
  cli_summary() { awk -F'"' -v n="$1" '/^name *=/ { name = $2 } /^summary *=/ && name == n { print $2; exit }' "$2" 2>/dev/null || true; }
fi

# cli_header FILE LAST: --help: `<tool>: <summary>` (this root's tools.toml), then lines 3 to LAST of the header.
cli_header() {
  printf '%s: %s\n' "$CLI_TOOL" "$(cli_summary "${CLI_TOOL#odapkit }" "$cli_root/tools.toml")"
  sed -n "3,${2}p" "$1" | sed 's/^# \{0,1\}//'
}
