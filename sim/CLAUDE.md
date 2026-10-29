# Toolkit sim

The study runner (`odw sim`).

## Working here

- The engine runs on the system `python3` with the standard library only; it loads `odw.clikit` through `core/clihost.py` (core/ appended to its path).
- Tests: `python3 -m unittest discover tests` from this folder; each test module removes the `GIT_*` variables itself (unittest never imports the root's conftest).
