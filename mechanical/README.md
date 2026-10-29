# Toolkit mechanical

Mechanical tooling for OpenDAP: exports from FreeCAD, and the CAD adapters of `odw render`.

## Layout

- `freecad_export.py` — exports a FreeCAD document as one STEP solid (the component as a single body) and prints its mass properties; run with FreeCAD's headless interpreter: `freecadcmd mechanical/freecad_export.py -- INPUT.FCStd OUTPUT.step`
- `render/cad.py` — `odw render`'s FreeCAD and Blender adapters (the dispatcher is core's `render/`)

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
