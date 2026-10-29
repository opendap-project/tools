# tools

| Folder | Content |
| --- | --- |
| [`hardware/`](hardware/README.md) | The hardware tools' Python project, and the [component export contract](hardware/docs/specs/component-export.md) every component follows |
| [`hooks/`](hooks/README.md) | Shared [pre-commit](https://pre-commit.com) hooks for every OpenDAP repository |
| [`host/`](host/README.md) | Developer host setup for OpenDAP: the tools, toolchains and checks every contributor installs |
| [`image/`](image/README.md) | Tools to build Blackbird OS images and the software that goes into them, for the platform's maintainers |
| [`core/`](core/README.md) | What every domain shares: command-line conventions and their conformance test, the evaluation-matrix engine, the `odw` package's shared modules |
| [`documentation/`](documentation/README.md) | The documentation engines: requirements as data, docs gates and index, RFC status, verification matrices |
| [`schemas/`](schemas/README.md) | The workspace formats as JSON Schemas, with their fixtures and tests |
| [`parts/`](parts/README.md) | Part data: the sourcing evaluation matrix (`odw parts`) and the part file sync |
| [`sim/`](sim/README.md) | The study runner: a board's simulation regression checks (`odw sim`) |
| [`electronics/`](electronics/README.md) | Electronics tooling: PCB and schematic craft checks, the layer stack and impedance solver, board variants |
| [`env/`](env/README.md) | The maintainers' host environment: QEMU for virtual devices, a LAN address for them, the build VM, the indexer |
| [`workspace/`](workspace/README.md) | `odw` itself and the workspace tools: plan, audit, shared files, the restructure's cut engine |
| [`mechanical/`](mechanical/README.md) | Mechanical tooling: the FreeCAD export and `odw render`'s CAD adapters |
| [`measure/`](measure/README.md) | Measurement tooling for OpenDAP: motion recorded on screen and measured (`odw motion`) |
| [`verify/`](verify/README.md) | Verification tooling for OpenDAP: simulations and samples captured from sessions, on the virtual T1 now and on prototypes later |

Each folder keeps its own `README.md`, `CLAUDE.md` and `REUSE.toml`; the licence texts are in `LICENSES/`. `hardware/`, `hooks/`, `host/`, `image/` and `verify/` came from the former `toolkit-hardware`, `toolkit-hooks`, `toolkit-host`, `toolkit-image` and `toolkit-verify` repositories with their history ([RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md)); their content moves into the domain folders one domain at a time (`core/`, `documentation/`, `electronics/`, `env/`, `measure/`, `mechanical/`, `parts/`, `schemas/`, `sim/` and `workspace/` so far).

## Tests never inherit a hook's git variables

Under a pre-commit hook, git exports `GIT_DIR`, `GIT_INDEX_FILE` and others. A test that runs git (a fixture repository, `git init`, a tool that calls git) with them acts on the repository being committed: it has set `core.bare` and written a placeholder identity there. Every test file that starts a process removes every `GIT_*` variable first, one way per language:

| Tests | Protection |
| --- | --- |
| Python under pytest (every suite the root's `pyproject.toml` runs, `core/tests`, `core/component/tests`, `documentation/tests`, `env/tests`, `schemas/tests` and `workspace/tests`) | the autouse fixture in the root's `conftest.py`, which applies to every test below it |
| Python under unittest (`electronics/`, `hardware/`, `hooks/`, `image/`, `measure/`, `parts/`, `sim/`) | at the top of each test module, after its imports: `for _k in [k for k in os.environ if k.startswith("GIT_")]: del os.environ[_k]` (in the module, because `python3 -m unittest discover tests` never imports `tests/__init__.py`) |
| Rust (`workspace/crates/odw-dispatch/tests/odw.rs`) | the `command()` helper, which starts every child from `env_clear()`; a helper that only drops git's variables calls `env_remove` for each `GIT_*` one |
| Shell (`image/tests/cli.sh`, `measure/tests/cli.sh`) | after `set -u`: `for v in $(env \| sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done` |

The other direction holds too: a tool that commits (`odw rename`) runs git in the environment a plain `git commit` has (`audit.hook_env`), never its own runtime, so the hooks' tests do not run with its virtual environment first on `PATH` (its `python3`, with `odw` installed) or the folder variables the dispatcher exports (HL-109).

`odw audit` lists, per repository, the test files that run git without one of these (`tests_running_git_unprotected`). A `conftest.py` beside a `pyproject.toml` counts only for that project's pytest `testpaths`, so a unittest module elsewhere still needs its own stanza. `core/tests/git-leak.sh DIR COMMAND…` runs a suite with `GIT_DIR` and `GIT_INDEX_FILE` pointing at a scratch repository and fails if the suite changed it.

## License

This repository follows the [REUSE](https://reuse.software) layout: licence texts in `LICENSES/`, and each folder's `REUSE.toml` maps its paths. Copyright © 2026 The OpenDAP Project.
