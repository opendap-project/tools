# Documentation

| Folder | Contents |
|---|---|
| [`requirements/`](requirements/) | What this project must achieve. Each requirement has an ID, a priority (**must** / **should**) and a status. Their source is [`requirements.toml`](requirements/requirements.toml); the README is rendered from it with `odw req render`. |
| [`specs/`](specs/) | Specifications every OpenDAP device shares: the [component export contract](specs/component-export.md), [simulation checks](../../sim/docs/specs/study-format.md), the [stackup method](specs/stackup-method.md), the [sourcing matrix tool](specs/sourcing-matrix.md) and the [variant tool](specs/variant-tool.md) with the variant file format. Each device applies them in its own repositories, the T1 first. |

Architecture Decision Records go in `adr/` once decisions are taken.

Only information confirmed by the maintainers is recorded here. Earlier experiments are not a source.

**Requirement status:** *Confirmed* (agreed, build on it) · *Draft* (agreed intent, wording or detail still open).
