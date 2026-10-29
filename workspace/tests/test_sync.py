"""odw sync: shared boilerplate rendered from the workspace templates; drift is reported or repaired."""
import json
import shutil

import pytest

from odw import schemas, sync

MANIFEST = """\
schema = "opendap.sync/v1"

[[file]]
template = "claude/settings.json"
target = ".claude/settings.json"
repos = ["public/*"]
when = "always"

[[file]]
template = "claude/rules/kicad.md"
target = ".claude/rules/kicad.md"
repos = ["public/*"]
when = "present"
"""


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\npublic/b\to/b\npublic/c\to/c\n")
    t = tmp_path / "templates"
    (t / "claude" / "rules").mkdir(parents=True)
    (t / "claude" / "settings.json").write_text('{"ok": true}\n')
    (t / "claude" / "rules" / "kicad.md").write_text("canonical\n")
    (t / "sync.toml").write_text(MANIFEST)
    for r in "abc":
        (tmp_path / "public" / r / ".git").mkdir(parents=True)
    (tmp_path / "public/a/.claude").mkdir()
    (tmp_path / "public/a/.claude/settings.json").write_text('{"ok": true}\n')
    (tmp_path / "public/b/.claude/rules").mkdir(parents=True)
    (tmp_path / "public/b/.claude/settings.json").write_text('{"ok": false}\n')
    (tmp_path / "public/b/.claude/rules/kicad.md").write_text("old variant\n")
    return tmp_path


def test_check_reports_drift_and_missing(ws):
    report = sorted((d.repo, d.target, d.state) for d in sync.check(ws, ws / "templates"))
    assert report == [("public/b", ".claude/rules/kicad.md", "differs"),
                      ("public/b", ".claude/settings.json", "differs"),
                      ("public/c", ".claude/settings.json", "missing")]


def test_present_rule_ignores_repos_without_the_file(ws):
    assert not any(d.repo == "public/a" and d.target.endswith("kicad.md") for d in sync.check(ws, ws / "templates"))


def test_apply_repairs_and_check_is_then_clean(ws):
    changed = sync.apply(ws, ws / "templates")
    assert len(changed) == 3
    assert (ws / "public/c/.claude/settings.json").read_text() == '{"ok": true}\n'
    assert sync.check(ws, ws / "templates") == []


def test_cli_exit_status(ws):
    assert sync.main(["--workspace", str(ws), "--templates", str(ws / "templates"), "--check"]) == 1
    assert sync.main(["--workspace", str(ws), "--templates", str(ws / "templates"), "--apply"]) == 0
    assert sync.main(["--workspace", str(ws), "--templates", str(ws / "templates"), "--check"]) == 0


BLOCK_MANIFEST = """\
schema = "opendap.sync/v1"

[[block]]
target = ".gitignore"
repos = ["public/*"]

[[block.fragment]]
template = "gitignore/common"

[[block.fragment]]
template = "gitignore/rust"
when_tracked = ["Cargo.toml"]
"""


def git(repo, *args):
    import subprocess
    from odw import audit
    subprocess.run(["git", *args], cwd=repo, check=True, env=audit.git_env(), capture_output=True)


