# shellcheck shell=sh
# Shared by the toolkit-image tools (sourced, POSIX sh; odapkit, toolkit-host). Sourcing it gives a
# tool the toolkit's conventions (rfcs 0009):
#   --help, -h      print the tool's header comment (its first line is the summary) and exit 0
# and how a tool finds what it works on:
#   repo_dir NAME   the checkout of GitHub repository NAME: $ODAP_<NAME>_DIR, else the workspace's
#                   repos.tsv (the folder above this checkout that lists every repository)
#   odap_data       host-wide data of the toolkit (~/.local/share/opendap): the development key
#   odap_cache      host-wide caches (~/.cache/opendap): the sysroot volume
#   die MSG [CODE]  print to stderr and exit (2: usage or data error, 1: failure)

# The tool's name as people type it: `odapkit image` for bin/odapkit-image.
tool_name() { n="${0##*/}"; echo "odapkit ${n#odapkit-}"; }

die() { echo "$(tool_name): $1" >&2; exit "${2:-1}"; }

case "${1:-}" in
  -h|--help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
esac

odap_data() { echo "${XDG_DATA_HOME:-$HOME/.local/share}/opendap"; }
odap_cache() { echo "${XDG_CACHE_HOME:-$HOME/.cache}/opendap"; }

workspace_root() {
  d="$(cd "$(dirname "$0")/.." && pwd)"
  while [ "$d" != / ]; do
    [ -f "$d/repos.tsv" ] && { echo "$d"; return 0; }
    d="$(dirname "$d")"
  done
  return 1
}

repo_dir() {
  var="ODAP_$(echo "$1" | tr 'a-z-' 'A-Z_')_DIR"
  eval "v=\${$var:-}"
  if [ -n "$v" ]; then
    [ -d "$v" ] || die "$var=$v is not a directory" 2
    echo "$v"; return 0
  fi
  ws="$(workspace_root)" || die "no workspace (repos.tsv) above this checkout: set $var" 2
  path="$(awk -F'\t' -v n="$1" 'NR > 1 { split($2, a, "/"); if (a[2] == n) print $1 }' "$ws/repos.tsv")"
  [ -n "$path" ] && [ -d "$ws/$path" ] || die "no $1 checkout in $ws: run scripts/bootstrap.sh, or set $var" 2
  echo "$ws/$path"
}
