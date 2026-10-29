"""odapkit cli: the umbrellas (HL-102) and the scripts a tool runs (HL-100) in the conformance test."""
import json

import pytest

from odw import cli_conformance, cli_inventory, cli_probe, cli_rules, schemas
from test_cli_conformance import HOST, by_name, repo


# HL-102: the two umbrellas pass the conformance test: odapkit names --help and its error code in its own
# source, and odw, a Cargo binary, is probed through the build its checkout made.
UMBRELLA_BUILD = """\
#!/bin/sh
case "${1:-}" in
  -h|--help) echo "odw: the maintainers' umbrella (odw help --json)"; exit 0 ;;
  help) echo '{"schema":"opendap.tools-list/v1","items":[]}'; exit 0 ;;
  *) echo "odw: error input.invalid: unknown flag" >&2; echo "  hint: odw --help" >&2; exit 2 ;;
esac
"""


def _umbrella_ws(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/tools\to/t\n")
    repo(tmp_path, "public/tools", {
        "host/bin/odapkit": ((HOST / "bin" / "odapkit").read_text(), 0o755),
        "host/crates/odw-dispatch/Cargo.toml": ('[package]\nname = "odw-dispatch"\nversion = "0.1.0"\n'
                                                '[[bin]]\nname = "odw"\npath = "src/main.rs"\n', 0o644),
        "host/crates/odw-dispatch/src/main.rs": ("fn main() { odw_dispatch::run(); }\n", 0o644),
        "host/crates/odw-dispatch/src/cli.rs": ('match a { "-h" | "--help" => {} }\n'
                                                'eprintln!("error tool.not_found"); let code = "input.invalid"; '
                                                '// hint\n', 0o644),
    })
    return tmp_path


def test_the_odapkit_alias_names_help_and_a_coded_error_in_its_source(tmp_path):
    items = by_name(cli_inventory.inventory(_umbrella_ws(tmp_path)))
    alias = items["odapkit"]
    assert alias["kind"] == "umbrella" and alias["probe"], alias["probe_skipped"]
    assert alias["facts"]["help"] and alias["facts"]["error_code"] and alias["facts"]["hint"]


def test_an_unbuilt_odw_is_read_from_its_crate_and_never_fails_help(tmp_path):
    ws = _umbrella_ws(tmp_path)
    odw = by_name(cli_inventory.inventory(ws))["odw"]
    assert odw["kind"] == "umbrella" and not odw["probe"] and "not built" in odw["probe_skipped"]
    assert odw["facts"]["help"]  # named in cli.rs, a source of the crate, not in main.rs
    assert cli_rules.cell("help", odw, None, {"summaries": {}})[0] == "unverified"


def test_a_built_odw_is_probed_as_the_binary(tmp_path):
    ws = _umbrella_ws(tmp_path)
    built = ws / "public/tools/host/target/release/odw"
    built.parent.mkdir(parents=True)
    built.write_text(UMBRELLA_BUILD)
    built.chmod(0o755)
    odw = by_name(cli_inventory.inventory(ws))["odw"]
    assert odw["probe"] and odw["exec"] == str(built)
    probes = cli_probe.probe_all([odw], ws, timeout=10)
    m = cli_conformance.conformance(ws, [odw], probes)
    states = {c["criterion"]: c["state"] for c in m["results"]}
    assert {k: v for k, v in states.items() if v not in ("pass", "n/a")} == {}, states


# HL-100: a script a declared tool runs is verified through that tool's own tools.toml probe, when the probe's
# run reaches it (a shell parent's json probe is traced), never by a table of its own.
PARENT = """\
#!/bin/sh
# odapkit par: a parent that runs one script per command
set -eu
here="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
case "${1:-}" in
  -h|--help) echo "odapkit par: a parent fixture (odapkit par a --json)"; exit 0 ;;
  a) shift; exec python3 "$here/tools/a.py" "$@" ;;
  b) shift; exec python3 "$here/tools/b.py" "$@" ;;
  *) echo "odapkit par: error input.invalid: unknown command" >&2; echo "  hint: odapkit par --help" >&2; exit 2 ;;
esac
"""
CHILD = """\
#!/usr/bin/env python3
import argparse, json
ap = argparse.ArgumentParser()
ap.add_argument("--json", action="store_true")
ap.parse_args()
print(json.dumps({"schema": "opendap.%s/v1"}))
"""


def _parent_ws(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/tools\to/t\n")
    repo(tmp_path, "public/tools", {
        "bin/odapkit-par": (PARENT, 0o755),
        "tools/a.py": (CHILD % "a", 0o755),
        "tools/b.py": (CHILD % "b", 0o755),
        "tools.toml": ('schema = "opendap.tools/v1"\nfront = "odw"\n[[tool]]\nname = "par"\n'
                       'summary = "a parent fixture"\nentry = "bin/odapkit-par"\naudience = "maintainer"\n'
                       'stability = "preview"\nprobe = ["a", "--json"]\n', 0o644),
    })
    return tmp_path


def test_a_script_is_verified_through_the_parent_probe_that_reaches_it(tmp_path):
    ws = _parent_ws(tmp_path)
    items = cli_inventory.inventory(ws)
    probes = cli_probe.probe_all(items, ws, timeout=10)
    assert probes["public/tools/bin/odapkit-par"]["json"]["reached"] == ["public/tools/tools/a.py"]
    m = cli_conformance.conformance(ws, items, probes)
    why = {(c["option"], c["criterion"]): (c["state"], c["why"]) for c in m["results"]}
    state, reason = why[("public/tools/tools/a.py", "json")]
    assert state == "pass" and "odapkit-par" in reason and "opendap.a/v1" in reason
    assert why[("public/tools/tools/b.py", "json")][0] == "unverified"  # no probe of its parent reaches it
    assert why[("public/tools/bin/odapkit-par", "json")][0] == "pass"


def test_the_trace_does_not_change_what_the_parent_prints(tmp_path):
    ws = _parent_ws(tmp_path)
    par = by_name(cli_inventory.inventory(ws))["odapkit-par"]
    j = cli_probe.probe(par, ws, timeout=10)["json"]
    assert j["exit"] == 0 and json.loads(j["stdout"]) == {"schema": "opendap.a/v1"}
    assert not any(line.startswith("+") for line in j["stderr"].splitlines())


@pytest.mark.skipif(not (HOST.parent / "hardware" / "tools.toml").is_file(), reason="not the merged tools root")
def test_sch_craft_check_is_verified_through_the_craft_probe(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/tools\to/t\n")
    (tmp_path / "public").mkdir()
    (tmp_path / "public/tools").symlink_to(HOST.parent)
    items = [e for e in cli_inventory.inventory(tmp_path)
             if e["name"] in ("odapkit-craft", "sch-craft-check.py")]
    m = cli_conformance.conformance(tmp_path, items, cli_probe.probe_all(items, tmp_path, timeout=60))
    why = {(c["option"], c["criterion"]): (c["state"], c["why"]) for c in m["results"]}
    assert why[("public/tools/hardware/tools/sch-craft-check.py", "json")][0] == "pass", why


# HL-105: a call names the script it reaches (`reaches`, from the tools.toml folder); that script, and only it, is
# credited by the call.
def test_the_tools_schema_takes_a_call_that_names_its_script():
    doc = {"schema": "opendap.tools/v1", "front": "odw", "tool": [{
        "name": "x", "summary": "s", "entry": "bin/x", "audience": "maintainer", "stability": "preview",
        "probe": [["a", "--json"], {"call": ["b", "--json"], "reaches": "tools/b.py"}]}]}
    assert schemas.errors(doc, "tools") == []
    for bad in ([{"call": ["b"]}], [{"reaches": "tools/b.py"}], [{"call": ["b", "--json"], "colour": "red"}]):
        doc["tool"][0]["probe"] = bad
        assert schemas.errors(doc, "tools"), bad


def test_a_declared_call_credits_the_script_it_names(tmp_path):
    ws = _parent_ws(tmp_path)
    toml = ws / "public/tools/tools.toml"
    toml.write_text(toml.read_text().replace(
        'probe = ["a", "--json"]',
        'probe = [{ call = ["a", "--json"], reaches = "tools/a.py" }, { call = ["b", "--json"], reaches = "tools/b.py" }]'))
    items = cli_inventory.inventory(ws)
    par = by_name(items)["odapkit-par"]
    assert par["json_probe"] == [["a", "--json"], ["b", "--json"]]
    assert par["json_reaches"] == ["public/tools/tools/a.py", "public/tools/tools/b.py"]
    m = cli_conformance.conformance(ws, items, cli_probe.probe_all(items, ws, timeout=10))
    why = {(c["option"], c["criterion"]): (c["state"], c["why"]) for c in m["results"]}
    for script, schema in (("a", "opendap.a/v1"), ("b", "opendap.b/v1")):
        state, reason = why[(f"public/tools/tools/{script}.py", "json")]
        assert state == "pass" and schema in reason, reason
