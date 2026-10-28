# Toolkit image

Tools to build Blackbird OS images and the software that goes into them, for the platform's maintainers.

## Working here

- Requirements are edited in `docs/requirements/requirements.toml`; `docs/requirements/README.md` is rendered from it with `odapkit req render` and never edited by hand.
- One tool per task: an `odapkit-<tool>` executable in `bin/`, run as `odapkit <tool>` (RFC 0009). Find other repositories through `lib/odapkit.sh` (`repo_dir`), never by relative paths; pass their content to builds as inputs, never write them into another repository's definition.
