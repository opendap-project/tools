# Toolkit image

Tools to build Blackbird OS images and the software that goes into them, for the platform's maintainers.

<!-- >>> odapkit sync: managed block from the workspace templates; edit them, not this block -->
Part of [OpenDAP](https://github.com/opendap-project). This repository is Open Source.

Contributor rules (English, confirmed information only, REUSE licensing, the `docs/` layout and its skills): the [contributor guide](https://github.com/opendap-project/.github/blob/main/CONTRIBUTING.md).
<!-- <<< odapkit sync -->

## Working here

- Requirements are edited in `docs/requirements/requirements.toml`; `docs/requirements/README.md` is rendered from it with `odapkit req render` and never edited by hand.
- One tool per task: an `odapkit-<tool>` executable in `bin/`, run as `odapkit <tool>` (RFC 0009). Find other repositories through `lib/odapkit.sh` (`repo_dir`), never by relative paths; pass their content to builds as inputs, never write them into another repository's definition.
