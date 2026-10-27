# Documentation

| Folder | Contents |
|---|---|
| [`requirements/`](requirements/) | What this project must achieve. Each requirement has an ID, a priority (**must** / **should**) and a status. Their source is [`requirements.toml`](requirements/requirements.toml); the README is rendered from it with `odapkit req render`. |
| [`specs/`](specs/) | Specifications every OpenDAP device shares: the [component export contract](specs/component-export.md), [simulation checks](specs/simulation-checks.md), the [stackup method](specs/stackup-method.md) and the [sourcing matrix tool](specs/sourcing-matrix.md). Each device applies them in its own repositories, the T1 first. |

Architecture Decision Records go in `adr/` once decisions are taken.

Only information confirmed by the maintainers is recorded here. Earlier experiments are not a source.

**Requirement status:** *Confirmed* (agreed, build on it) · *Draft* (agreed intent, wording or detail still open).
