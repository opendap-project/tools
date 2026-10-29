# Toolkit verify

Verification tooling for OpenDAP: simulations and samples captured from sessions, on the virtual T1 now and on prototypes later. Dedicated lab instruments are not assumed (TK-012).

## Working here

- Requirements are edited in the tools root's `docs/requirements/requirements.toml` (HL-71); its README is rendered with `odw req render` and never edited by hand.
- The toolkit is one repository per tool domain under `public/toolkit/` (`host`, `image`, `hardware`, `verify`, `hooks`), each tool run as `odapkit <tool>`; the `opendap` CLI is user-side and lives in `cli`, outside the toolkit. Use another toolkit repository through its documented interface, not its internals.
