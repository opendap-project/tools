# Documentation

| Folder | Contents |
|---|---|
| [`requirements/`](requirements/) | What this project must achieve. Each requirement has an ID, a priority (**must** / **should**) and a status. Their source is [`requirements.toml`](requirements/requirements.toml); the README is rendered from it with `odapkit req render`. |
| [`specs/`](specs/) | How it works: [development workflow](specs/development-workflow.md), [workspace schemas](specs/workspace-schemas.md), [programme plan](specs/programme-plan.md), [workspace audit](specs/workspace-audit.md), [requirements as data](specs/requirements-data.md), [docs gates](specs/docs-gates.md), [evaluation matrix](specs/evaluation-matrix.md), [workspace sync](specs/workspace-sync.md), [verification matrices](specs/verification.md), [command standard](specs/command-standard.md), [command-line conformance](specs/cli-conformance.md). |

Architecture Decision Records go in `adr/` once decisions are taken.

Only information confirmed by the maintainers is recorded here. Earlier experiments are not a source.

**Requirement status:** *Confirmed* (agreed, build on it) · *Draft* (agreed intent, wording or detail still open).
