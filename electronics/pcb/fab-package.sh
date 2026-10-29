#!/bin/sh
# Build a fab + assembly package for one KiCad project, locally.
# Usage: fab-package.sh PROJECT.kicad_pro [--out DIR] [--skip-checks] [--json]
#        fab-package.sh PROJECT.kicad_pro --check
# Output (default): <project dir>/fab/<name>-<git describe>/  (fab/ is never committed)
# --check runs only the checks the package needs (the project's files, ERC and DRC) and writes
# nothing. --json prints opendap.fab-package/v1 (name, revision, out, files) on stdout and the
# progress on stderr. toolkit-hooks is $ODAP_TOOLS_DIR/hooks, else the sibling ../hooks.
# Example: fab-package.sh hardware/board.kicad_pro --check
# Exit status: 0 package written (or checks pass), 1 a check or an export failed, 2 usage error.
set -eu
usage() {
  printf 'fab-package.sh: error input.invalid: %s\n  hint: the usage is in fab-package.sh --help\n  fix: fab-package.sh --help\n' "$1" >&2
  exit 2
}
pro=""; out=""; skip=0; check=0; json=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '2,13s/^# \{0,1\}//p' "$0"; exit 0;;
    --out) [ $# -ge 2 ] || usage "--out needs a DIR"; out="$2"; shift 2;;
    --skip-checks) skip=1; shift;;
    --check) check=1; shift;;
    --json) json=1; shift;;
    -*) usage "unknown flag $1";;
    *) pro="$1"; shift;;
  esac
done
[ -f "$pro" ] || usage "no project file ${pro:-given} (PROJECT.kicad_pro)"
here="$(cd "$(dirname "$0")/../.." && pwd)"  # electronics/ (HL-71)
# toolkit-hooks: $ODAP_TOOLS_DIR/hooks (HL-71), else ODAP_TOOLKIT_HOOKS_DIR (deprecated), else the sibling ../hooks
hooks="${ODAP_TOOLS_DIR:+$ODAP_TOOLS_DIR/hooks}"
hooks="${hooks:-${ODAP_TOOLKIT_HOOKS_DIR:-$here/../hooks}}"
[ -x "$hooks/hooks/kicad-check.sh" ] || {
  printf 'fab-package.sh: error repo.not_found: toolkit-hooks not found at %s\n  hint: ERC and DRC run through its hooks/kicad-check.sh\n  fix: export ODAP_TOOLS_DIR=<tools checkout>\n' "$hooks" >&2
  exit 2
}
# With --json, stdout carries only the document: the progress goes to stderr.
if [ "$json" = 1 ]; then exec 3>&1 1>&2; fi
dir="$(cd "$(dirname "$pro")" && pwd)"; name="$(basename "$pro" .kicad_pro)"
pcb="$dir/$name.kicad_pcb"; sch="$dir/$name.kicad_sch"
kicad_cli="${KICAD_CLI:-/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli}"
kicad_py="${KICAD_PYTHON:-/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3}"
[ -x "$kicad_cli" ] || kicad_cli="$(command -v kicad-cli)"
rev="$(git -C "$dir" describe --tags --always --dirty 2>/dev/null || echo untracked)"

if [ $check -eq 1 ]; then
  [ -f "$pcb" ] && [ -f "$sch" ] || { echo "fab-package.sh: $name needs $name.kicad_pcb and $name.kicad_sch" >&2; exit 1; }
  echo "== ERC / DRC"
  "$hooks/hooks/kicad-check.sh" erc "$sch"
  "$hooks/hooks/kicad-check.sh" drc "$pcb"
  echo "== Checks pass: $name ($rev); nothing written"
  exit 0
fi
if [ $skip -eq 0 ]; then
  echo "== ERC / DRC"
  "$hooks/hooks/kicad-check.sh" erc "$sch"
  "$hooks/hooks/kicad-check.sh" drc "$pcb"
else
  rev="$rev-UNCHECKED"
  echo "WARNING: ERC/DRC skipped; the package is marked UNCHECKED." >&2
