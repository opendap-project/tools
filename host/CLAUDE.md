# Toolkit host setup

Developer host setup for OpenDAP: the tools, toolchains and checks every contributor installs. Everything runs locally; there is no CI.

## Working here

- Requirements are edited in the tools root's `docs/requirements/requirements.toml` (HL-71); its README is rendered with `odw req render` and never edited by hand.
- The toolkit is one repository per tool domain under `public/toolkit/` (`host`, `image`, `hardware`, `verify`, `hooks`); the `opendap` CLI is user-side and lives in `cli`, outside the toolkit. Use another toolkit repository through its documented interface, not its internals.
