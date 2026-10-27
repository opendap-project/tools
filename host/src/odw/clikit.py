"""odw clikit: the command-line behaviour every odapkit tool shares (command standard §5, §6).

- `CliError(code, message, hint, fix, status=1)`: an error an agent can act on: a stable dotted code, a hint and
  a fix (a command to run).
- `report(tool, error, as_json)`: prints it on stderr as text and, with --json, as an `opendap.error/v1` document
  on stdout; returns its exit status.
- `Parser`: argparse whose `--help` starts `<front> <tool>: <summary>` and ends with examples, and whose usage
  errors (an unknown flag, a bad argument, an unknown subcommand) are `CliError("input.invalid", status=2)`.
- `summary(name)`: the tool's summary from this root's `tools.toml`, the one source of that first line.
- `main(tool, body, argv)`: runs `body(argv)` and turns `--help` into 0 and a `CliError` into its report.

Shell tools use the same contract through `lib/cli.sh`. Spec: docs/specs/cli-conformance.md (Shared helper).
"""
import argparse
import functools
import json
import pathlib
import sys
import tomllib

ERROR_SCHEMA = "opendap.error/v1"
# Installed, tools.toml ships inside the package (odw/tools.toml); in a checkout it is at the repository root.
_HERE = pathlib.Path(__file__).resolve().parent
TOOLS_TOML = _HERE / "tools.toml" if (_HERE / "tools.toml").is_file() else _HERE.parents[1] / "tools.toml"


@functools.cache
def summary(name):
    """The summary tools.toml declares for tool `name` (the one source of the --help first line)."""
    tools = tomllib.loads(TOOLS_TOML.read_text()).get("tool", []) if TOOLS_TOML.is_file() else []
    return next((t["summary"] for t in tools if t["name"] == name), None)


class CliError(Exception):
    def __init__(self, code, message, hint, fix, status=1):
        super().__init__(message)
        self.code, self.message, self.hint, self.fix, self.status = code, message, hint, fix, status


def report(tool, e, as_json=False):
    print(f"{tool}: error {e.code}: {e.message}\n  hint: {e.hint}\n  fix: {e.fix}", file=sys.stderr)
    if as_json:
        print(json.dumps({"schema": ERROR_SCHEMA, "ok": False, "error": {
            "code": e.code, "message": e.message, "hint": e.hint, "fix": [{"command": e.fix}]}}, ensure_ascii=False))
    return e.status


def no_workspace(tool):
    return CliError("workspace.not_found", "not inside a workspace (no repos.tsv here or above)",
                    "run it inside a workspace checkout, or name one", f"{tool} --workspace DIR", status=2)


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, summary=None, examples=(), **kwargs):
        if examples:
            kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        super().__init__(*args, **kwargs)
        self.summary, self.examples = summary, list(examples)

    def error(self, message):
        top = self.prog.split(" ")[:2]
        raise CliError("input.invalid", message, f"the usage is in {' '.join(top)} --help",
                       f"{self.prog} --help", status=2)

    def format_help(self):
        text = super().format_help()
        if self.summary:
            text = f"{self.prog}: {self.summary}\n\n{text}"
        if self.examples:
            text += "\nExamples:\n" + "".join(f"  {x}\n" for x in self.examples)
        return text


def main(tool, body, argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        return body(argv)
    except SystemExit as e:
        return 0 if not e.code else 2 if e.code is True or isinstance(e.code, str) else e.code
    except CliError as e:
        return report(tool, e, "--json" in argv)
