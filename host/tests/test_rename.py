"""odw rename: one root's cut from the rename map, on synthetic workspaces only (never the real one)."""
import hashlib
import json
import pathlib
import subprocess

import pytest

from odw import audit, rename, rename_map, schemas

ORG = "https://github.com/opendap-project"
MAP = [
    ("repo:opendap-project/kicad-lib", "repo:opendap-project/libs (kicad/)", "repo", "merge", ""),
    ("repo:opendap-project/parts-lib", "repo:opendap-project/libs (parts/)", "repo", "merge", ""),
    ("repo:opendap-project/hat-standard", "repo:opendap-project/hats (standard/)", "repo", "merge", ""),
    ("repo:opendap-project/toolkit-hardware", "repo:opendap-project/tools electronics/ parts/", "repo", "merge", ""),
    ("public/libs/kicad/README.md", "libs/kicad/README.md", "doc", "keep", ""),
    ("public/libs/kicad/symbols/ECS.kicad_sym", "libs/kicad/symbols/ecs.kicad_sym", "library", "rename", "case only"),
    ("public/libs/kicad/footprints/Murata.pretty/**", "libs/kicad/footprints/murata.pretty/**", "library", "rename", ""),
    ("public/libs/kicad/docs/guide.md", "libs/kicad/docs/guides/guide.md", "doc", "move", ""),
    ("public/libs/kicad/3dmodels/.gitkeep", "", "file", "delete", "placeholder"),
    ("public/libs/parts/table.md", "libs/parts/table.toml;libs/parts/table.md", "data", "convert", ""),
    ("public/libs/kicad/check.py", "tools/electronics/check.py", "tool", "move", ""),
    ("public/libs/kicad/gone.md", "libs/kicad/gone2.md", "doc", "move", "stale row"),
    ("env:ODAP_KICAD_LIB_DIR", "env:ODAP_LIBS_DIR", "env", "rename", "ODAP_<ROOT>_DIR"),
    ("mfr:Murata", "mfr:murata", "identifier", "rename", "kicad-lib library key"),
    ("crate:odap-composer", "crate:blackbird-composer", "crate", "rename", ""),
    ("env:ODAP_X;ODAP_Y", "env:ODAP_A", "env", "rename", ""),
]
KICAD = {
    "README.md": (f"# KiCad\n\nSee [the guide](docs/guide.md#use) and [symbols](symbols/ECS.kicad_sym).\n"
                  f"Parts: {ORG}/parts-lib and [table]({ORG}/parts-lib/blob/main/table.md).\n"
                  f"Pinned: {ORG}/kicad-lib/blob/v1.0/docs/guide.md\n"
                  f"HATs: {ORG}/hat-standard/blob/main/README.md\n"
                  f"Issues: {ORG}/kicad-lib/issues/3\n"
                  "Set ODAP_KICAD_LIB_DIR; Murata parts; odap-composer crate.\n"
                  "```\n[example](docs/guide.md)\n```\n"),
    "docs/guide.md": "# Guide\n\nBack to the [README](../README.md); [footprints](../footprints/Murata.pretty/).\n",
    "symbols/ECS.kicad_sym": "(kicad_symbol_lib)\n",
    "footprints/Murata.pretty/L_1.kicad_mod": "(footprint L_1)\n",
    "footprints/Murata.pretty/L_2.kicad_mod": "(footprint L_2)\n",
    "3dmodels/.gitkeep": "",
    "check.py": "print('check')\n",
}
PARTS = {"table.md": "# Table\n\nKiCad: [README](https://github.com/opendap-project/kicad-lib/blob/main/README.md)\n"}


def git(cwd, *args):
    """git in a fixture repository, never in the repository whose hook runs these tests (no GIT_* variables)."""
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env=audit.git_env()).stdout


def make_repo(path, files, message="init"):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.org")
    git(path, "config", "user.name", "T")
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    (w / "repos.tsv").write_text("path\trepository\npublic/libs/kicad\topendap-project/kicad-lib\n"
                                 "public/libs/parts\topendap-project/parts-lib\n"
                                 "public/hats/standard\topendap-project/hat-standard\n"
                                 "public/toolkit/hardware\topendap-project/toolkit-hardware\n")
    make_repo(w / "public/libs/kicad", KICAD)
    make_repo(w / "public/libs/parts", PARTS)
    make_repo(w / "public/hats/standard", {"README.md": "# Standard\n"})
    lines = ["old\tnew\tkind\taction\tnotes\tscope"] + ["\t".join([*r, "test"]) for r in MAP]
    (tmp_path / "map.tsv").write_text("\n".join(lines) + "\n")
    return w


