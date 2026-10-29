# Toolkit image

Tools to build Blackbird OS images and the software that goes into them, for the platform's maintainers.

## Working here

- Requirements are edited in the tools root's `docs/requirements/requirements.toml` (HL-71); its README is rendered with `odw req render` and never edited by hand.
- One tool per task: an `odapkit-<tool>` executable in `bin/`, run as `odapkit <tool>` (RFC 0009). Find other repositories through `lib/odapkit.sh` (`repo_dir`), never by relative paths; pass their content to builds as inputs, never write them into another repository's definition.
