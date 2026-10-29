"""odw rename after a structure-only cut (HL-71 phase 0): a source that left repos.tsv at its root's cut is found again
through the manifest's history (its old workspace path) and the root's merge commit (its landing), so the map's rows
for it plan as moves inside the root."""
import json

from test_rename import git, make_repo, run

from odw import rename_map

MERGE = ("tools: join toolkit-a, toolkit-b with their history\n\n- toolkit-a → a/: 3 commits, head 1111111\n"
         "- toolkit-b → b/: 2 commits, head 2222222\n\nSource-Head: toolkit-a 1111111\nSource-Head: toolkit-b 2222222")
MAP = [("repo:opendap-project/toolkit-a", "repo:opendap-project/tools env/", "repo", "merge", ""),  # says env/, landed a/
       ("repo:opendap-project/toolkit-b", "repo:opendap-project/tools core/", "repo", "merge", ""),
       ("public/toolkit/a/x.py", "tools/core/x.py", "engine", "move", ""),
       ("public/toolkit/b/docs/y.md", "tools/documentation/docs/y.md", "doc", "move", "")]


def cut_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "repos.tsv").write_text("path\trepository\npublic/toolkit/a\topendap-project/toolkit-a\n"
                                  "public/toolkit/b\topendap-project/toolkit-b\n")
    git(ws, "init", "-q", "-b", "main")
    git(ws, "add", "repos.tsv")
    git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "two toolkit repositories")
    (ws / "repos.tsv").write_text("path\trepository\npublic/tools\topendap-project/tools\n")
    git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "tools is one repository (the cut)")
    make_repo(ws / "public/tools", {"a/x.py": "print('x')\n", "b/docs/y.md": "# Y\n"}, MERGE)
    lines = ["old\tnew\tkind\taction\tnotes\tscope"] + ["\t".join([*r, "test"]) for r in MAP]
    (tmp_path / "map.tsv").write_text("\n".join(lines) + "\n")
    return ws


def test_a_source_that_left_the_manifest_is_found_through_its_history(tmp_path):
    ws = cut_workspace(tmp_path)
    m = rename_map.load(ws, tmp_path / "map.tsv")
    a = m.sources["toolkit-a"]
    assert (a.path, a.root, a.landing) == ("public/toolkit/a", "tools", "a")  # the merge commit, not the row's env/
    assert m.in_root("public/toolkit/b/docs/y.md") == ("tools", "b/docs/y.md")


def test_the_rows_of_a_root_cut_as_structure_only_plan_as_moves(tmp_path, capsys):
    ws = cut_workspace(tmp_path)
    code, out, err = run(capsys, "plan", "tools", "--workspace", str(ws), "--map", str(tmp_path / "map.tsv"),
                         "--root-dir", str(ws / "public/tools"), "--json")
    assert code == 0, err
    moves = {mv["from"]: mv["to"] for mv in json.loads(out)["moves"]}
    assert moves == {"a/x.py": "core/x.py", "b/docs/y.md": "documentation/docs/y.md"}


def test_the_plan_lists_the_tracked_files_no_row_accounts_for(tmp_path, capsys):
    ws = cut_workspace(tmp_path)
    tools = ws / "public/tools"
    for rel in ("a/z.py", "README.md", "b/docs/old.md"):
        (tools / rel).write_text("x\n")
    git(tools, "add", ".")
    git(tools, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "three more files")
    rows = [("public/tools/README.md", "tools/README.md", "doc", "keep", ""),  # a root file that stays
            ("public/toolkit/b/docs/old.md", "", "doc", "delete", "")]
    with (tmp_path / "map.tsv").open("a") as f:
        f.writelines("\t".join([*r, "test"]) + "\n" for r in rows)
    code, out, err = run(capsys, "plan", "tools", "--workspace", str(ws), "--map", str(tmp_path / "map.tsv"),
                         "--root-dir", str(tools), "--json")
    assert code == 0, err
    doc = json.loads(out)
    assert doc["uncovered"] == ["a/z.py"]
    assert doc["summary"]["uncovered"] == 1
