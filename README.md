# tools

| Folder | Content |
| --- | --- |
| [`hardware/`](hardware/README.md) | Hardware tooling for OpenDAP: fab packages, FreeCAD exports, part data sync, and the [component export contract](hardware/docs/specs/component-export.md) every component follows |
| [`hooks/`](hooks/README.md) | Shared [pre-commit](https://pre-commit.com) hooks for every OpenDAP repository |
| [`host/`](host/README.md) | Developer host setup for OpenDAP: the tools, toolchains and checks every contributor installs |
| [`image/`](image/README.md) | Tools to build Blackbird OS images and the software that goes into them, for the platform's maintainers |
| [`verify/`](verify/README.md) | Verification tooling for OpenDAP: simulations and samples captured from sessions, on the virtual T1 now and on prototypes later |

Each folder keeps its own `README.md`, `CLAUDE.md` and `REUSE.toml`; the licence texts are in `LICENSES/`. The folders came from the former `toolkit-hardware`, `toolkit-hooks`, `toolkit-host`, `toolkit-image` and `toolkit-verify` repositories with their history ([RFC 0017](https://github.com/opendap-project/rfcs/blob/main/text/0017-naming-and-topology.md)).

## License

This repository follows the [REUSE](https://reuse.software) layout: licence texts in `LICENSES/`, and each folder's `REUSE.toml` maps its paths. Copyright © 2026 The OpenDAP Project.
