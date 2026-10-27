"""odapkit cli: the inventory of command-line surfaces and the conformance test, on fixture executables."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap

import pytest

from odw import audit, cli_conformance, cli_inventory, cli_probe, schemas

HOST = pathlib.Path(__file__).resolve().parents[1]

GOOD = '''\
#!/usr/bin/env python3
"""odapkit good: a conforming fixture"""
import argparse, json, sys
CODE = "input.invalid"
if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
    print("odapkit good: a conforming fixture\\n\\nExample: odapkit good --json --dry-run")
    sys.exit(0)
ap = argparse.ArgumentParser(prog="odapkit good", add_help=False)
ap.add_argument("--json", action="store_true")
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()
print("hint: none", file=sys.stderr)
'''
BAD = """\
#!/bin/sh
# odapkit-bad: ignores unknown flags and exits 5 on errors
case "${1:-}" in
  -h|--help) echo "usage: bad"; exit 0 ;;
  ls) echo listing ;;
esac
[ -n "${X:-}" ] && exit 5
exit 0
"""
DANGER = """\
#!/bin/sh
touch "$MARKER"
"""
HANG = """\
#!/bin/sh
case "${1:-}" in --help) sleep 30 ;; esac
exit 2
"""
PROMPT = """\
#!/bin/sh
case "${1:-}" in --help) echo usage; exit 0 ;; *) echo "error" >&2; exit 2 ;; esac
read -p "continue? " answer
"""
WRITER = """\
#!/bin/sh
case "${1:-}" in --help) echo usage > "$HOME/odw-probe-written"; echo usage; exit 0 ;; esac
exit 2
"""
GEN = """\
import pathlib, sys
if __name__ == "__main__":
    pathlib.Path("out.txt").write_text("x")
