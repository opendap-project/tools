"""odw rename apply on a root cut as structure only (each source whole in its folder), shaped like the tools
rehearsal of 2026-10-27: fixtures and pre-commit pins stay, generated files follow their source, and URLs of a root
cut earlier (kicad-lib, no longer in repos.tsv) are rewritten. Synthetic workspaces only."""
import json
import subprocess

import pytest

from odw import audit, rename, requirements, schemas

ORG = "https://github.com/opendap-project"
MAP = [
    ("repo:opendap-project/kicad-lib", "repo:opendap-project/libs (kicad/)", "repo", "merge", ""),
    ("repo:opendap-project/toolkit-host", "repo:opendap-project/tools workspace/ core/", "repo", "merge", ""),
    ("repo:opendap-project/toolkit-hooks", "repo:opendap-project/tools (hooks/)", "repo", "merge", ""),
    ("public/libs/kicad/symbols/Murata.kicad_sym", "libs/kicad/symbols/murata.kicad_sym", "library", "rename", ""),
    ("public/toolkit/host/src/plan.py", "tools/workspace/plan.py", "tool", "move", "the domain split"),
    ("public/toolkit/host/docs/old.md", "", "doc", "delete", ""),
    ("public/toolkit/host/bin/odap-inject", "blackbird/bin/odap-inject", "tool", "move", "leaves for blackbird"),
    ("env:ODAP_TOOLKIT_HOST_DIR", "env:ODAP_TOOLS_DIR", "env", "rename", ""),
]
LANDINGS = ["--landing", "toolkit-host=host", "--landing", "toolkit-hooks=hooks"]
REQ_TOML = ('schema = "opendap.requirements/v1"\n\n[[block]]\nmarkdown = "# Host requirements\\n"\n\n[[block]]\n'
            '[[block.requirement]]\nid = "TK-100"\ntext = "Hooks follow [the hooks guide](' + ORG
            + '/toolkit-hooks/blob/main/README.md)."\npriority = "must"\nstatus = "confirmed"\n')
HOST = {
    "README.md": (f"# Host\n\nHooks: [guide]({ORG}/toolkit-hooks/blob/main/README.md); "
                  f"library: {ORG}/kicad-lib/blob/main/symbols/Murata.kicad_sym and {ORG}/kicad-lib.\n"
                  "Set ODAP_TOOLKIT_HOST_DIR. See [plan](src/plan.py).\n"),
    "src/plan.py": "print('plan')\n",
    "docs/old.md": "# Old\n",
    "bin/odap-inject": "#!/bin/sh\n",
    "Cargo.toml": f'[package]\nname = "odw"\nrepository = "{ORG}/toolkit-host"\nhomepage = "{ORG}/toolkit-host"\n',
    ".pre-commit-config.yaml": (f"repos:\n  - repo: {ORG}/toolkit-hooks\n    rev: v0.4.1\n    hooks:\n"
                                f"      - id: no-stray-files\n# docs: {ORG}/toolkit-hooks/blob/main/README.md\n"),
    "tests/fixtures/pinned.md": f"# Pinned (sha256 in a test)\n\n{ORG}/toolkit-hooks/blob/main/README.md\n",
    "tests/test_audit.py": f'HOOKS = "{ORG}/toolkit-hooks"  # test input: the old pin must still be read\n',
    "docs/requirements/requirements.toml": REQ_TOML,
}
HOOKS = {"README.md": "# Hooks\n\nBack to [host](https://github.com/opendap-project/toolkit-host).\n"}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env=audit.git_env()).stdout


