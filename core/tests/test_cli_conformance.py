"""odw cli: the inventory of command-line surfaces and the conformance test, on fixture executables."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap

import pytest

from clifixtures import HOST, TOP, by_name, git, repo
from odw import audit, cli_conformance, cli_inventory, cli_probe, schemas

ROOT_PROJECT = ("pyproject.toml", "uv.lock", "conftest.py")  # the tools root's Python project (HL-71)

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


def test_inventory_finds_and_classes_every_executable(ws):
    items = by_name(cli_inventory.inventory(ws))
    assert {k: e["kind"] for k, e in items.items()} == {
        "odapkit-good": "plugin", "odapkit-bad": "plugin", "danger.sh": "script", "hang.sh": "script",
        "prompt.sh": "script", "gen.py": "script", "odapkit-lib": "plugin", "check.sh": "hook", "odap-view": "host-binary",
        "odap-led": "device-binary", "odap-reboot": "device-binary"}
    assert schemas.errors(cli_inventory.document(list(items.values())), "cli-inventory") == []


def test_a_merged_blackbird_root_keeps_system_binaries_on_the_device(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/blackbird\to/blackbird\n")
    repo(tmp_path, "public/blackbird", {
        "system/crates/odap-led/Cargo.toml": ('[package]\nname = "odap-led"\nversion = "0.1.0"\n'
                                              '[[bin]]\nname = "odap-led"\npath = "src/main.rs"\n', 0o644),
        "system/crates/odap-led/src/main.rs": ('fn main() { std::process::exit(4); }\n', 0o644),
        "emulator/viewer/Cargo.toml": ('[package]\nname = "odap-view"\nversion = "0.1.0"\n', 0o644),
        "emulator/viewer/src/main.rs": (CLAP, 0o644),
    })
    kinds = {k: e["kind"] for k, e in by_name(cli_inventory.inventory(tmp_path)).items()}
    assert kinds == {"odap-led": "device-binary", "odap-view": "host-binary"}  # the emulator runs on the host


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


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=audit.git_env()).stdout


def _conforms(ws):
    items = [e for e in cli_inventory.inventory(ws) if e["name"] == "odw-cli"]
    assert items and items[0]["probe"]
    m = cli_conformance.conformance(ws, items, cli_probe.probe_all(items, ws, timeout=60))
    states = {c["criterion"]: (c["state"], c["why"]) for c in m["results"]}
    assert all(s in ("pass", "n/a") for s, _ in states.values()), {k: v for k, v in states.items() if v[0] not in ("pass", "n/a")}


def test_the_tool_passes_its_own_test(tmp_path):
    """odw cli, inventoried and probed in a workspace that holds this checkout, conforms. The checkout's
    git root is linked, so host/ is the root (toolkit-host) or a folder of it (the merged tools root)."""
    top = pathlib.Path(_git(["rev-parse", "--show-toplevel"], HOST).strip()).resolve()
    root = "public/toolkit/host" if top == HOST else "public/tools"
    (tmp_path / "repos.tsv").write_text(f"path\trepository\n{root}\to/h\n")
    (tmp_path / root).parent.mkdir(parents=True)
    (tmp_path / root).symlink_to(top)
    _conforms(tmp_path)


def test_the_tool_passes_its_own_test_on_a_copy(tmp_path):
    """The same, on a copy of this checkout's tracked files: core/, host/ and schemas/ of the tools root, with the
    root's Python project (pyproject.toml, uv.lock, conftest.py) that builds odw since HL-71."""
    repo = tmp_path / "public/tools"
    top = HOST.parent
    files = [f for f in _git(["ls-files", "-z"], top).split("\0") if f.split("/")[0] in ("core", "host", "schemas")]
    files += list(ROOT_PROJECT)
    for f in files:
        if (top / f).is_file():
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(top / f, repo / f)
    _git(["init", "-q"], repo)
    _git(["add", "-A"], repo)
    _git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], repo)
    # The probe's sandbox writes only inside an existing .venv, as in a real checkout.
    subprocess.run(["uv", "sync", "--quiet", "--frozen", "--project", str(repo)], check=True, env=audit.git_env())
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/tools\to/h\n")
    _conforms(tmp_path)


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


def test_writing_to_os_devnull_is_not_a_state_change():
    f = cli_inventory.facts([("tools/x.py", 'import os\nnull = open(os.devnull, "w")\n')])
    assert f["writes"] == []


@pytest.mark.parametrize("layout", ["public/toolkit/host", "public/tools/host"])
def test_summaries_in_either_toolkit_layout(tmp_path, layout):
    """A repository per toolkit folder, or the merged `tools` root whose folders hold their own tools.toml."""
    folder = tmp_path / layout
    (folder / "bin").mkdir(parents=True)
    (folder / "tools.toml").write_text('schema = "opendap.tools/v1"\nfront = "odw"\n[[tool]]\nname = "good"\n'
                                       'summary = "a conforming fixture"\nentry = "bin/odapkit-good"\n')
    root = layout.rsplit("/", 1)[0] if layout.startswith("public/tools/") else layout
    items = [{"root": root, "path": f"{layout}/bin/odapkit-good"}]
    assert cli_conformance.summaries(tmp_path, items) == {f"{layout}/bin/odapkit-good": "a conforming fixture"}