@pytest.fixture
def merged(ws, tmp_path):
    """The libs root after the history merge (RFC 0017 §7 step 2): each library in its folder, a bare remote."""
    root = tmp_path / "libs"
    make_repo(root, {**{f"kicad/{k}": v for k, v in KICAD.items()}, **{f"parts/{k}": v for k, v in PARTS.items()}})
    (root / "kicad/symbols/ECS.kicad_sym").write_text("(kicad_symbol_lib (version 2))\n")
    git(root, "commit", "-q", "-am", "second commit")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, env=audit.git_env())
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", "main")
    return root


def args(ws, *extra):
    return ["--workspace", str(ws), "--map", str(ws.parent / "map.tsv"), *extra]


def digest(path):
    h = hashlib.sha256()
    for p in sorted(pathlib.Path(path).rglob("*")):
        if p.is_file():
            h.update(str(p).encode() + p.read_bytes())
    return h.hexdigest()


def run(capsys, *argv):
    code = rename.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_landings_come_from_repository_rows(ws):
    m = rename_map.load(ws, ws.parent / "map.tsv")
    assert m.sources["kicad-lib"].root == "libs" and m.sources["kicad-lib"].landing == "kicad"
    assert m.sources["hat-standard"].landing == "standard"
    assert m.sources["toolkit-hardware"].landing is None  # several folders: the map does not say which
    assert m.in_root("public/libs/kicad/docs/guide.md") == ("libs", "kicad/docs/guide.md")


def test_identifier_rows_are_mechanical_only_when_safe(ws):
    m = rename_map.load(ws, ws.parent / "map.tsv")
    got = {i.row.old: (i.owner, i.reason) for i in map(lambda r: rename_map.identifier(m, r),
                                                          rename_map.identifier_rows(m))}
    assert got["env:ODAP_KICAD_LIB_DIR"] == ("libs", None)
    assert got["mfr:Murata"] == ("libs", "plain-word")  # owner from the notes; a plain word needs a hand
    assert got["crate:odap-composer"] == ("blackbird", None)
    assert got["env:ODAP_X;ODAP_Y"][1] == "count-mismatch"


ROOTED = [  # (old, new, kind, notes, root): the root column names the owner the tool cannot infer
    ("command:odapkit", "command:odw", "command", "the command", "libs"),
    ("command:odapkit craft check", "command:odw craft check", "command", "", "tools"),
    ("skill:/opendap:requirement", "skill:/opendap:docs-requirement", "skill", "", "libs"),
    ("mfr:Murata", "mfr:murata", "identifier", "kicad-lib library key", ""),
]


def rooted_map(ws):
    """The test map plus rows that carry a root column (the column is optional: other rows leave it empty)."""
    lines = ["old\tnew\tkind\taction\tnotes\tscope\troot"]
    lines += ["\t".join([*r, "test", ""]) for r in MAP if r[0] != "mfr:Murata"]
    lines += ["\t".join([o, n, k, "rename", notes, "test", root]) for o, n, k, notes, root in ROOTED]
    (ws.parent / "map.tsv").write_text("\n".join(lines) + "\n")
    readme = ws / "public/libs/kicad/README.md"
    readme.write_text(readme.read_text() + "Run odapkit or odapkit-docs; odapkit craft check; "
                      "/opendap:requirement for a requirement.\n")
    git(ws / "public/libs/kicad", "commit", "-q", "-am", "rooted")


def test_root_column_names_the_owner_and_clears_a_plain_word(ws):
    rooted_map(ws)
    m = rename_map.load(ws, ws.parent / "map.tsv")
    got = {i.row.old: (i.owner, i.reason) for i in map(lambda r: rename_map.identifier(m, r),
                                                          rename_map.identifier_rows(m))}
    assert got["command:odapkit"] == ("libs", None)  # a plain word whose root a maintainer set
    assert got["command:odapkit craft check"] == ("tools", None)  # owner-unknown without the column
    assert got["skill:/opendap:requirement"] == ("libs", None)  # written as it appears, so a token
    assert got["mfr:Murata"] == ("libs", "plain-word")  # no root: the word also appears in prose


def test_rooted_plain_word_is_rewritten_as_a_whole_token_only(ws, capsys):
    rooted_map(ws)
    code, out, _ = run(capsys, "plan", "libs", *args(ws), "--json")
    doc = json.loads(out)
    status = {i["old"]: i["status"] for i in doc["identifiers"]}
    assert status["command:odapkit"] == "apply" and status["skill:/opendap:requirement"] == "apply"
    assert status["command:odapkit craft check"] == "later" and status["mfr:Murata"] == "manual"
    pairs = {(e["old"], e["new"]) for e in doc["edits"] if e["kind"] == "identifier"}
    assert ("odapkit", "odw") in pairs and ("/opendap:requirement", "/opendap:docs-requirement") in pairs
    assert not any(o in {"odapkit-docs", "requirement", "Murata"} for o, _ in pairs)


