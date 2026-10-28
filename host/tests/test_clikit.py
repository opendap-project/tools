"""odw clikit: the shared command-line behaviour (command standard §5, §6) every odapkit tool uses."""
import json
import os
import pathlib
import subprocess

import pytest

from odw import clikit

HOST = pathlib.Path(__file__).resolve().parents[1]


def _parser():
    ap = clikit.Parser(prog="odapkit demo", summary="A demo tool",
                       examples=["odapkit demo --json", "odapkit demo --dry-run"])
    ap.add_argument("--json", action="store_true")
    return ap


def test_help_starts_with_front_tool_and_summary(capsys):
    with pytest.raises(SystemExit) as e:
        _parser().parse_args(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "odapkit demo: A demo tool"
    assert "Examples:\n  odapkit demo --json\n" in out


def test_unknown_flag_is_a_usage_error_with_code_hint_and_fix():
    with pytest.raises(clikit.CliError) as e:
        _parser().parse_args(["--no-such-flag"])
    err = e.value
    assert (err.code, err.status) == ("input.invalid", 2)
    assert "--no-such-flag" in err.message
    assert err.fix == "odapkit demo --help"


def test_report_prints_text_on_stderr_and_an_error_document_with_json(capsys):
    err = clikit.CliError("workspace.not_found", "no workspace", "run it in a workspace", "odapkit demo --workspace DIR")
    assert clikit.report("odapkit demo", err, as_json=True) == 1
    out, errs = capsys.readouterr()
    assert errs == ("odapkit demo: error workspace.not_found: no workspace\n  hint: run it in a workspace\n"
                    "  fix: odapkit demo --workspace DIR\n")
    doc = json.loads(out)
    assert doc == {"schema": "opendap.error/v1", "ok": False, "error": {
        "code": "workspace.not_found", "message": "no workspace", "hint": "run it in a workspace",
        "fix": [{"command": "odapkit demo --workspace DIR"}]}}


def test_report_without_json_prints_nothing_on_stdout(capsys):
    clikit.report("odapkit demo", clikit.CliError("a.b", "m", "h", "f"), as_json=False)
    assert capsys.readouterr().out == ""


def test_main_maps_help_errors_and_results_to_exit_status(capsys):
    def body(argv):
        args = _parser().parse_args(argv)
        if args.json:
            raise clikit.CliError("demo.refused", "refused", "h", "f")
        return 0
    assert clikit.main("odapkit demo", body, []) == 0
    assert clikit.main("odapkit demo", body, ["--help"]) == 0
    assert clikit.main("odapkit demo", body, ["--bad"]) == 2
    assert clikit.main("odapkit demo", body, ["--json"]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["error"]["code"] == "demo.refused"


def test_no_workspace_names_the_flag_that_fixes_it():
    err = clikit.no_workspace("odapkit demo")
    assert err.code == "workspace.not_found" and err.status == 2 and "--workspace" in err.fix


def test_subcommand_usage_errors_are_cli_errors_too():
    ap = clikit.Parser(prog="odapkit demo", summary="A demo tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    with pytest.raises(clikit.CliError):
        ap.parse_args(["list", "--bad"])
    with pytest.raises(clikit.CliError):
        ap.parse_args(["nope"])


def _sh(script, *args, env=None):
    full = f'. "{HOST}/lib/cli.sh"\n{script}'
    return subprocess.run(["sh", "-c", full, "t", *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def test_shell_helper_fails_with_code_hint_fix_and_status():
    r = _sh('CLI_TOOL="odapkit demo"; cli_fail tool.not_found "no tool x" "odapkit help lists them" "odapkit help" 2')
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr == "odapkit demo: error tool.not_found: no tool x\n  hint: odapkit help lists them\n  fix: odapkit help\n"


def test_shell_helper_prints_the_error_document_with_json():
    r = _sh('CLI_TOOL="odapkit demo"; CLI_JSON=1; cli_fail a.b \'say "hi"\' "h\\\\x" "f"')
    assert r.returncode == 1
    assert json.loads(r.stdout)["error"] == {"code": "a.b", "message": 'say "hi"', "hint": "h\\x",
                                             "fix": [{"command": "f"}]}


def test_shell_helper_json_string_escapes():
    r = _sh('cli_json_str "a\\"b\\\\c\tz"')
    assert json.loads(r.stdout) == 'a"b\\c\tz'


def test_shell_helper_never_prompts_without_a_tty():
    assert _sh("cli_interactive && echo yes || echo no").stdout.strip() == "no"


def test_summary_reads_another_roots_tools_toml(tmp_path):
    f = tmp_path / "tools.toml"
    f.write_text('schema = "opendap.tools/v1"\nfront = "odw"\n\n[[tool]]\nname = "x"\nsummary = "Does x"\n'
                 'entry = "bin/odapkit-x"\naudience = "maintainer"\nstability = "preview"\n')
    assert clikit.summary("x", f) == "Does x" and clikit.summary("y", f) is None


def test_clikit_imports_on_python_3_9_syntax_and_stdlib():
    # KiCad's bundled Python is 3.9: no tomllib at import time, no 3.10+ syntax.
    import ast
    src = (HOST / "src/odw/clikit.py").read_text()
    tree = ast.parse(src, feature_version=(3, 9))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(a.name == "tomllib" for n in top for a in n.names)


def test_load_toml_fix_quotes_the_path(tmp_path):
    """The fix is a command an agent runs: a path with shell syntax in it is quoted, never executed (HL-99)."""
    import shlex
    f = tmp_path / "a $(touch pwned); b.toml"
    f.write_text('x = "\n')
    with pytest.raises(clikit.CliError) as e:
        clikit.load_toml(f, "x.invalid")
    assert e.value.fix == f"$EDITOR {shlex.quote(str(f))}" and e.value.details["line"] == 1
