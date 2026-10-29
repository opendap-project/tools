# Toolkit measure

Measurement tooling for OpenDAP: motion recorded on screen and measured (`odw motion`).

## Working here

- The engine is `motion/motioncli.py`; `bin/odw-motion` only runs it. Its tests run with `python3 -m unittest discover tests` and `tests/cli.sh`, both from this folder.
- Requirements for this folder are still kept in `verify/docs/requirements/requirements.toml` until the documentation domain gathers them (HL-71).