def test_plan_on_the_sources_lists_every_move_and_rewrite(ws, capsys):
    before = digest(ws)
    code, out, _ = run(capsys, "plan", "libs", *args(ws), "--json")
    assert code == 0 and digest(ws) == before
    doc = json.loads(out)
    assert not schemas.errors(doc, "rename-plan")
    assert doc["view"] == "sources" and doc["cut"] == ["libs"]
    moves = {(m["from"], m["to"]) for m in doc["moves"]}
    assert moves == {("kicad/symbols/ECS.kicad_sym", "kicad/symbols/ecs.kicad_sym"),
                     ("kicad/footprints/Murata.pretty/L_1.kicad_mod", "kicad/footprints/murata.pretty/L_1.kicad_mod"),
                     ("kicad/footprints/Murata.pretty/L_2.kicad_mod", "kicad/footprints/murata.pretty/L_2.kicad_mod"),
                     ("kicad/docs/guide.md", "kicad/docs/guides/guide.md")}
    assert [d["path"] for d in doc["deletes"]] == ["kicad/3dmodels/.gitkeep"]
    assert [(p["action"], p["reason"]) for p in doc["pending"]] == [("convert", "action-convert")]
    assert [(c["direction"], c["to"]) for c in doc["cross_root"]] == [("out", "tools/electronics/check.py")]
    assert [x["from"] for x in doc["missing"]] == ["kicad/gone.md"]
    ids = {i["old"]: i["status"] for i in doc["identifiers"]}
    assert ids == {"env:ODAP_KICAD_LIB_DIR": "apply", "mfr:Murata": "manual", "crate:odap-composer": "later"}
    edits = {(e["path"], e["kind"], e["old"], e["new"]) for e in doc["edits"]}
    assert ("kicad/README.md", "link", "docs/guide.md#use", "docs/guides/guide.md#use") in edits
    assert ("kicad/README.md", "link", "symbols/ECS.kicad_sym", "symbols/ecs.kicad_sym") in edits
    assert ("kicad/README.md", "url", f"{ORG}/parts-lib", f"{ORG}/libs/tree/main/parts") in edits
    assert ("kicad/README.md", "link", f"{ORG}/libs/blob/main/parts/table.md", "../parts/table.md") in edits
    assert ("kicad/README.md", "identifier", "ODAP_KICAD_LIB_DIR", "ODAP_LIBS_DIR") in edits
    assert ("kicad/docs/guides/guide.md", "link", "../README.md", "../../README.md") in edits
    assert ("kicad/docs/guides/guide.md", "link", "../footprints/Murata.pretty/", "../../footprints/murata.pretty/") in edits
    assert ("parts/table.md", "link", f"{ORG}/libs/blob/main/kicad/README.md", "../kicad/README.md") in edits
    olds = {e["old"] for e in doc["edits"]}
    assert not any("hat-standard" in o or "v1.0" in o or "issues" in o for o in olds)  # other roots, pins, issues stay
    assert "odap-composer" not in olds and "Murata" not in olds


def test_text_plan_is_readable(ws, capsys):
    code, out, _ = run(capsys, "plan", "libs", *args(ws), "--text")
    assert code == 0
    assert "move kicad/docs/guide.md -> kicad/docs/guides/guide.md" in out
    assert "delete kicad/3dmodels/.gitkeep" in out


def test_apply_moves_with_history_and_rewrites(ws, merged, capsys):
    sources, remote = digest(ws), git(merged, "ls-remote", "origin")
    code, out, err = run(capsys, "apply", "libs", *args(ws, "--root-dir", str(merged)), "--json")
    assert code == 0, err
    doc = json.loads(out)
    assert not schemas.errors(doc, "rename-plan") and len(doc["commits"]) == 2
    files = set(git(merged, "ls-files").split())
    assert "kicad/symbols/ecs.kicad_sym" in files and "kicad/symbols/ECS.kicad_sym" not in files
    assert "kicad/footprints/murata.pretty/L_1.kicad_mod" in files and "kicad/3dmodels/.gitkeep" not in files
    assert "kicad/check.py" in files  # leaves at the tools cut, not now
    log = git(merged, "log", "--follow", "--format=%s", "--", "kicad/symbols/ecs.kicad_sym").splitlines()
    assert "second commit" in log and "init" in log
    readme = (merged / "kicad/README.md").read_text()
    assert "(docs/guides/guide.md#use)" in readme and "ODAP_LIBS_DIR" in readme
    assert f"{ORG}/libs/tree/main/parts" in readme and "[table](../parts/table.md)" in readme
    assert f"{ORG}/kicad-lib/blob/v1.0/docs/guide.md" in readme and f"{ORG}/hat-standard/blob/main" in readme
    assert "[example](docs/guide.md)" in readme  # fenced example untouched
    assert "../../footprints/murata.pretty/" in (merged / "kicad/docs/guides/guide.md").read_text()
    assert digest(ws) == sources and git(merged, "ls-remote", "origin") == remote  # nothing else touched, no push
    assert not git(merged, "status", "--porcelain")