@pytest.fixture
def cut(tmp_path):
    """The workspace after the libs cut (public/libs is one repository; kicad-lib is archived) and the merged
    tools root before apply."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "repos.tsv").write_text("path\trepository\npublic/libs\topendap-project/libs\n"
                                  "public/toolkit/host\topendap-project/toolkit-host\n"
                                  "public/toolkit/hooks\topendap-project/toolkit-hooks\n")
    (tmp_path / "map.tsv").write_text("old\tnew\tkind\taction\tnotes\tscope\n"
                                      + "".join("\t".join([*r, "test"]) + "\n" for r in MAP))
    top = tmp_path / "tools"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    git(top, "config", "user.email", "t@example.org")
    git(top, "config", "user.name", "T")
    files = {**{f"host/{k}": v for k, v in HOST.items()}, **{f"hooks/{k}": v for k, v in HOOKS.items()}}
    files["host/docs/requirements/README.md"] = requirements.render(requirements.tomllib.loads(REQ_TOML))
    for name, text in files.items():
        (top / name).parent.mkdir(parents=True, exist_ok=True)
        (top / name).write_text(text)
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", "tools: join with history")
    return ws, top


def run(capsys, verb, ws, top, *extra):
    code = rename.main([verb, "tools", "--workspace", str(ws), "--map", str(ws.parent / "map.tsv"), "--root-dir",
                        str(top), "--cut", "libs,tools", *LANDINGS, *extra])
    out, err = capsys.readouterr()
    return code, out, err


def test_structure_only_keeps_every_path_and_identifier(cut, capsys):
    ws, top = cut
    code, out, err = run(capsys, "plan", ws, top, "--structure-only", "--json")
    assert code == 0, err
    doc = json.loads(out)
    assert not schemas.errors(doc, "rename-plan") and doc["structure_only"] is True
    assert not doc["moves"] and not doc["deletes"] and not doc["pending"] and not doc["identifiers"]
    assert [(c["direction"], c["to"]) for c in doc["cross_root"]] == [("out", "blackbird/bin/odap-inject")]
    assert not any(e["kind"] == "identifier" for e in doc["edits"])
    assert not any("src/plan.py" in (e["old"] + e["new"]) for e in doc["edits"])  # the domain split waits


def test_structure_only_apply_rewrites_urls_into_the_folders(cut, capsys):
    ws, top = cut
    code, out, err = run(capsys, "apply", ws, top, "--structure-only", "--json")
    assert code == 0, err
    doc = json.loads(out)
    assert len(doc["commits"]) == 1  # no moves: only the references commit
    files = set(audit.tracked(top))
    assert {"host/src/plan.py", "host/docs/old.md", "host/bin/odap-inject"} <= files
    readme = (top / "host/README.md").read_text()
    assert "[guide](../hooks/README.md)" in readme  # a URL into the root itself becomes relative
    assert "ODAP_TOOLKIT_HOST_DIR" in readme and "[plan](src/plan.py)" in readme
    assert "[host](../host)" in (top / "hooks/README.md").read_text()  # a bare repository URL: its folder


def test_urls_of_a_root_cut_earlier_follow_its_map(cut, capsys):
    ws, top = cut
    assert run(capsys, "apply", ws, top, "--structure-only")[0] == 0
    readme = (top / "host/README.md").read_text()
    assert f"{ORG}/libs/blob/main/kicad/symbols/murata.kicad_sym" in readme  # kicad-lib is archived, libs is cut
    assert f"{ORG}/libs/tree/main/kicad." in readme


def test_fixtures_and_pre_commit_pins_stay(cut, capsys):
    ws, top = cut
    pinned = {p: (top / p).read_bytes() for p in ("host/tests/fixtures/pinned.md", "host/tests/test_audit.py")}
    assert run(capsys, "apply", ws, top, "--structure-only")[0] == 0
    assert {p: (top / p).read_bytes() for p in pinned} == pinned  # tests and their fixtures are inputs
    cfg = (top / "host/.pre-commit-config.yaml").read_text()
    assert f"  - repo: {ORG}/toolkit-hooks\n" in cfg  # the pin moves with its rev, after the unfreeze
    assert f"# docs: {ORG}/tools/blob/main/hooks/README.md" in cfg  # other lines of the file still follow
    cargo = (top / "host/Cargo.toml").read_text()
    assert f'repository = "{ORG}/tools"' in cargo and f'homepage = "{ORG}/tools"' in cargo  # never /tree/main/


def test_generated_readmes_are_rendered_from_their_rewritten_source(cut, capsys):
    ws, top = cut
    code, out, _ = run(capsys, "plan", ws, top, "--structure-only", "--json")
    doc = json.loads(out)
    assert doc["generated"] == [{"path": "host/docs/requirements/README.md",
                                 "source": "host/docs/requirements/requirements.toml"}]
    assert not any(e["path"] == "host/docs/requirements/README.md" for e in doc["edits"])
    assert run(capsys, "apply", ws, top, "--structure-only")[0] == 0
    toml = top / "host/docs/requirements/requirements.toml"
    assert f"{ORG}/tools/blob/main/hooks/README.md" in toml.read_text()
    assert requirements.is_current(toml, top / "host/docs/requirements/README.md")
    code, out, _ = run(capsys, "check", ws, top, "--structure-only", "--json")
    doc = json.loads(out)
    assert code == 0, [r for r in doc["results"] if r["verdict"] == "fail"]


def test_check_fails_on_a_stale_generated_file(cut, capsys):
    ws, top = cut
    assert run(capsys, "apply", ws, top, "--structure-only")[0] == 0
    (top / "host/docs/requirements/README.md").write_text("# edited by hand\n")
    git(top, "commit", "-q", "-am", "hand edit")
    code, out, _ = run(capsys, "check", ws, top, "--structure-only", "--json")
    fails = [r["id"] for r in json.loads(out)["results"] if r["verdict"] == "fail"]
    assert code == 1 and fails == ["rename.generated:host/docs/requirements/README.md"]


def test_without_structure_only_the_map_applies(cut, capsys):
    ws, top = cut
    code, out, _ = run(capsys, "plan", ws, top, "--json")
    doc = json.loads(out)
    assert code == 0 and doc["structure_only"] is False
    assert [(m["from"], m["to"]) for m in doc["moves"]] == [("host/src/plan.py", "workspace/plan.py")]
    assert [d["path"] for d in doc["deletes"]] == ["host/docs/old.md"]


def test_cargo_git_pins_stay_and_issue_trackers_follow_the_root(cut, capsys):
    ws, top = cut
    (top / "hooks/Cargo.toml").write_text(f'[dependencies]\nh = {{ git = "{ORG}/toolkit-host", rev = "abc1234" }}\n')
    (top / "hooks/Cargo.lock").write_text(f'[[package]]\nname = "h"\nsource = "git+{ORG}/toolkit-host?rev=abc1234'
                                          '#abc1234"\n')
    (top / "hooks/os.env").write_text(f"HOME_URL={ORG}/toolkit-hooks\nBUG_URL={ORG}/toolkit-hooks/issues\n"
                                      f"ONE={ORG}/toolkit-hooks/issues/7\n")
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", "pins")
    pins = {p: (top / p).read_text() for p in ("hooks/Cargo.toml", "hooks/Cargo.lock")}
    assert run(capsys, "apply", ws, top, "--structure-only")[0] == 0
    assert {p: (top / p).read_text() for p in pins} == pins  # a git pin moves with its rev, after the cut
    env = (top / "hooks/os.env").read_text()
    assert f"BUG_URL={ORG}/tools/issues\n" in env  # an archived repository takes no issues
    assert f"ONE={ORG}/toolkit-hooks/issues/7\n" in env  # one issue stays where it was filed


def test_urls_into_a_root_cut_as_structure_only_follow_its_checkout(cut, capsys):
    ws, top = cut
    libs = ws / "public/libs"
    (libs / "kicad/symbols").mkdir(parents=True)
    (libs / "kicad/symbols/Murata.kicad_sym").write_text("(kicad_symbol_lib)\n")
    git(libs, "init", "-q", "-b", "main")
    git(libs, "add", "-A")
    git(libs, "-c", "user.email=t@example.org", "-c", "user.name=T", "commit", "-q", "-m", "libs")
    assert run(capsys, "apply", ws, top, "--structure-only")[0] == 0
    readme = (top / "host/README.md").read_text()
    assert f"{ORG}/libs/blob/main/kicad/symbols/Murata.kicad_sym" in readme  # its map row has not landed


def test_urls_into_an_earlier_root_follow_the_landing_its_merge_recorded(cut, capsys):
    ws, top = cut
    libs = ws / "public/libs"
    (libs / "lib/symbols").mkdir(parents=True)
    (libs / "lib/symbols/Murata.kicad_sym").write_text("(kicad_symbol_lib)\n")
    git(libs, "init", "-q", "-b", "main")
    git(libs, "add", "-A")
    git(libs, "-c", "user.email=t@example.org", "-c", "user.name=T", "commit", "-q", "-m",
        "libs: join kicad-lib with their history\n\n- kicad-lib → lib/: 3 commits, head abc\n\n"
        "Source-Head: kicad-lib abc\n")
    assert run(capsys, "apply", ws, top, "--structure-only")[0] == 0
    readme = (top / "host/README.md").read_text()
    assert f"{ORG}/libs/blob/main/lib/symbols/Murata.kicad_sym" in readme  # not the map's kicad/
