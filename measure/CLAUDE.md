# Toolkit measure

Measurement tooling for OpenDAP: motion recorded on screen and measured (`odw motion`).

## Working here

- The engine is `motion/motioncli.py`; `bin/odw-motion` only runs it. Its tests run with `python3 -m unittest discover tests` and `tests/cli.sh`, both from this folder.
- Requirements are edited in the tools root's `docs/requirements/requirements.toml` (HL-71); its README is rendered with `odw req render` and never edited by hand.
