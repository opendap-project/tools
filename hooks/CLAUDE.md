# Toolkit hooks

Shared pre-commit hooks for every OpenDAP repository.

## Working here

- Every OpenDAP repository pins these hooks by tag. Changing a hook means a new tag and updating the pins; this repository runs its own hooks with `repo: local`.
- The toolkit is one repository per tool domain under `public/toolkit/` (`host`, `image`, `hardware`, `verify`, `hooks`), each tool run as `odapkit <tool>`; the `opendap` CLI is user-side and lives in `cli`, outside the toolkit.
