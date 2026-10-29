# shellcheck shell=sh
# Shared by the toolkit-image tools (sourced, POSIX sh; odapkit, toolkit-host). Sourcing it gives a
# tool the toolkit's conventions (rfcs 0009, command standard §6):
#   --help, -h      print `odapkit <tool>: <summary>` (the summary from this root's tools.toml), then
#                   the rest of the tool's header comment, and exit 0
#   cli_fail CODE MSG HINT FIX [STATUS]
#                   an error with a stable dotted code, a hint and a fix (a command to run), on stderr
#                   and, with CLI_JSON=1, as an opendap.error/v1 document on stdout; exits STATUS
#                   (default 1; 2: usage or data error). From toolkit-host's lib/cli.sh, with
#                   cli_usage, cli_json_str and cli_interactive
#   cli_flag ARG    for a tool's argument loop: --json sets CLI_JSON=1, --dry-run sets DRY_RUN=1 (both
#                   return 0); any other flag is a usage error (exit 2); returns 1 for a word
# and how a tool finds what it works on:
#   repo_dir NAME   the checkout of GitHub repository NAME: $ODAP_<NAME>_DIR, else the workspace's
#                   repos.tsv (the folder above this checkout that lists every repository)
#   odap_data       host-wide data of the toolkit (~/.local/share/opendap): the development key
#   odap_cache      host-wide caches (~/.cache/opendap): the sysroot volume
#   die MSG [CODE]  cli_fail with the tool's generic code `<tool>.failed`

# The tool's name as people type it: `odw image` for bin/odw-image.
# The plugin is odw-<tool>; odapkit-<tool>, its old name, is a link to it (HL-72).
tool_name() { n="${0##*/}"; n="${n#odw-}"; echo "odw ${n#odapkit-}"; }
CLI_TOOL="$(tool_name)"
CLI_JSON=0
DRY_RUN=0

workspace_root() {
  d="$(cd "$(dirname "$0")/.." && pwd)"
  while [ "$d" != / ]; do
    [ -f "$d/repos.tsv" ] && { echo "$d"; return 0; }
    d="$(dirname "$d")"
  done
  return 1
}

# True when FILE belongs to the user running the tool (POSIX: no test -O).
_mine() { [ -n "$(find "$1" -prune -user "$(id -u)" 2>/dev/null)" ]; }

# toolkit-host: host/ of this repository when this checkout is one of its folders (the merged tools root: the
# nearest folder with a .git above it), else $ODAP_TOOLKIT_HOST_DIR (odapkit sets it), else the workspace's
# repos.tsv: only a workspace this user owns, and a folder inside it, so a repos.tsv someone else wrote never
# names code to source.
_host_sibling() {
  top="$(cd "$(dirname "$0")/.." && pwd)"
  d="$top"
  while [ ! -e "$d/.git" ]; do
    [ "$d" = / ] && return 1
    d="$(dirname "$d")"
  done
  [ "$d" != "$top" ] && _mine "$d/.git" && [ -f "$d/host/lib/cli.sh" ] && _mine "$d/host/lib/cli.sh" && echo "$d/host"
}
_host_dir() {
  _host_sibling && return 0
  [ -n "${ODAP_TOOLKIT_HOST_DIR:-}" ] && { echo "$ODAP_TOOLKIT_HOST_DIR"; return 0; }
  ws="$(workspace_root)" || return 1
  _mine "$ws/repos.tsv" || return 1
  p="$(awk -F'\t' 'NR > 1 { n = split($2, a, "/"); if (a[n] == "toolkit-host" && $1 !~ /(^\/|\.\.)/) print $1 }' "$ws/repos.tsv")"
  [ -n "$p" ] && echo "$ws/$p"
}
_host="$(_host_dir || true)"
if [ -n "$_host" ] && [ -f "$_host/lib/cli.sh" ] && _mine "$_host/lib/cli.sh"; then
  # shellcheck source=/dev/null
  . "$_host/lib/cli.sh"
