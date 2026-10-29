# Toolkit host

What is left of the former toolkit-host: the `odapkit` alias. The host setup (`setup.sh`, the `Brewfile`) and the development workflow are in [`env/`](../env/README.md), and `odw` itself in [`workspace/`](../workspace/README.md) (HL-71).

## Layout

- `bin/odapkit` — `odw` under its earlier name (RFC 0009): an alias that runs `odw`, so every `odapkit <tool>` keeps working until the `tools` cut. `env/setup.sh` links it into `~/.local/bin`

## License

This folder follows the [REUSE](https://reuse.software) layout: license texts live in the root's `LICENSES/` and `REUSE.toml` maps each path to its license.

- Default: **GNU GPL v3.0 or later** (`GPL-3.0-or-later`)
- Markdown files and `docs/`: **Creative Commons Attribution-ShareAlike 4.0** (`CC-BY-SA-4.0`)

Copyright © 2026 The OpenDAP Project.
