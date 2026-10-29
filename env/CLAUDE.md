# Toolkit env

The maintainers' host environment: the host setup (`setup.sh`, `Brewfile`), `odw qemu`, `odw vmnet`, `odw noindex`, the build VM's configuration.

## Working here

- `odw qemu`'s patch set name, in its version line, changes whenever `qemu/patches/` changes.
- The build VM is for `mkosi` only; `odw image vm` creates it from `build-vm.yaml` on first use.
- Tests run from the tools root (`uv run pytest`), under the root's `conftest.py`.
- `setup.sh` is documented as `public/tools/env/setup.sh` across the workspace (CONTRIBUTING, READMEs); a move updates every reference in the same window.