else
  # Without toolkit-host, the same contract in its smallest form: text on stderr, no JSON document.
  cli_fail() { printf '%s: error %s: %s\n  hint: %s\n  fix: %s\n' "$CLI_TOOL" "$1" "$2" "$3" "$4" >&2; exit "${5:-1}"; }
  cli_usage() { cli_fail "input.invalid" "$1" "the usage is in $CLI_TOOL --help" "$CLI_TOOL --help" 2; }
  cli_json_str() { printf '"%s"\n' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }
  cli_interactive() { [ -t 0 ] && [ -t 1 ] && [ -z "${CI:-}" ] && [ -z "${ODAP_NONINTERACTIVE:-}" ]; }
fi

die() { cli_fail "${CLI_TOOL#* }.failed" "$1" "the message says what is missing" "$CLI_TOOL --help" "${2:-1}"; }

# CLI_JSON and DRY_RUN are read by the tools that source this file.
# shellcheck disable=SC2034
cli_flag() {
  case "$1" in
    --json) CLI_JSON=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -*) cli_usage "unknown flag $1" ;;
    *) return 1 ;;
  esac
}

case "${1:-}" in
  -h|--help)
    s="$(awk -F'"' -v n="${CLI_TOOL#* }" '/^name *=/ { name = $2 } /^summary *=/ && name == n { print $2; exit }' \
      "$(dirname "$0")/../tools.toml" 2>/dev/null || true)"
    [ -n "$s" ] || s="$(sed -n '2s/^# \{0,1\}//p' "$0")"
    printf '%s: %s\n' "$CLI_TOOL" "$s"
    awk 'NR > 2 && !/^#/ { exit } NR > 2 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
esac

odap_data() { echo "${XDG_DATA_HOME:-$HOME/.local/share}/opendap"; }
odap_cache() { echo "${XDG_CACHE_HOME:-$HOME/.cache}/opendap"; }

# A repository merged into a root (RFC 0017 §7): prints "<root> <folder>", or nothing.
_merged() {
  case "$1" in
    blackbird-os) echo "blackbird os" ;; blackbird-ui) echo "blackbird ui" ;;
    system) echo "blackbird system" ;; virtual-devices) echo "blackbird emulator" ;;
  esac
}

# The workspace path repos.tsv lists for repository $2 in workspace $1, or nothing.
_row() { awk -F'\t' -v n="$2" 'NR > 1 { split($2, a, "/"); if (a[2] == n) print $1 }' "$1/repos.tsv"; }

repo_dir() {
  # Merged into a root (RFC 0017): only its folder, under ODAP_<ROOT>_DIR or the root's row; the old
  # variable and row are not read any more (HL-91).
  m="$(_merged "$1")"; mroot="${m% *}"; mfolder="${m#* }"
  if [ -n "$m" ]; then
    var="ODAP_$(echo "$mroot" | tr 'a-z-' 'A-Z_')_DIR"
    eval "v=\${$var:-}"
    if [ -n "$v" ]; then
      [ -d "$v/$mfolder" ] || cli_fail "repo.not_found" "$var=$v has no $mfolder/ folder" \
        "point $var at a checkout of $mroot" "unset $var" 2
      echo "$v/$mfolder"; return 0
    fi
  else
    var="ODAP_$(echo "$1" | tr 'a-z-' 'A-Z_')_DIR"
    eval "v=\${$var:-}"
    if [ -n "$v" ]; then
      [ -d "$v" ] || cli_fail "repo.not_found" "$var=$v is not a directory" "point $var at a checkout of $1" \
        "unset $var" 2
      echo "$v"; return 0
    fi
  fi
  ws="$(workspace_root)" || cli_fail "workspace.not_found" "no workspace (repos.tsv) above this checkout" \
    "run it from a workspace checkout, or set $var" "export $var=DIR" 2
  if [ -n "$m" ]; then
    path="$(_row "$ws" "$mroot")"; [ -n "$path" ] && path="$path/$mfolder"
  else
    path="$(_row "$ws" "$1")"
  fi
  [ -n "$path" ] && [ -d "$ws/$path" ] || cli_fail "repo.not_found" "no $1 checkout in $ws" \
    "the workspace bootstrap clones every repository you can reach" "$ws/scripts/bootstrap.sh" 2
  echo "$ws/$path"
}
