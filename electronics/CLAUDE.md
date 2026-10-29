# Toolkit electronics

Electronics tooling for OpenDAP: the craft checks (`odw craft`), the layer stack (`odw stackup`), board variants (`odw variant`).

## Working here

- CRAFT rules (levels, limits, the text of the standard's rule tables) are edited only in `craft/rules.toml`: the checks read it and `odw craft rules render` writes the standard's tables from it. Copy values from the standard and its sources; never add a limit the rule's row does not state.
- Each engine keeps its tests beside it (`craft/tests`, `pcb/craft/tests`, `schematic/craft/tests`, `pcb/stackup/tests`, `variant/tests`), run with `python3 -m unittest discover tests` from the engine's folder; `tests/` holds the folder's command-line contract.
- The engines load the shared helpers through core's `clihost.py` and read KiCad files through core's `kicad/sexpr.py`; `odw stackup` runs in the hardware tools' uv project until the one Python workspace.
