"""odw rename links: the workspace-wide link check after the cuts, on synthetic workspaces only (never the real one)."""
import json
import subprocess

import pytest

from odw import audit, rename, rename_rewrite as rw, schemas

ORG = "https://github.com/opendap-project"
MAP = [
    ("repo:opendap-project/kicad-lib", "repo:opendap-project/libs (kicad/)", "repo", "merge", ""),
    ("repo:opendap-project/parts-lib", "repo:opendap-project/libs (parts/)", "repo", "merge", ""),
    ("repo:opendap-project/t1", "repo:opendap-project/t1 (folder product/)", "repo", "merge", ""),
    ("repo:opendap-project/board", "repo:opendap-project/t1 (folder board/)", "repo", "merge", ""),
    ("repo:opendap-project/notebook", "repo:opendap-project/notebook", "repo", "keep", ""),
]
LIBS = {"kicad/README.md": "# KiCad\n", "kicad/symbols/murata.kicad_sym": "(kicad_symbol_lib)\n",
        "parts/README.md": "# Parts\n"}
T1 = {"product/README.md": "# T1\n", "board/docs/specs/pinout.md": "# Pinout\n"}
NOTES = {
    "README.md": (f"Library: [readme]({ORG}/kicad-lib/blob/main/README.md).\n"
                  f"Pinned: {ORG}/kicad-lib/blob/v1.0/README.md and {ORG}/parts-lib/blob/0123abc/Murata/X.md\n"
                  f"Board: [pinout]({ORG}/board/blob/main/docs/specs/pinout.md); product: {ORG}/t1\n"
                  f"Gone: [sheet]({ORG}/parts-lib/blob/main/Murata/X.md)\n"
                  f"Broken: [renders]({ORG}/t1/blob/main/product/renders/web/none.html)\n"
                  f"Elsewhere: {ORG}/website/blob/main/missing.md\n"),
    "plans/old-plan.md": f"Plan: {ORG}/kicad-lib/blob/main/README.md\n",
    "x/journal/2026-10-27.md": f"Log: {ORG}/kicad-lib/blob/main/README.md\n",
    "tests/fixture.md": f"Input: {ORG}/kicad-lib/blob/main/README.md\n",
    "notes/kept.md": f"Kept: {ORG}/kicad-lib/blob/main/README.md\n",
    "sheets/murata/X.md": "# X (moved here from parts-lib)\n",
    "history/sheets/X.md": "# X, a frozen copy: not a namesake\n",
    "plans/electronics-plan.md": "# A plan is a target like any other file\n",
    "notes/plan-link.md": f"[plan]({ORG}/t1/blob/main/notes/plans/electronics-plan.md)\n",
    ".pre-commit-config.yaml": f"repos:\n  - repo: {ORG}/kicad-lib\n    rev: v0.1.0\n",
}


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
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "repos.tsv").write_text("path\trepository\npublic/libs\topendap-project/libs\n"
                                  "public/t1\topendap-project/t1\n")
    make_repo(ws / "public/libs", LIBS)
    make_repo(ws / "public/t1", T1, "t1: join board, t1 with their history\n\n- board → board/: 1 commits, head b\n"
              "- t1 → product/: 1 commits, head a\n\nSource-Head: board b\nSource-Head: t1 a")  # as the merge records
    overlay = "path\trepository\nprivate/notebook\topendap-project/notebook\n"
    make_repo(ws / "private/overlay", {"repos.tsv": overlay})
    make_repo(ws / "private/notebook", NOTES)
    rows = ["old\tnew\tkind\taction\tnotes"] + ["\t".join(r) for r in MAP]
    (tmp_path / "map.tsv").write_text("\n".join(rows) + "\n")
    return ws


def run(capsys, ws, *extra):
    code = rename.main(["links", "--workspace", str(ws), "--map", str(ws.parent / "map.tsv"), "--json", *extra])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def fails(doc, kind):
    return sorted((r["location"]["path"], r["location"]["line"]) for r in doc["results"]
                  if r["verdict"] == "fail" and r["id"].startswith(f"rename.{kind}:"))


def test_check_reports_old_and_broken_urls_in_live_files(ws, capsys):
    code, doc, err = run(capsys, ws)
    assert code == 1, err
    assert not schemas.errors(doc, "check-result")
    assert fails(doc, "old-url") == [("private/notebook/README.md", 1), ("private/notebook/README.md", 3),
                                     ("private/notebook/README.md", 4), ("private/notebook/notes/kept.md", 1),
                                     ("private/notebook/notes/plan-link.md", 1)]
    assert fails(doc, "broken-url") == [("private/notebook/README.md", 5)]


def test_history_tests_and_pinned_refs_are_never_reported(ws, capsys):
    _, doc, _ = run(capsys, ws, "--keep", "notes/**")
    where = {r["location"]["path"] for r in doc["results"] if "location" in r}
    assert where == {"private/notebook/README.md"}  # plans/, journal/, tests/ and the --keep glob stay
    lines = {r["location"]["line"] for r in doc["results"] if "location" in r}
    assert 2 not in lines  # tags and commits of an archived repository keep serving


