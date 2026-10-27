#!/bin/sh
# Run KiCad ERC or DRC on the projects that contain the given files.
# Usage: kicad-check.sh erc|drc FILE...
# The former entry point of the kicad-erc and kicad-drc hooks, kept for the tools that call it
# (toolkit-hardware's fab-package.sh): the engine is kicad-check.py, next to it.
exec python3 "$(dirname "$0")/kicad-check.py" "$@"
