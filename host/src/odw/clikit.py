"""odw clikit: the command-line behaviour every odapkit tool shares (command standard §5, §6).

- `CliError(code, message, hint, fix, status=1)`: an error an agent can act on: a stable dotted code, a hint and
  a fix (a command to run).
- `report(tool, error, as_json)`: prints it on stderr as text and, with --json, as an `opendap.error/v1` document
  on stdout; returns its exit status.
- `Parser`: argparse whose `--help` starts `<front> <tool>: <summary>` and ends with examples, and whose usage
  errors (an unknown flag, a bad argument, an unknown subcommand) are `CliError("input.invalid", status=2)`.
- `summary(name, tools_toml=None)`: the tool's summary from a root's `tools.toml` (this one by default), the one
  source of that first line. The module loads on Python 3.9 (KiCad's); `summary` needs 3.11 (tomllib).
- `load_toml(path, code, shown, fix, status)`: a TOML file's contents; a file that does not parse is `CliError(code)` with
  its file and line (`error.file`, `error.line` in the JSON document), never a traceback.
- `main(tool, body, argv)`: runs `body(argv)` and turns `--help` into 0 and a `CliError` into its report.

Shell tools use the same contract through `lib/cli.sh`. Spec: docs/specs/cli-conformance.md (Shared helper).
"""
import argparse
import functools
import json
import pathlib
import sys

ERROR_SCHEMA = "opendap.error/v1"
# Installed, tools.toml ships inside the package (odw/tools.toml); in a checkout it is at the repository root.
_HERE = pathlib.Path(__file__).resolve().parent
TOOLS_TOML = _HERE / "tools.toml" if (_HERE / "tools.toml").is_file() else _HERE.parents[1] / "tools.toml"


@functools.lru_cache(maxsize=None)
def summary(name, tools_toml=None):
    """The summary a tools.toml (this root's by default) declares for tool `name`: the one source of the --help
    first line. tomllib is imported here, so the module itself loads on Python 3.9 (KiCad's)."""
    import tomllib
    f = pathlib.Path(tools_toml) if tools_toml else TOOLS_TOML
    tools = tomllib.loads(f.read_text()).get("tool", []) if f.is_file() else []
    return next((t["summary"] for t in tools if t["name"] == name), None)


class CliError(Exception):
    def __init__(self, code, message, hint, fix, status=1, details=None):
        super().__init__(message)
        self.code, self.message, self.hint, self.fix, self.status = code, message, hint, fix, status
        self.details = details or {}


def load_toml(path, code, shown=None, fix=None, status=1):
    """The parsed TOML file at `path`. A file that does not parse is `CliError(code)` naming `shown` (the path as
    the user knows it), its line and column, with a hint on quoting and a fix (`fix`, else open the file)."""
    import re
    import shlex
    import tomllib
    path = pathlib.Path(path)
    shown = shown or str(path)
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        m = re.search(r"\(at line (\d+), column (\d+)\)", str(e))
        line, col = (int(m[1]), int(m[2])) if m else (getattr(e, "lineno", None), getattr(e, "colno", None))
        what = re.sub(r"\s*\(at (line \d+, column \d+|end of document)\)", "", getattr(e, "msg", str(e)))
        where = f"{shown}:{line}:{col}" if line else shown
        raise CliError(code, f"{where}: not valid TOML: {what}",
                       "a basic string escapes its quotes and backslashes (\\\", \\\\) or is a literal string ('...')",
                       fix or f"$EDITOR {shlex.quote(str(path))}", status, details={"file": shown, "line": line, "column": col}) from None


def report(tool, e, as_json=False):
    print(f"{tool}: error {e.code}: {e.message}\n  hint: {e.hint}\n  fix: {e.fix}", file=sys.stderr)
    if as_json:
        print(json.dumps({"schema": ERROR_SCHEMA, "ok": False, "error": {
            "code": e.code, "message": e.message, "hint": e.hint, "fix": [{"command": e.fix}], **e.details}}, ensure_ascii=False))
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
