"""odw rename merge: a root's source repositories joined with history (RFC 0017 §7 step 2), on synthetic
repositories only (never the real workspace)."""
import hashlib
import io
import json
import pathlib
import shutil
import subprocess

import pytest

from odw import audit, rename, rename_merge, rename_merge_git, schemas

MAP = [
    ("repo:opendap-project/kicad-lib", "repo:opendap-project/libs (kicad/)", "repo", "merge", ""),
    ("repo:opendap-project/parts-lib", "repo:opendap-project/libs (parts/)", "repo", "merge", ""),
    ("repo:opendap-project/freecad-lib", "repo:opendap-project/libs (freecad/)", "repo", "merge", ""),
    ("repo:opendap-project/hat-standard", "repo:opendap-project/hats (standard/)", "repo", "merge", ""),
    ("repo:opendap-project/toolkit-hardware", "repo:opendap-project/tools electronics/ parts/", "repo", "merge", ""),
    ("public/libs/kicad/docs/guide.md", "libs/kicad/docs/guides/guide.md", "doc", "move", ""),
]
REPOS = {"public/libs/kicad": "kicad-lib", "public/libs/parts": "parts-lib", "public/libs/freecad": "freecad-lib",
         "public/hats/standard": "hat-standard", "public/toolkit/hardware": "toolkit-hardware"}
LFS = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 12\n"
ENGINES = ["fast-export"] + (["filter-repo"] if rename_merge_git.has_filter_repo() else [])


def git(cwd, *args, env=None):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env={**audit.git_env(), **(env or {})}).stdout


def commit(repo, files, message, removed=()):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    for name in removed:
        git(repo, "rm", "-q", "--", name)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)


def make_repo(path, history):
    """A repository with several commits and a bare origin it is pushed to (so `main...origin/main` is even)."""
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.org")
    git(path, "config", "user.name", "T")
    git(path, "config", "lfs.allowincompletepush", "true")  # the fixture's LFS pointers have no objects
    for files, message, *removed in history:
        commit(path, files, message, *removed)
    remote = path.parent / f".{path.name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, env=audit.git_env())
    git(path, "remote", "add", "origin", str(remote))
    git(path, "push", "-q", "-u", "origin", "main")


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "M", "GIT_AUTHOR_EMAIL": "m@example.org", "GIT_COMMITTER_NAME": "M",
                 "GIT_COMMITTER_EMAIL": "m@example.org"}.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    (w / "repos.tsv").write_text("path\trepository\n" + "".join(f"{p}\topendap-project/{n}\n"
                                                              for p, n in REPOS.items()))
    make_repo(w / "public/libs/kicad", [
        ({"README.md": "# KiCad\n", "docs/guide.md": "# Guide\n", ".gitignore": "*.bak\n"}, "kicad: start"),
        ({"docs/guide.md": "# Guide\n\nUse it.\n"}, "kicad: guide grows"),
        ({"docs/usage.md": "# Guide\n\nUse it.\n"}, "kicad: guide renamed", ["docs/guide.md"]),
        ({"symbols/odd name ü.kicad_sym": "(kicad_symbol_lib)\n"}, "kicad: a path that fast-export quotes"),
        ({}, "kicad: an empty commit"),
    ])
    make_repo(w / "public/libs/parts", [
        ({"README.md": "# Parts\n", ".gitignore": "*.bak\n"}, "parts: start"),
        ({"parts/r.toml": "value = 1\n"}, "parts: a part\n\nM 100644 :1 not/a/file/command\n"),
    ])
    make_repo(w / "public/libs/freecad", [({"README.md": "# FreeCAD\n", "models/a.FCStd": LFS,
                                           ".gitattributes": "*.FCStd filter=lfs diff=lfs merge=lfs -text\n"},
                                          "freecad: start")])
    make_repo(w / "public/hats/standard", [({"README.md": "# Standard\n"}, "standard: start")])
    lines = ["old\tnew\tkind\taction\tnotes\tscope"] + ["\t".join([*r, "test"]) for r in MAP]
    (tmp_path / "map.tsv").write_text("\n".join(lines) + "\n")
    return w


def args(ws, *extra):
    return ["--workspace", str(ws), "--map", str(ws.parent / "map.tsv"), *extra]


