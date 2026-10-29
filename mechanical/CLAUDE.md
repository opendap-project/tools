# Toolkit mechanical

FreeCAD exports and the CAD adapters of `odw render`.

## Working here

- `render/cad.py` is imported by core's `render/core.py` (its adapter registry); keep its interface (`Unavailable`, the adapter functions) in step with it and with `odw render`'s tests (`core/render/tests`).