def test_check_lists_old_names_until_applied(ws, merged, capsys):
    code, out, _ = run(capsys, "check", "libs", *args(ws, "--root-dir", str(merged)), "--json")
    doc = json.loads(out)
    assert code == 1 and not schemas.errors(doc, "check-result")
    fails = {r["id"].split(":")[0] for r in doc["results"] if r["verdict"] == "fail"}
    assert {"rename.old-path", "rename.old-reference"} <= fails
    run(capsys, "apply", "libs", *args(ws, "--root-dir", str(merged)))
    code, out, _ = run(capsys, "check", "libs", *args(ws, "--root-dir", str(merged)), "--json")
    doc = json.loads(out)
    assert code == 0, [r for r in doc["results"] if r["verdict"] == "fail"]
    warns = {r["id"] for r in doc["results"] if r["verdict"] == "warn"}
    assert "rename.manual-identifier:mfr:Murata" in warns


def test_plan_after_apply_is_empty(ws, merged, capsys):
    run(capsys, "apply", "libs", *args(ws, "--root-dir", str(merged)))
    code, out, _ = run(capsys, "plan", "libs", *args(ws, "--root-dir", str(merged)), "--json")
    doc = json.loads(out)
    assert code == 0 and doc["view"] == "root" and not doc["moves"] and not doc["deletes"] and not doc["edits"]
    assert len(doc["done"]) == 3


@pytest.mark.parametrize("dirty", ["modified", "untracked"])
def test_apply_refuses_a_dirty_tree(ws, merged, capsys, dirty):
    if dirty == "modified":
        (merged / "kicad/README.md").write_text("someone else's edit\n")
    else:
        (merged / "notes.txt").write_text("someone else's file\n")
    head, before = git(merged, "rev-parse", "HEAD"), digest(merged)
    code, out, err = run(capsys, "apply", "libs", *args(ws, "--root-dir", str(merged)), "--json")
    assert code == 1 and json.loads(out)["error"]["code"] == "rename.dirty"
    assert "hint:" in err and "fix:" in err
    assert git(merged, "rev-parse", "HEAD") == head and digest(merged) == before


def test_apply_needs_the_merged_root(ws, capsys):
    before = digest(ws)
    code, out, _ = run(capsys, "apply", "libs", *args(ws), "--json")
    assert code == 1 and json.loads(out)["error"]["code"] == "rename.no-root"
    assert digest(ws) == before


def test_apply_dry_run_is_the_plan(ws, merged, capsys):
    before = digest(merged)
    code, out, _ = run(capsys, "apply", "libs", *args(ws, "--root-dir", str(merged)), "--dry-run", "--json")
    assert code == 0 and json.loads(out)["mode"] == "plan" and digest(merged) == before


def test_usage_errors_exit_2(ws, capsys, monkeypatch):
    monkeypatch.delenv("ODAP_RENAME_MAP", raising=False)
    code, out, _ = run(capsys, "plan", "nowhere", *args(ws), "--json")
    assert code == 2 and json.loads(out)["error"]["code"] == "rename.unknown-root"
    code, out, _ = run(capsys, "plan", "libs", "--workspace", str(ws), "--json")
    assert code == 2 and json.loads(out)["error"]["code"] == "rename.no-map"
    assert run(capsys, "plan", "libs", "--bogus")[0] == 2
    assert run(capsys, "--help")[0] == 0


def test_the_engine_never_pushes():
    src = pathlib.Path(rename.__file__).read_text() + pathlib.Path(rename_map.__file__).read_text()
    assert '"push"' not in src and "'push'" not in src


def test_python_files_stay_within_budget():
    for mod in ("rename", "rename_map", "rename_rewrite"):
        path = pathlib.Path(rename.__file__).with_name(f"{mod}.py")
        assert path.read_text().count("\n") <= 500, mod