def run(capsys, *argv):
    code = rename.main(["merge", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def state(repo):
    """Everything git keeps for a clone: refs, config, index and objects (a source must come out untouched).
    Git LFS's own scratch folder is left out: its clean filter writes there when status compares a file."""
    files = {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((repo / ".git").rglob("*")) if p.is_file() and "lfs/tmp" not in p.as_posix()}
    return files, git(repo, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all")


def subjects(repo, rev="HEAD", *paths):
    return git(repo, "log", "--format=%s", rev, "--", *paths).splitlines()


@pytest.mark.parametrize("engine", ENGINES)
def test_merge_joins_every_source_with_history(ws, tmp_path, capsys, engine):
    before = {n: state(ws / p) for p, n in REPOS.items() if (ws / p / ".git").exists()}
    into = tmp_path / "libs"
    code, out, err = run(capsys, "libs", *args(ws, "--into", str(into), "--engine", engine, "--json"))
    assert code == 0, err
    doc = json.loads(out)
    assert schemas.errors(doc, "rename-merge") == []
    assert doc["mode"] == "merge" and doc["engine"] == engine
    assert [s["name"] for s in doc["sources"]] == ["freecad-lib", "kicad-lib", "parts-lib"]
    by = {s["name"]: s for s in doc["sources"]}
    for p, n in REPOS.items():
        if n in by:
            assert by[n]["head"] == git(ws / p, "rev-parse", "main").strip()
            assert by[n]["commits"] == by[n]["commits_rewritten"] and by[n]["trees_match"] is True
    assert by["kicad-lib"]["commits"] == 5 and by["freecad-lib"]["lfs_pointers"] == 1
    assert any(w["code"] == "merge.lfs" for w in doc["warnings"])
    # The first commit of the root is the merge, with every source's head as a trailer.
    head = git(into, "rev-parse", "main").strip()
    assert doc["commit"] == head and doc["checks"]["root_commits"] == 5 + 2 + 1 + 1
    assert git(into, "rev-list", "--count", "main").strip() == "9"
    body = git(into, "log", "-1", "--format=%B", "main")
    for s in doc["sources"]:
        assert f"Source-Head: {s['name']} {s['head']}" in body
    assert len(git(into, "log", "-1", "--format=%P", "main").split()) == 3
    # Every source's commits are there, under its folder, and --follow crosses the rename.
    every = [x for p in ("kicad", "parts", "freecad") for x in subjects(ws / "public/libs" / p, "main")]
    assert sorted(subjects(into, "main")[1:]) == sorted(every)
    follow = git(into, "log", "--follow", "--format=%s", "main", "--", "kicad/docs/usage.md").splitlines()
    assert follow == ["kicad: guide renamed", "kicad: guide grows", "kicad: start"]
    assert git(into, "log", "--format=%s", "main", "--", "parts").splitlines()[0] == "parts: a part"
    # A file identical in two sources: --no-merges keeps --follow from crossing to its twin at the merge.
    twin = git(into, "log", "--follow", "--no-merges", "--format=%s", "main", "--", "parts/.gitignore")
    assert twin.splitlines() == ["parts: start"]
    # The files are the sources' files, each under its landing folder, and the work tree is clean.
    files = set(audit.tracked(into))
    assert "kicad/symbols/odd name ü.kicad_sym" in files and "parts/parts/r.toml" in files
    assert "freecad/models/a.FCStd" in files and not any(f.startswith("standard/") for f in files)
    assert git(into, "status", "--porcelain").strip() == ""
    assert git(into, "for-each-ref", "refs/odw-merge").strip() == "" and git(into, "tag").strip() == ""
    # Commit maps and the report are kept in the root's git folder, and the sources are untouched.
    cmap = (into / ".git/odw-merge/kicad-lib.commit-map").read_text().splitlines()
    assert cmap[0].split() == ["old", "new"] and len(cmap) == 6
    assert f"{by['kicad-lib']['head']} {by['kicad-lib']['rewritten']}" in cmap
    assert json.loads((into / ".git/odw-merge/report.json").read_text())["commit"] == head
    after = {n: state(ws / p) for p, n in REPOS.items() if (ws / p / ".git").exists()}
    changed = {n: sorted(k for k in {*before[n][0], *after[n][0]} if before[n][0].get(k) != after[n][0].get(k))
               for n in before}
    assert all(not v for v in changed.values()) and after == before, changed
    assert git(ws / "public/libs/kicad", "remote").split() == ["origin"]


def test_a_missing_work_folder_is_created(ws, tmp_path, capsys):
    work = tmp_path / "cut/work"  # the tools rehearsal passed a folder that did not exist yet: a traceback
    code, out, err = run(capsys, "libs", *args(ws, "--into", str(tmp_path / "libs"), "--work", str(work), "--json"))
    assert code == 0, err
    assert work.is_dir() and not any(work.iterdir())  # the temporary clones inside it are removed


def test_lfs_counts_only_files_that_are_pointers(tmp_path):
    """A file that only mentions the pointer line (the merge tool, its tests) is not an LFS pointer."""
    repo = tmp_path / "r"
    make_repo(repo, [({"model.FCStd": LFS, "tool.py": f'LFS_POINTER = "{LFS.splitlines()[0]}"\n',
                       "test_tool.py": f'TEXT = """\n{LFS}"""\n', "empty.txt": ""}, "start")])
    assert rename_merge_git.lfs_pointers(repo, "HEAD") == 1


def test_both_engines_give_the_same_trees(ws, tmp_path, capsys):
    if len(ENGINES) < 2:
        pytest.skip("git filter-repo is not installed")
    trees = []
    for engine in ENGINES:
        into = tmp_path / engine
        assert run(capsys, "libs", *args(ws, "--into", str(into), "--engine", engine))[0] == 0
        trees.append(git(into, "rev-parse", "main^{tree}").strip())
    assert trees[0] == trees[1]


def test_rename_plan_reads_the_merged_root(ws, tmp_path, capsys):
    into = tmp_path / "libs"
    assert run(capsys, "libs", *args(ws, "--into", str(into)))[0] == 0
    code = rename.main(["plan", "libs", *args(ws, "--root-dir", str(into), "--json")])
    doc = json.loads(capsys.readouterr()[0])
    assert code == 0 and doc["view"] == "root"
    assert doc["done"] == [] and doc["missing"] == [{"row": 7, "from": "kicad/docs/guide.md",
                                                     "to": "kicad/docs/guides/guide.md"}]


def test_dry_run_lists_and_creates_nothing(ws, tmp_path, capsys):
    into = tmp_path / "libs"
    code, out, _ = run(capsys, "libs", *args(ws, "--into", str(into), "--dry-run", "--json"))
    doc = json.loads(out)
    assert code == 0 and doc["mode"] == "dry-run" and doc["commit"] is None and doc["problems"] == []
    assert schemas.errors(doc, "rename-merge") == []
    assert {s["name"]: s["landing"] for s in doc["sources"]} == {"freecad-lib": "freecad", "kicad-lib": "kicad",
                                                                  "parts-lib": "parts"}
    assert all(s["rewritten"] is None for s in doc["sources"]) and doc["steps"]
    assert not into.exists()
    code, out, _ = run(capsys, "libs", *args(ws, "--dry-run", "--text"))
    assert code == 0 and "kicad-lib" in out and "kicad/" in out


@pytest.mark.parametrize("spoil,code_", [
    (lambda ws: (ws / "public/libs/parts/new.txt").write_text("x"), "merge.dirty"),
    (lambda ws: (ws / "public/libs/parts/README.md").write_text("changed"), "merge.dirty"),
    (lambda ws: commit(ws / "public/libs/parts", {"x.md": "x"}, "not pushed"), "merge.unpushed"),
    (lambda ws: shutil.rmtree(ws / "public/libs/parts"), "merge.missing-source"),
    (lambda ws: git(ws / "public/libs/parts", "branch", "-q", "-m", "main", "trunk"), "merge.no-branch"),
])
def test_dirty_or_missing_sources_are_refused(ws, tmp_path, capsys, spoil, code_):
    spoil(ws)
    into = tmp_path / "libs"
    code, out, err = run(capsys, "libs", *args(ws, "--into", str(into), "--json"))
    assert code == 1 and json.loads(out)["error"]["code"] == code_ and "fix:" in err
    assert not into.exists()
    code, out, _ = run(capsys, "libs", *args(ws, "--into", str(into), "--dry-run", "--json"))
    doc = json.loads(out)
    assert code == 1 and [p["code"] for p in doc["problems"]] == [code_]


def test_into_must_be_new_or_empty_and_outside_the_sources(ws, tmp_path, capsys):
    into = tmp_path / "libs"
    into.mkdir()
    (into / "keep.txt").write_text("someone's")
    code, out, _ = run(capsys, "libs", *args(ws, "--into", str(into), "--json"))
    assert code == 1 and json.loads(out)["error"]["code"] == "merge.into-exists"
    assert (into / "keep.txt").read_text() == "someone's"
    code, out, _ = run(capsys, "libs", *args(ws, "--into", str(ws / "public/libs/kicad/x"), "--json"))
    assert code == 1 and json.loads(out)["error"]["code"] == "merge.into-inside-source"


def test_a_source_can_come_from_a_bundle(ws, tmp_path, capsys):
    bundle = tmp_path / "parts.bundle"
    git(ws / "public/libs/parts", "bundle", "create", "-q", str(bundle), "main")
    (ws / "public/libs/parts/wip.txt").write_text("another session's work")
    into = tmp_path / "libs"
    code, out, err = run(capsys, "libs", *args(ws, "--into", str(into), "--source", f"parts-lib={bundle}", "--json"))
    assert code == 0, err
    parts = [s for s in json.loads(out)["sources"] if s["name"] == "parts-lib"][0]
    assert parts["from"] == str(bundle) and parts["trees_match"] is True
    assert (ws / "public/libs/parts/wip.txt").exists()


def test_unknown_or_clashing_landings_are_refused(ws, tmp_path, capsys):
    m = ws.parent / "map.tsv"
    m.write_text(m.read_text().replace("libs (parts/)", "libs parts/ kicad/"))
    code, out, _ = run(capsys, "libs", *args(ws, "--into", str(tmp_path / "a"), "--json"))
    assert code == 1 and json.loads(out)["error"]["code"] == "merge.landing-unknown"
    code, out, _ = run(capsys, "libs", *args(ws, "--into", str(tmp_path / "a"), "--landing", "parts-lib=kicad/x",
                                             "--json"))
    assert code == 1 and json.loads(out)["error"]["code"] == "merge.landing-clash"


def test_usage_errors_exit_2(ws, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ODAP_RENAME_MAP", raising=False)
    code, out, _ = run(capsys, "libs", *args(ws, "--json"))
    assert code == 2 and json.loads(out)["error"]["code"] == "merge.no-into"
    code, out, _ = run(capsys, "nowhere", *args(ws, "--dry-run", "--json"))
    assert code == 2 and json.loads(out)["error"]["code"] == "merge.unknown-root"
    code, out, _ = run(capsys, "libs", "--workspace", str(ws), "--dry-run", "--json")
    assert code == 2 and json.loads(out)["error"]["code"] == "merge.no-map"
    assert run(capsys, "libs", "--bogus")[0] == 2
    assert run(capsys, "--help")[0] == 0


def test_fast_export_paths_are_rewritten_and_messages_are_not():
    stream = (b"commit refs/heads/main\nmark :1\ndata 25\nM 100644 :1 not/a/command\n"
              b"M 100644 :2 a.md\nM 100644 :3 \"odd \\303\\274.md\"\nD gone.md\nD \"q \\\"x\\\"\"\n\n")
    out = rename_merge_git.prefix_stream(io.BytesIO(stream), "kicad/sub")
    assert out == (b"commit refs/heads/main\nmark :1\ndata 25\nM 100644 :1 not/a/command\n"
                   b"M 100644 :2 kicad/sub/a.md\nM 100644 :3 \"kicad/sub/odd \\303\\274.md\"\nD kicad/sub/gone.md\n"
                   b"D \"kicad/sub/q \\\"x\\\"\"\n\n")
    with pytest.raises(ValueError):
        rename_merge_git.prefix_stream(io.BytesIO(b"R a b\n"), "kicad")


def test_the_engine_never_pushes():
    for mod in (rename_merge, rename_merge_git):
        src = pathlib.Path(mod.__file__).read_text()
        assert '"push"' not in src and "'push'" not in src


def test_python_files_stay_within_budget():
    for mod in (rename_merge, rename_merge_git):
        assert pathlib.Path(mod.__file__).read_text().count("\n") <= 500, mod.__name__


def test_the_schema_is_valid():
    schemas.check_schema("rename-merge")