@pytest.fixture
def bws(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/r\to/r\npublic/py\to/py\n")
    t = tmp_path / "templates"
    (t / "gitignore").mkdir(parents=True)
    (t / "gitignore" / "common").write_text(".DS_Store\n*.noindex\n")
    (t / "gitignore" / "rust").write_text("target\n")
    (t / "sync.toml").write_text(BLOCK_MANIFEST)
    for name, files in (("r", {"Cargo.toml": "[workspace]\n", ".gitignore": ".DS_Store\n\n# Rust\ntarget/\n\n# Local\nscratch/\n"}),
                        ("py", {"x.py": "", ".gitignore": ".DS_Store\n"})):
        d = tmp_path / "public" / name
        d.mkdir(parents=True)
        for f, text in files.items():
            (d / f).write_text(text)
        git(d, "init", "-q")
        git(d, "add", "-A")
    return tmp_path


def test_block_fragments_follow_tracked_files(bws):
    assert sync.render_block(bws, bws / "templates", "public/r", sync.blocks(bws / "templates")[0]).count("target") == 1
    assert "target" not in sync.render_block(bws, bws / "templates", "public/py", sync.blocks(bws / "templates")[0])


def test_when_tracked_except_ignores_files_under_those_paths(bws):
    block = {"target": ".gitignore", "repos": ["public/*"],
             "fragment": [{"template": "gitignore/rust", "when_tracked": ["x.py"],
                           "when_tracked_except": ["**/tests/fixtures/**", "**/fixtures/**"]}]}
    assert sync.render_block(bws, bws / "templates", "public/py", block) is not None   # x.py at the root counts
    py = bws / "public/py"
    git(py, "rm", "-q", "--cached", "x.py")
    for f in ("tests/fixtures/c/x.py", "fixtures/x.py", "a/fixtures/b/x.py"):
        (py / f).parent.mkdir(parents=True, exist_ok=True)
        (py / f).write_text("")
        git(py, "add", f)
    assert sync.render_block(bws, bws / "templates", "public/py", block) is None        # only fixtures track it
    (py / "src").mkdir()
    (py / "src" / "x.py").write_text("")
    git(py, "add", "src/x.py")
    assert sync.render_block(bws, bws / "templates", "public/py", block) is not None   # one outside them does


def test_missing_block_is_drift_then_apply_inserts_it_and_drops_duplicates(bws):
    states = {(d.repo, d.state) for d in sync.check(bws, bws / "templates")}
    assert states == {("public/r", "no-block"), ("public/py", "no-block")}
    sync.apply(bws, bws / "templates")
    text = (bws / "public/r/.gitignore").read_text()
    assert text.startswith("# >>> odapkit sync")
    assert text.count(".DS_Store") == 1
    outside = text.split("# <<< odapkit sync")[1]
    assert ".DS_Store" not in outside and "scratch/" in outside
    assert "target/" not in outside and "# Rust" not in outside
    assert sync.check(bws, bws / "templates") == []


def test_edited_block_is_drift(bws):
    sync.apply(bws, bws / "templates")
    p = bws / "public/py/.gitignore"
    p.write_text(p.read_text().replace("*.noindex", "*.tmp"))
    assert [(d.repo, d.state) for d in sync.check(bws, bws / "templates")] == [("public/py", "differs")]


def test_no_matching_fragment_means_no_file_needed(bws):
    m = (bws / "templates" / "sync.toml").read_text().replace('template = "gitignore/common"\n', 'template = "gitignore/common"\nwhen_tracked = ["*.kicad_pro"]\n')
    (bws / "templates" / "sync.toml").write_text(m)
    (bws / "public/py/.gitignore").unlink()
    assert ("public/py", "missing") not in {(d.repo, d.state) for d in sync.check(bws, bws / "templates")}


MD_MANIFEST = """\
schema = "opendap.sync/v1"

[[block]]
target = "CLAUDE.md"
repos = ["public/*"]
comment = "<!--"
comment_end = "-->"
place = "after-intro"

[[block.fragment]]
template = "claude/contributor.md"
"""


@pytest.fixture
def mws(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/r\to/r\npublic/bare\to/bare\n")
    t = tmp_path / "templates" / "claude"
    t.mkdir(parents=True)
    (t / "contributor.md").write_text("Part of OpenDAP.\n\nContributor rules: the guide.\n")
    (tmp_path / "templates" / "sync.toml").write_text(MD_MANIFEST)
    d = tmp_path / "public" / "r"
    d.mkdir(parents=True)
    (d / "CLAUDE.md").write_text("# R\n\nWhat R is,\non two lines.\n\nPart of OpenDAP.\n\n## Working here\n\n- Own rule.\n")
    git(d, "init", "-q")
    git(d, "add", "-A")
    b = tmp_path / "public" / "bare"
    b.mkdir(parents=True)
    git(b, "init", "-q")
    return tmp_path


def test_markdown_block_goes_after_the_intro_and_stays_in_place(mws):
    assert [(d.repo, d.state) for d in sync.check(mws, mws / "templates")] == [("public/r", "no-block")]
    sync.apply(mws, mws / "templates")
    text = (mws / "public/r/CLAUDE.md").read_text()
    head, block = text.split("<!-- >>> odapkit sync")
    assert head == "# R\n\nWhat R is,\non two lines.\n\n"
    assert block.split("\n", 1)[0].endswith(" -->")
    assert "<!-- <<< odapkit sync -->\n\n## Working here\n\n- Own rule.\n" in text
    assert text.count("Part of OpenDAP.") == 1
    assert sync.check(mws, mws / "templates") == []
    (mws / "templates/claude/contributor.md").write_text("Part of OpenDAP.\n\nContributor rules: the new guide.\n")
    assert [d.state for d in sync.check(mws, mws / "templates")] == ["differs"]
    sync.apply(mws, mws / "templates")
    again = (mws / "public/r/CLAUDE.md").read_text()
    assert again.startswith("# R\n\nWhat R is,") and "the new guide" in again and again.count(">>> odapkit sync") == 1
    assert not (mws / "public/bare/CLAUDE.md").exists()


def test_a_repository_without_its_pre_commit_hook_is_drift(bws):
    r = bws / "public/r"
    (r / ".pre-commit-config.yaml").write_text("repos: []\n")
    found = [(d.repo, d.target, d.state) for d in sync.check(bws, bws / "templates")]
    assert ("public/r", ".git/hooks/pre-commit", "missing") in found
    hook = r / ".git/hooks/pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n")
    assert ("public/r", ".git/hooks/pre-commit", "missing") not in [(d.repo, d.target, d.state) for d in sync.check(bws, bws / "templates")]


def test_apply_dry_run_names_the_targets_and_writes_nothing(ws, capsys):
    before = (ws / "public/b/.claude/settings.json").read_text()
    assert sync.main(["--workspace", str(ws), "--templates", str(ws / "templates"), "--apply", "--dry-run"]) == 0
    assert (ws / "public/b/.claude/settings.json").read_text() == before
    assert "would write public/b/.claude/settings.json" in capsys.readouterr().out
    assert sync.check(ws, ws / "templates")


def test_check_json_is_a_check_result(ws, capsys):
    assert sync.main(["--workspace", str(ws), "--templates", str(ws / "templates"), "--check", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert schemas.errors(doc, "check-result") == []
    assert {r["verdict"] for r in doc["results"]} == {"fail"}


def test_missing_templates_is_an_error_with_a_code(ws, tmp_path, capsys):
    assert sync.main(["--workspace", str(ws), "--templates", str(tmp_path / "none"), "--check", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "templates.not_found"


PUSH_CONFIG = """repos:
  - repo: local
    hooks:
      - id: cargo-test
        name: cargo test (before push)
        entry: cargo test
        language: system
        pass_filenames: false
        stages: [pre-push]
"""


def _hooks(ws):
    return [(d.repo, d.target, d.template) for d in sync.check(ws, ws / "templates") if "hooks/" in d.target]


def test_a_config_with_pre_push_hooks_needs_its_pre_push_hook_installed(bws):
    """HL-83: `pre-commit install` installs only the pre-commit hook, so a fresh clone ran no pre-push stage."""
    (bws / "public/r/.pre-commit-config.yaml").write_text(PUSH_CONFIG)
    (bws / "public/py/.pre-commit-config.yaml").write_text("repos: []\n")
    found = _hooks(bws)
    assert ("public/r", ".git/hooks/pre-push", "pre-commit install --hook-type pre-push") in found
    assert not any(repo == "public/py" and target.endswith("pre-push") for repo, target, _ in found)


@pytest.mark.skipif(not shutil.which("pre-commit"), reason="pre-commit is not installed")
def test_apply_installs_both_hook_types(bws):
    (bws / "public/r/.pre-commit-config.yaml").write_text(PUSH_CONFIG)
    changed = [(d.repo, d.target) for d in sync.apply(bws, bws / "templates") if "hooks/" in d.target]
    assert ("public/r", ".git/hooks/pre-commit") in changed and ("public/r", ".git/hooks/pre-push") in changed
    assert (bws / "public/r/.git/hooks/pre-push").is_file() and (bws / "public/r/.git/hooks/pre-commit").is_file()
    assert _hooks(bws) == []
