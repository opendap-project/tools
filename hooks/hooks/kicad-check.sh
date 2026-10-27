#!/bin/sh
# Run KiCad ERC or DRC on the projects that contain the given files.
# Usage: kicad-check.sh erc|drc FILE...
set -eu
mode="$1"; shift

kicad_cli="${KICAD_CLI:-}"
if [ -z "$kicad_cli" ]; then
  for c in /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli "$(command -v kicad-cli || true)"; do
    if [ -n "$c" ] && [ -x "$c" ]; then kicad_cli="$c"; break; fi
  done
fi
if [ -z "$kicad_cli" ]; then
  echo "kicad-cli not found. Install KiCad 10 or set KICAD_CLI." >&2
  exit 1
fi

status=0
for f in "$@"; do
  dir="$(dirname "$f")"
  base="$(basename "$f")"; base="${base%.*}"
  # Only files that belong to a KiCad project (same-named .kicad_pro next to them).
  [ -f "$dir/$base.kicad_pro" ] || continue
  report="$(mktemp)"
  if [ "$mode" = erc ]; then
    "$kicad_cli" sch erc --severity-error --exit-code-violations -o "$report" "$f" >/dev/null 2>&1 || rc=$?
  else
    "$kicad_cli" pcb drc --schematic-parity --severity-error --exit-code-violations -o "$report" "$f" >/dev/null 2>&1 || rc=$?
  fi
  if [ "${rc:-0}" -ne 0 ]; then
    echo "KiCad $(echo "$mode" | tr a-z A-Z) failed: $f" >&2
    sed -n '1,60p' "$report" >&2
    status=1
  fi
  rc=0
  rm -f "$report"
done
exit $status