"""
HOOK = """\
#!/bin/sh
echo "run: make fix" >&2
exit 1
"""
CLAP = """\
use clap::Parser;
#[derive(Parser)]
struct Args { #[arg(long)] json: bool }
fn main() {
    let _ = Args::parse();
    if 2 > 1 { let _ = std::io::BufReader::new(stream()).read_line(&mut String::new()); }
}
"""


def git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, env=audit.git_env(), capture_output=True)


def repo(root, path, files):
    d = root / path
    for name, (text, mode) in files.items():
        f = d / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text))
        f.chmod(mode)
    git(["init", "-q"], d)
    git(["add", "-A"], d)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], d)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKER", str(tmp_path / "danger-ran"))
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/tools\to/t\npublic/system\to/s\npublic/os\to/o\n")
    repo(tmp_path, "public/tools", {
        "bin/odapkit-good": (GOOD, 0o755),
        "bin/odapkit-bad": (BAD, 0o755),
        "scripts/danger.sh": (DANGER, 0o755),
        "scripts/hang.sh": (HANG, 0o755),
        "scripts/prompt.sh": (PROMPT, 0o755),
        "tools/gen.py": (GEN, 0o644),
        "tools/lib.py": ("X = 1\n", 0o644),
        "tools/test_gen.py": ("def test(): pass\n", 0o644),
        "tools/vendor/thing/tools/x.py": ("if __name__ == '__main__': pass\n", 0o644),
        "hooks/check.sh": (HOOK, 0o755),
        "lib/common.sh": ('case "${1:-}" in -h|--help) echo usage; exit 0 ;; esac\n', 0o644),
        "bin/odapkit-lib": ('#!/bin/sh\n. "$(dirname "$0")/../lib/common.sh"\nimport getpass\n'
                            'awk \'NR > 1 { print }\' "$f"\n', 0o755),
        "tools.toml": ('schema = "opendap.tools/v1"\nfront = "odw"\n[[tool]]\nname = "good"\n'
                       'summary = "a conforming fixture"\nentry = "bin/odapkit-good"\naudience = "maintainer"\n'
                       'stability = "preview"\n', 0o644),
        "viewer/Cargo.toml": ('[package]\nname = "odap-view"\nversion = "0.1.0"\n', 0o644),
        "viewer/src/main.rs": (CLAP, 0o644),
    })
    repo(tmp_path, "public/system", {
        "crates/odap-led/Cargo.toml": ('[package]\nname = "odap-led"\nversion = "0.1.0"\n'
                                       '[[bin]]\nname = "odap-led"\npath = "src/main.rs"\n', 0o644),
        "crates/odap-led/src/main.rs": ('fn main() { std::process::exit(4); }\n', 0o644),
    })
    repo(tmp_path, "public/os", {"image/mkosi.extra/usr/bin/odap-reboot": ("#!/bin/sh\nreboot\n", 0o755)})
    return tmp_path


def by_name(items):
    return {e["name"]: e for e in items}


def test_inventory_finds_and_classes_every_executable(ws):
    items = by_name(cli_inventory.inventory(ws))
    assert {k: e["kind"] for k, e in items.items()} == {
        "odapkit-good": "plugin", "odapkit-bad": "plugin", "danger.sh": "script", "hang.sh": "script",
        "prompt.sh": "script", "gen.py": "script", "odapkit-lib": "plugin", "check.sh": "hook", "odap-view": "host-binary",
        "odap-led": "device-binary", "odap-reboot": "device-binary"}
    assert schemas.errors(cli_inventory.document(list(items.values())), "cli-inventory") == []


def test_only_safe_executables_are_marked_to_run(ws):
    items = by_name(cli_inventory.inventory(ws))
    assert items["odapkit-good"]["probe"] and items["hang.sh"]["probe"]
    for name, why in [("danger.sh", "no --help"), ("check.sh", "hook"), ("odap-led", "device-binary"),
                      ("odap-view", "Cargo"), ("odap-reboot", "device-binary")]:
        assert not items[name]["probe"] and why in items[name]["probe_skipped"], name


def test_source_facts(ws):
    items = by_name(cli_inventory.inventory(ws))
    # A comparison is not a write, reading a socket is not a prompt, a sourced library's --help counts.
    assert items["odap-view"]["facts"]["writes"] == [] and not items["odap-view"]["facts"]["prompt"]
    assert items["odapkit-lib"]["facts"]["help"] and items["odapkit-lib"]["facts"]["writes"] == []
    assert not items["odapkit-lib"]["facts"]["prompt"]
    assert items["odapkit-bad"]["facts"]["forbidden_verbs"] == ["ls"]
    assert items["odapkit-bad"]["facts"]["bad_exits"][0]["code"] == 5
    assert items["prompt.sh"]["facts"]["prompt"]
    assert items["gen.py"]["facts"]["writes"] and not items["gen.py"]["facts"]["check"]
    assert items["odap-view"]["facts"]["parser"] == "clap"
    assert items["odap-led"]["facts"]["bad_exits"][0]["code"] == 4


@pytest.fixture
def matrix(ws):
    items = cli_inventory.inventory(ws)
    probes = cli_probe.probe_all(items, ws, timeout=3, jobs=4)
    return cli_conformance.conformance(ws, items, probes), ws


def cells(m, name):
    ids = {o["id"] for o in m["options"] if o["name"] == name}
    return {c["criterion"]: c["state"] for c in m["results"] if c["option"] in ids}


def verdicts(m):
    names = {o["id"]: o["name"] for o in m["options"]}
    return {names[v["option"]]: v["verdict"] for v in m["verdicts"]}


def test_conforming_plugin_passes(matrix):
    m, _ = matrix
    assert {k: v for k, v in cells(m, "odapkit-good").items() if v != "n/a"} == {
        "help": "pass", "help-first-line": "pass", "unknown-flag": "pass", "json": "unverified",
        "no-prompt": "pass", "exit-status": "pass", "error-code": "pass", "verbs": "pass", "tools-toml": "pass"}


def test_failures_are_named(matrix):
    m, _ = matrix
    bad = cells(m, "odapkit-bad")
    for c in ("unknown-flag", "exit-status", "verbs", "tools-toml", "json", "error-code", "help-first-line"):
        assert bad[c] == "fail", c
    assert cells(m, "hang.sh")["no-prompt"] == "fail"
    assert cells(m, "prompt.sh")["no-prompt"] == "fail"
    assert cells(m, "gen.py")["generator-check"] == "fail"
    assert cells(m, "danger.sh")["help"] == "fail"
    assert cells(m, "check.sh")["hook-message"] == "pass"
    assert cells(m, "odap-led")["exit-status"] == "fail"
    assert cells(m, "odap-view")["help"] == "pass" and cells(m, "odap-view")["unknown-flag"] == "pass"
    assert verdicts(m)["odapkit-bad"] == "fail"


def test_never_runs_what_it_may_not(matrix):
    _, ws = matrix
    assert not (ws / "danger-ran").exists()


@pytest.mark.skipif(not shutil.which("sandbox-exec"), reason="the sandbox is macOS only")
def test_probes_cannot_write_outside_their_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    w = tmp_path / "w.sh"
    w.write_text(WRITER)
    w.chmod(0o755)
    out = cli_probe.run([str(w), "--help"], timeout=5)
    assert out["sandboxed"] and not (home / "odw-probe-written").exists()


def test_matrix_and_check_result_validate(matrix):
    m, ws = matrix
    assert schemas.errors(m, "matrix") == []
    assert schemas.errors(cli_conformance.check_result(m, ws), "check-result") == []
    roots = cli_conformance.by_root(m)
    assert roots["public/tools"]["fail"] >= 1 and "unknown-flag" in roots["public/tools"]["failing"]


def test_write_and_dry_run(matrix, tmp_path):
    m, _ = matrix
    cli_conformance.write(m, tmp_path / "dry", dry_run=True)
    assert not (tmp_path / "dry").exists()
    cli_conformance.write(m, tmp_path / "out")
    md = (tmp_path / "out" / "cli-conformance.md").read_text()
    assert md.startswith("<!-- @generated") and "## By root" in md
    assert json.loads((tmp_path / "out" / "cli-conformance.json").read_text())["schema"] == "opendap.matrix/v1"


def test_cli_usage_errors(ws, capsys):
    assert cli_conformance.main(["list", "--workspace", str(ws), "--bogus"]) == 2
    err = capsys.readouterr().err
    assert "error input.invalid" in err and "hint:" in err and "fix:" in err
    assert cli_conformance.main(["list", "--workspace", str(ws), "--root", "public/none", "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "root.not_found" and doc["error"]["fix"]
    assert cli_conformance.main([]) == 2


def test_cli_list_and_check(ws, capsys):
    assert cli_conformance.main(["list", "--workspace", str(ws), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["total"] == 11
    assert cli_conformance.main(["check", "--workspace", str(ws), "--no-probe", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["schema"] == "opendap.check-result/v1"


def test_the_tool_passes_its_own_test(tmp_path):
    """odapkit cli, inventoried and probed in a workspace that holds this checkout, conforms."""
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/toolkit/host\to/h\n")
    (tmp_path / "public" / "toolkit").mkdir(parents=True)
    (tmp_path / "public" / "toolkit" / "host").symlink_to(HOST)
    items = [e for e in cli_inventory.inventory(tmp_path) if e["name"] == "odapkit-cli"]
    assert items and items[0]["probe"]
    m = cli_conformance.conformance(tmp_path, items, cli_probe.probe_all(items, tmp_path, timeout=60))
    states = {c["criterion"]: (c["state"], c["why"]) for c in m["results"]}
    assert all(s in ("pass", "n/a") for s, _ in states.values()), states


def test_closure_follows_modules_named_by_from_imports(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "bin/odapkit-x").write_text('#!/bin/sh\nexec python3 -m pkg.tool "$@"\n')
    (tmp_path / "src/pkg/tool.py").write_text("from pkg import helper, other as o\n")
    (tmp_path / "src/pkg/helper.py").write_text('import argparse\nCODE = "input.invalid"  # hint\n')
    (tmp_path / "src/pkg/__init__.py").write_text("")
    files = ["bin/odapkit-x", "src/pkg/tool.py", "src/pkg/helper.py", "src/pkg/__init__.py"]
    sources = cli_inventory._closure(tmp_path, files, "bin/odapkit-x")
    assert [p for p, _ in sources] == ["bin/odapkit-x", "src/pkg/tool.py", "src/pkg/__init__.py", "src/pkg/helper.py"]
    f = cli_inventory.facts(sources)
    assert f["parser"] == "argparse" and f["error_code"] and f["hint"]
