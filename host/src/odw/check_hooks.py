"""odw check hooks: the pre-commit hooks that run `odw check` for the stages each root's check.toml declares.

A repository's check roots are its folders that hold a tracked check.toml (the repository itself, or folders of a
merged root such as blackbird's system/ and emulator/; tests and fixtures are not roots). Each root gets one local
hook per stage it declares: `fast` before each commit, `full` before each push (`stages: [pre-push]`); `release`
has none. The hook passes the changed paths, so odw check runs the steps whose `paths` match (command standard §7).
A root in a folder runs inside it, with the paths given back relative to it. odapkit sync renders the hooks as a
generated block at the end of .pre-commit-config.yaml (generator `odw-check`), and odapkit rename config renders the
same block for a cut root. Spec: docs/specs/workspace-sync.md.
"""
import pathlib
import re
import tomllib

FILE = "check.toml"
# The stages a hook runs, with the git hook each runs in (None: pre-commit, the default).
STAGES = {"fast": None, "full": "pre-push"}
NOT_ROOTS = re.compile(r"(^|/)(tests?|fixtures)/")


def stages(text):
    """The stages of a check.toml that a hook runs, in order; none when it does not parse."""
    try:
        declared = tomllib.loads(text).get("stage", {})
    except tomllib.TOMLDecodeError:
        return []
    return [s for s in STAGES if declared.get(s)]


def roots(top, files):
    """{folder: [stage, …]} for every tracked check.toml under `top` that declares a hooked stage ("" is the top)."""
    out = {}
    for f in sorted(files):
        p = pathlib.PurePosixPath(f)
        if p.name != FILE or NOT_ROOTS.search(f):
            continue
        found = stages((pathlib.Path(top) / f).read_text())
        if found:
            out["" if str(p.parent) == "." else str(p.parent)] = found
    return out


def _entry(folder, stage):
    command = f"odw check --stage {stage}"
    if not folder:
        return command
    up = "../" * len(folder.split("/"))
    # pre-commit passes the repository's paths; inside the folder each is given back relative to it.
    return f"sh -c 'cd {folder} && for f; do set -- \"$@\" \"{up}$f\"; shift; done; exec {command} \"$@\"' --"


def hook(folder, stage):
    """The YAML lines of one hook (six spaces in, as a `- repo: local` block's)."""
    suffix = f"-{folder.replace('/', '-')}" if folder else ""
    name = f"{folder + ' ' if folder else ''}odw check --stage {stage}" + (" (before push)" if STAGES[stage] else "")
    lines = [f"      - id: odw-check-{stage}{suffix}", f"        name: {name}", f"        entry: {_entry(folder, stage)}",
             "        language: system", "        require_serial: true"]
    if folder:
        lines.append(f"        files: ^{folder}/")
    if STAGES[stage]:
        lines.append(f"        stages: [{STAGES[stage]}]")
    return lines


def body(found):
    """The `- repo: local` block of every root's hooks, or "" when no root declares a hooked stage."""
    hooks = [line for folder in sorted(found) for stage in found[folder] for line in hook(folder, stage)]
    if not hooks:
        return ""
    head = ["  # odw check: the stages each check.toml declares, on the changed paths (command standard §7).",
            "  - repo: local", "    hooks:"]
    return "\n".join(head + hooks) + "\n"


def render(top, files):
    """The generated fragment for odapkit sync: the hooks of a repository's check roots."""
    return body(roots(top, files))
