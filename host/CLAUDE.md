# Toolkit host

The `odapkit` alias, until the `tools` cut retires it; the host setup is in `env/`, `odw` in `workspace/` (HL-71).

## Working here

- `bin/odapkit` runs the `odw` its checkout built (`workspace/target/release/odw`), else the one on `PATH`; it sets `ODAP_TOOLS_DIR` to its tools root.