def test_apply_rewrites_what_lands_and_leaves_the_rest(ws, capsys):
    code, doc, err = run(capsys, ws, "--apply")
    assert code == 1, err  # the sheet gone from libs and the broken render are left for a hand
    text = (ws / "private/notebook/README.md").read_text()
    assert f"[readme]({ORG}/libs/blob/main/kicad/README.md)" in text
    assert f"[pinout]({ORG}/t1/blob/main/board/docs/specs/pinout.md)" in text
    assert f"product: {ORG}/t1\n" in text  # a bare URL of a root that reuses the source's name is the root
    assert f"[sheet]({ORG}/parts-lib/blob/main/Murata/X.md)" in text  # its rewrite would point to nothing
    assert f"{ORG}/kicad-lib/blob/v1.0/README.md" in text
    assert (ws / "private/notebook/plans/old-plan.md").read_text() == NOTES["plans/old-plan.md"]
    old = [r for r in doc["results"] if r["id"].startswith("rename.old-url:private/notebook/README.md")]
    assert [r["location"]["line"] for r in old] == [4]
    assert "no such path" in old[0]["message"]
    assert git(ws / "private/notebook", "status", "--porcelain").split() == ["M", "README.md", "M", "notes/kept.md"]
    code, doc, _ = run(capsys, ws)
    assert fails(doc, "old-url") == [("private/notebook/README.md", 4),
                                     ("private/notebook/notes/plan-link.md", 1)]


def test_clean_workspace_passes(ws, capsys):
    (ws / "private/notebook/README.md").write_text(f"[readme]({ORG}/libs/blob/main/kicad/README.md)\n")
    (ws / "private/notebook/notes/kept.md").write_text("Nothing old.\n")
    (ws / "private/notebook/notes/plan-link.md").write_text("Nothing old.\n")
    code, doc, err = run(capsys, ws)
    assert code == 0, err
    assert [r for r in doc["results"] if r["verdict"] == "fail"] == []
    assert doc["results"][-1]["id"] == "rename.links" and doc["results"][-1]["verdict"] == "pass"


def test_find_in_points_a_dead_url_of_a_private_file_to_its_only_namesake(ws, capsys):
    code, doc, _ = run(capsys, ws, "--find-in", "notebook")
    gone = [r for r in doc["results"] if r["id"] == "rename.old-url:private/notebook/README.md:4"]
    assert f"{ORG}/notebook/blob/main/sheets/murata/X.md (found in notebook)" in gone[0]["message"]
    run(capsys, ws, "--find-in", "notebook", "--apply")
    text = (ws / "private/notebook/README.md").read_text()
    assert f"[sheet]({ORG}/notebook/blob/main/sheets/murata/X.md)" in text
    assert f"[renders]({ORG}/t1/blob/main/product/renders/web/none.html)" in text  # no namesake: left
    assert (ws / "private/notebook/notes/plan-link.md").read_text() == \
        f"[plan]({ORG}/notebook/blob/main/plans/electronics-plan.md)\n"


def test_find_in_never_gives_a_public_file_a_private_link(ws, capsys):
    (ws / "public/libs/kicad/README.md").write_text(f"# KiCad\n\nSheet: {ORG}/parts-lib/blob/main/Murata/X.md\n")
    git(ws / "public/libs", "commit", "-qam", "sheet")
    run(capsys, ws, "--find-in", "notebook", "--apply")
    assert "notebook" not in (ws / "public/libs/kicad/README.md").read_text()


def test_pre_commit_pins_stay(ws, capsys):
    _, doc, _ = run(capsys, ws)
    assert not any(r["location"]["path"].endswith(".pre-commit-config.yaml") for r in doc["results"] if "location" in r)


def test_no_map_is_a_usage_error(ws, capsys):
    code = rename.main(["links", "--workspace", str(ws), "--map", str(ws / "none.tsv"), "--json"])
    out, _ = capsys.readouterr()
    assert code == 2 and json.loads(out)["error"]["code"] == "rename.no-map"


def test_bare_url_of_a_root_named_like_its_source_stays():
    src = rw.Paths({})
    sources = {"t1": type("S", (), {"name": "t1", "root": "t1", "landing": "product"})()}
    text, edits = rw.urls(f"see {ORG}/t1 and {ORG}/t1/blob/main/README.md\n", sources, None, lambda r: src)
    assert text == f"see {ORG}/t1 and {ORG}/t1/blob/main/product/README.md\n" and len(edits) == 1



def test_an_overlay_is_any_repos_tsv_two_levels_below_the_workspace(ws, capsys):
    """No overlay is named in the code: any cloned repository two folders down with its own repos.tsv lists more."""
    (ws / "private/overlay").rename(ws / "private/lists")
    _, doc, _ = run(capsys, ws)
    assert any(r["location"]["path"].startswith("private/notebook/") for r in doc["results"] if "location" in r)
