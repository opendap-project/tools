# tools

One folder per former repository (`hardware/`, `hooks/`, `host/`, `image/` and `verify/`), whose content moves into the domain folders one domain at a time: `core/`, `documentation/`, `measure/` and `schemas/` so far.

<!-- >>> odapkit sync: managed block from the workspace templates; edit them, not this block -->
Part of [OpenDAP](https://github.com/opendap-project). This repository is Open Source.

Contributor rules (English, confirmed information only, REUSE licensing, the `docs/` layout and its skills): the [contributor guide](https://github.com/opendap-project/.github/blob/main/CONTRIBUTING.md).
<!-- <<< odapkit sync -->

## Working here

- Each folder has its own `CLAUDE.md` with its rules; read the one for the folder you change.
- Tests never inherit a hook's `GIT_*` variables: each language's protection is in the README's [rule](README.md#tests-never-inherit-a-hooks-git-variables), and `odw audit` flags a test file that runs git without it.
- Requirements are edited in each folder's `docs/requirements/requirements.toml`; its README is rendered with `odw req render` and never edited by hand.