# HL-63: the read-only --json call of each tool is declared in its tools.toml (`probe`), one metadata source.
def test_a_probe_declared_in_tools_toml_is_attached_and_run(ws):
    toml = ws / "public/tools/tools.toml"
    toml.write_text(toml.read_text() + 'probe = ["--json"]\n')
    good = by_name(cli_inventory.inventory(ws))["odapkit-good"]
    assert good["json_probe"] == [["--json"]] and good["declared_in"] == "public/tools"
    assert "json_probe" not in by_name(cli_inventory.inventory(ws))["odapkit-bad"]
    probes = cli_probe.probe_all([good], ws, timeout=10)
    assert probes[good["path"]]["json"]["argv"][-1] == "--json"


def test_probe_arguments_resolve_the_root_and_the_workspace(tmp_path):
    entry = {"kind": "plugin", "json_probe": [["get", "{root}/tests/fixtures/x", "--workspace", "{workspace}", "--json"]],
             "declared_in": "public/tools/host"}
    assert cli_probe.json_args(entry, tmp_path) == [[
        "get", f"{tmp_path}/public/tools/host/tests/fixtures/x", "--workspace", str(tmp_path), "--json"]]
    assert cli_probe.json_args({"kind": "umbrella", "name": "odw"}, tmp_path) == [["help", "--json"]]
    assert cli_probe.json_args({"kind": "plugin", "name": "odapkit-x"}, tmp_path) is None
    assert not hasattr(cli_probe, "JSON_PROBES")


def test_the_tools_schema_takes_a_probe_with_json():
    doc = {"schema": "opendap.tools/v1", "front": "odw", "tool": [{
        "name": "x", "summary": "s", "entry": "bin/x", "audience": "maintainer", "stability": "preview"}]}
    assert schemas.errors(doc, "tools") == []
    doc["tool"][0]["probe"] = ["list", "--json"]
    assert schemas.errors(doc, "tools") == []
    for bad in ([], ["list"], ["list", 1]):
        doc["tool"][0]["probe"] = bad
        assert schemas.errors(doc, "tools"), bad


def test_the_tools_schema_takes_a_list_of_probe_calls():
    """HL-105: one call, or a list of calls (each with --json) when a tool's scripts answer different calls."""
    doc = {"schema": "opendap.tools/v1", "front": "odw", "tool": [{
        "name": "x", "summary": "s", "entry": "bin/x", "audience": "maintainer", "stability": "preview",
        "probe": [["check", "--json", "a"], ["rules", "render", "s.md", "--check", "--json"]]}]}
    assert schemas.errors(doc, "tools") == []
    for bad in ([[]], [["check", "--json"], ["rules"]], [["check", "--json"], "--json"]):
        doc["tool"][0]["probe"] = bad
        assert schemas.errors(doc, "tools"), bad


MIGRATED = {"core": {"cli"}, "host": {"audit", "docs", "noindex", "plan", "rename", "req", "sync", "verify"},
            "hardware": {"component", "craft", "sim", "variant", "stackup"}}


@pytest.mark.skipif(not (HOST.parent / "hardware" / "tools.toml").is_file(), reason="not the merged tools root")
def test_every_tool_that_had_a_probe_declares_it_in_its_tools_toml():
    import tomllib
    for folder, names in MIGRATED.items():
        doc = tomllib.loads((HOST.parent / folder / "tools.toml").read_text())
        assert schemas.errors(doc, "tools") == [], folder
        declared = {t["name"] for t in doc["tool"] if t.get("probe")}
        assert names <= declared, (folder, names - declared)


def test_a_list_of_probe_calls_runs_each_and_fails_on_any(ws):
    """HL-105: every declared call is run; the json cell passes only when each prints a document with a schema."""
    toml = ws / "public/tools/tools.toml"
    toml.write_text(toml.read_text() + 'probe = [["--json"], ["--json", "--dry-run"]]\n')
    good = by_name(cli_inventory.inventory(ws))["odapkit-good"]
    assert good["json_probe"] == [["--json"], ["--json", "--dry-run"]]
    probes = cli_probe.probe_all([good], ws, timeout=10)
    assert [j["argv"][-1] for j in probes[good["path"]]["json_calls"]] == ["--json", "--dry-run"]
    m = cli_conformance.conformance(ws, [good], probes)
    assert {c["criterion"]: c["state"] for c in m["results"]}["json"] == "fail"  # good prints no JSON
