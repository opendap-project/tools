# Toolkit hardware

Hardware tooling that has not moved to its domain folder yet (`odw render`, the FreeCAD export, the hardware tools' uv project), and the component export contract.

## Working here

- Requirements are edited in `docs/requirements/requirements.toml`; `docs/requirements/README.md` is rendered from it with `odw req render` and never edited by hand.
- The craft checks, `odw stackup` and `odw variant` are in `electronics/`, `odw sim` in `sim/`, `odw parts` in `parts/`, `odw component` in `core/` (HL-71).
