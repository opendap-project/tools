#!/bin/sh
# outputs-regenerate (pre-push, RFC 0021 §6): regenerates the components that hold the pushed files and fails
# when their outputs/ then differ from the commit, naming the files to commit. Needs the programs of the
# declared sources (KiCad 10 for KiCad sources).
here="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/../.." && pwd)"
[ $# -eq 0 ] && exit 0
exec python3 "$here/hardware/tools/render/__main__.py" check --regenerate "$@"