fi

out="${out:-$dir/fab/$name-$rev}"
rm -rf "$out"; mkdir -p "$out/gerbers"
log="$out/kicad-cli.log"
echo "== Gerbers and drill"
"$kicad_cli" pcb export gerbers --output "$out/gerbers/" "$pcb" >>"$log" 2>&1
"$kicad_cli" pcb export drill --output "$out/gerbers/" --format excellon --excellon-units mm --generate-map --map-format pdf "$pcb" >>"$log" 2>&1
(cd "$out/gerbers" && zip -q -r "../$name-gerbers.zip" .)
echo "== Pick and place"
"$kicad_cli" pcb export pos --format csv --units mm --side both --exclude-dnp --output "$out/$name-pos.csv" "$pcb" >>"$log" 2>&1
echo "== BOM"
"$kicad_cli" sch export bom --exclude-dnp \
  --fields 'Reference,${QUANTITY},Value,Footprint,Manufacturer,MPN,Distributor PN' \
  --labels 'Designator,Quantity,Value,Package,Manufacturer,Manufacturer Part Number,Distributor Part Number' \
  --group-by 'Value,Footprint,MPN' --output "$out/$name-bom.csv" "$sch" >>"$log" 2>&1
if ! awk -F'","' 'NR>1 && ($6=="" || $5=="") {bad=1} END {exit bad}' "$out/$name-bom.csv"; then
  echo "WARNING: some BOM lines have no Manufacturer or MPN; turn-key assembly needs both." >&2
fi
echo "== Documents and 3D"
"$kicad_cli" sch export pdf --output "$out/$name-schematic.pdf" "$sch" >>"$log" 2>&1
"$kicad_cli" pcb export pdf --layers F.Fab,Edge.Cuts --mode-single --output "$out/$name-assembly-top.pdf" "$pcb" >>"$log" 2>&1
"$kicad_cli" pcb export pdf --layers B.Fab,Edge.Cuts --mode-single --mirror --output "$out/$name-assembly-bottom.pdf" "$pcb" >>"$log" 2>&1
"$kicad_cli" pcb export step --output "$out/$name.step" "$pcb" >>"$log" 2>&1 || echo "WARNING: STEP export failed (missing 3D models?)" >&2
"$kicad_cli" pcb export ipc2581 --output "$out/$name.xml" "$pcb" >>"$log" 2>&1 || echo "WARNING: IPC-2581 export failed" >&2
echo "== Interactive HTML BOM"
"$kicad_py" -m InteractiveHtmlBom.generate_interactive_bom --no-browser --dest-dir "$out" --name-format "%f-ibom" "$pcb" >>"$log" 2>&1 \
  || echo "WARNING: Interactive HTML BOM failed (install it with toolkit host/setup.sh)" >&2
{
  echo "Fab package: $name"
  echo "Source revision: $rev"
  echo "KiCad: $("$kicad_cli" version)"
  echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  (cd "$out" && find . -type f ! -name MANIFEST.txt ! -name kicad-cli.log | sort)
} > "$out/MANIFEST.txt"
echo "== Package ready: $out"
if [ "$json" = 1 ]; then
  # Each line a JSON string: backslash and quote escaped, control characters dropped.
  jstr() { tr -d '\000-\010\013-\037' | awk 'BEGIN { ORS = "" } { gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); gsub(/\t/, "\\t"); printf "%s\"%s\"", (NR > 1 ? ", " : ""), $0 }'; }
  files="$(cd "$out" && find . -type f ! -name kicad-cli.log | sort | sed 's|^\./||' | jstr)"
  printf '{"schema": "opendap.fab-package/v1", "name": %s, "revision": %s, "out": %s, "files": [%s]}\n' \
    "$(printf '%s\n' "$name" | jstr)" "$(printf '%s\n' "$rev" | jstr)" "$(printf '%s\n' "$out" | jstr)" "$files" >&3
fi
