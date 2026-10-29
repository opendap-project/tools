# tools

| Folder | Content |
| --- | --- |
| [`hardware/`](hardware/README.md) | Hardware tooling for OpenDAP: fab packages, FreeCAD exports, part data sync, and the [component export contract](hardware/docs/specs/component-export.md) every component follows |
| [`hooks/`](hooks/README.md) | Shared [pre-commit](https://pre-commit.com) hooks for every OpenDAP repository |
| [`host/`](host/README.md) | Developer host setup for OpenDAP: the tools, toolchains and checks every contributor installs |
| [`image/`](image/README.md) | Tools to build Blackbird OS images and the software that goes into them, for the platform's maintainers |
| [`measure/`](measure/README.md) | Measurement tooling for OpenDAP: motion recorded on screen and measured (`odw motion`) |
| [`verify/`](verify/README.md) | Verification tooling for OpenDAP: simulations and samples captured from sessions, on the virtual T1 now and on prototypes later |

Each folder keeps its own `README.md`, `CLAUDE.md` and `REUSE.toml`; the licence texts are in `LICENSES/`. The first five folders came from the former `toolkit-hardware`, `toolkit-hooks`, `toolkit-host`, `toolkit-image` and `toolkit-verify` repositories with their history ([RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md)); their content moves into the domain folders one domain at a time (`measure/` first).

## Tests never inherit a hook's git variables

Under a pre-commit hook, git exports `GIT_DIR`, `GIT_INDEX_FILE` and others. A test that runs git (a fixture repository, `git init`, a tool that calls git) with them acts on the repository being committed: it has set `core.bare` and written a placeholder identity there. Every test file that starts a process removes every `GIT_*` variable first, one way per language:

| Tests | Protection |
| --- | --- |
| Python under pytest (every suite the root's `pyproject.toml` runs, `host/tests` today) | the autouse fixture in the root's `conftest.py`, which applies to every test below it |
| Python under unittest (`hardware/`, `hooks/`, `image/`, `measure/`) | at the top of each test module, after its imports: `for _k in [k for k in os.environ if k.startswith("GIT_")]: del os.environ[_k]` (in the module, because `python3 -m unittest discover tests` never imports `tests/__init__.py`) |
| Rust (`host/crates/odw-dispatch/tests/odw.rs`) | the `command()` helper, which starts every child from `env_clear()`; a helper that only drops git's variables calls `env_remove` for each `GIT_*` one |
| Shell (`image/tests/cli.sh`, `measure/tests/cli.sh`) | after `set -u`: `for v in $(env \| sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done` |

The other direction holds too: a tool that commits (`odw rename`) runs git in the environment a plain `git commit` has (`audit.hook_env`), never its own runtime, so the hooks' tests do not run with its virtual environment first on `PATH` (its `python3`, with `odw` installed) or the folder variables the dispatcher exports (HL-109).

`odw audit` lists, per repository, the test files that run git without one of these (`tests_running_git_unprotected`). A `conftest.py` beside a `pyproject.toml` counts only for that project's pytest `testpaths`, so a unittest module elsewhere still needs its own stanza. `host/tests/git-leak.sh DIR COMMAND…` runs a suite with `GIT_DIR` and `GIT_INDEX_FILE` pointing at a scratch repository and fails if the suite changed it.

## License

This repository follows the [REUSE](https://reuse.software) layout: licence texts in `LICENSES/`, and each folder's `REUSE.toml` maps its paths. Copyright © 2026 The OpenDAP Project.
