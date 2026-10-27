# Toolkit hardware

Hardware tooling for OpenDAP: fab packages, FreeCAD exports, part data sync, and the [component export contract](docs/specs/component-export.md) every component follows.

<!-- >>> odapkit sync: managed block from the workspace templates; edit them, not this block -->
Part of [OpenDAP](https://github.com/opendap-project). This repository is Open Source.

Contributor rules (English, confirmed information only, REUSE licensing, the `docs/` layout and its skills): the [contributor guide](https://github.com/opendap-project/.github/blob/main/CONTRIBUTING.md).
<!-- <<< odapkit sync -->

## Working here

- Requirements are edited in `docs/requirements/requirements.toml`; `docs/requirements/README.md` is rendered from it with `odapkit req render` and never edited by hand.
- CRAFT rules (levels, limits, the text of the standard's rule tables) are edited only in `tools/craft/rules.toml`: the checks read it and `odapkit craft rules render` writes the standard's tables from it. Copy values from the standard and its sources; never add a limit the rule's row does not state.
- The toolkit is one repository per tool domain under `public/toolkit/` (`host`, `image`, `hardware`, `verify`, `hooks`), each tool run as `odapkit <tool>`; the `opendap` CLI is user-side and lives in `cli`, outside the toolkit. Use another toolkit repository through its documented interface, not its internals.
