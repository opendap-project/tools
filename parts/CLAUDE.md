# Toolkit parts

The sourcing matrix (`odw parts`) and the part file sync (`parts-sync.py`).

## Working here

- Sourcing data (stock, prices, quotes) is never committed to a public repository: `odw parts matrix` writes only to the `--out` folder the caller names, which belongs in a private location.
- Standard library only; the engine loads `odw.clikit` through `core/clihost.py`.
- Tests: `python3 -m unittest discover -s sourcing/tests` and `python3 -m unittest discover -s tests`, from this folder.
