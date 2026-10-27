#!/bin/sh
# Build a fab + assembly package for one KiCad project, locally.
# Usage: fab-package.sh PROJECT.kicad_pro [--out DIR] [--skip-checks]
# Output (default): <project dir>/fab/<name>-<git describe>/  (fab/ is never committed)
set -eu
pro=""; out=""; skip=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out) out="$2"; shift 2;;
    --skip-checks) skip=1; shift;;
    *) pro="$1"; shift;;
  esac
done
[ -f "$pro" ] || { echo "usage: fab-package.sh PROJECT.kicad_pro [--out DIR] [--skip-checks]" >&2; exit 2; }
here="$(cd "$(dirname "$0")/.." && pwd)"
hooks="${OPENDAP_TOOLKIT_HOOKS:-$here/../hooks}"   # toolkit-hooks checkout (sibling in the workspace)
[ -x "$hooks/hooks/kicad-check.sh" ] || { echo "toolkit-hooks not found: set OPENDAP_TOOLKIT_HOOKS" >&2; exit 2; }
dir="$(cd "$(dirname "$pro")" && pwd)"; name="$(basename "$pro" .kicad_pro)"
pcb="$dir/$name.kicad_pcb"; sch="$dir/$name.kicad_sch"
kicad_cli="${KICAD_CLI:-/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli}"
kicad_py="${KICAD_PYTHON:-/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3}"
[ -x "$kicad_cli" ] || kicad_cli="$(command -v kicad-cli)"
rev="$(git -C "$dir" describe --tags --always --dirty 2>/dev/null || echo untracked)"

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
