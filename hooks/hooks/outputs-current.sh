#!/bin/sh
# outputs-current (pre-commit, RFC 0021 §6): every component's committed visual outputs are current, by their
# source-hash stamps (outputs/stamp.json); reads and hashes files only, never runs KiCad, FreeCAD or Blender.
# The engine is odw render's (hardware/tools/render in this checkout), so a repository needs no odw on PATH.
here="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/../.." && pwd)"
exec python3 "$here/hardware/tools/render/__main__.py" check .
