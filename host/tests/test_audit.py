"""odw audit: workspace metrics from the repositories' tracked files."""
import json
import subprocess
import textwrap

import pytest

from odw import audit


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, env=audit.git_env())


def repo(root, path, files):
    d = root / path
    d.mkdir(parents=True)
    for name, text in files.items():
        f = d / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text))
    run(["git", "init", "-q"], d)
    run(["git", "add", "-A"], d)
    run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], d)


PRECOMMIT = """\
repos:
  - repo: https://github.com/opendap-project/toolkit-hooks
    rev: {rev}
    hooks:
      - id: no-stray-files
"""


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\npublic/b\to/b\npublic/gone\to/gone\n")
    repo(tmp_path, "public/a", {
        ".pre-commit-config.yaml": PRECOMMIT.format(rev="v0.3.0"),
        "CLAUDE.md": "This repository is open source.\n",
        ".claude/rules/kicad.md": "rule\n",
        "src/big.rs": "fn x() {}\n" * 700,
        "docs/specs/s.md": "# S\n\n- **Status:** Draft\n\nValue (v1.3: 4 → 6).\n",
        "docs/specs/t.md": "# T\n\nFor now this is enough.\n",
        "src/ids.rs": 'const A: &str = "org.opendap.hw.Power";\nconst B: &str = "OPENDAP_TOKEN";\n',
    })
    repo(tmp_path, "public/b", {
        ".pre-commit-config.yaml": PRECOMMIT.format(rev="v0.4.1"),
        "CLAUDE.md": "This repository is Open Source.\n",
        "ui/big.slint": "x\n" * 600,
        "docs/README.md": "# Docs\n",
    })
    return tmp_path


def test_repositories_listed_and_missing_reported(ws):
    r = audit.audit(ws)
    assert r["repositories"]["total"] == 2
    assert r["repositories"]["missing"] == ["public/gone"]


def test_pre_commit_pins_and_variants(ws):
    r = audit.audit(ws)["repositories"]
    assert r["hooks_pins"] == ["v0.3.0", "v0.4.1"]
    assert r["pre_commit_variants"] == 2
    assert r["repos_without_pre_commit"] == []


@pytest.mark.parametrize("hooks_repo", ["toolkit-hooks", "tools"])
def test_hooks_pins_of_either_layout(tmp_path, hooks_repo):
    """The shared hooks come from toolkit-hooks today and from the merged tools root after its cut."""
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\npublic/b\to/b\n")
    pin = PRECOMMIT.replace("toolkit-hooks", hooks_repo)
    repo(tmp_path, "public/a", {".pre-commit-config.yaml": pin.format(rev="v0.1.0")})
    other = PRECOMMIT.replace("toolkit-hooks", "tools-extra")  # another repository is no hooks pin
    repo(tmp_path, "public/b", {".pre-commit-config.yaml": other.format(rev="v9.9.9")})
    r = audit.audit(tmp_path)
    assert r["repositories"]["hooks_pins"] == ["v0.1.0"]
    assert r["conformance"]["public/a"]["hooks_pin"] == "v0.1.0"
    assert r["conformance"]["public/b"]["hooks_pin"] == ""


def test_claude_files(ws):
    r = audit.audit(ws)["repositories"]
    assert r["claude_md_lowercase_open_source"] == 1
    assert r["claude_rules_copies"] == 1


def test_file_budgets(ws):
    r = audit.audit(ws)["software"]
    assert r["rust_files_over_600_lines"] == 1
    assert r["slint_files_over_500_lines"] == 1


def test_docs_smells(ws):
    r = audit.audit(ws)["docs"]
    assert r["docs_markdown_files"] == 3
    assert r["specs_with_inline_history"] == 1
    assert r["docs_with_working_notes"] == 1


def test_identifiers(ws):
    r = audit.audit(ws)["identifiers"]
    assert r["rdns_org_opendap_uses"] == 1
    assert r["opendap_env_uses"] == 1


def test_per_repo_conformance(ws):
    c = audit.audit(ws)["conformance"]
    assert c["public/a"]["hooks_pin"] == "v0.3.0"
    assert c["public/b"]["claude_md_lowercase_open_source"] is False


def test_files_without_rename_row(ws, tmp_path):
    rm = tmp_path / "rename-map.tsv"
    rm.write_text("old\tnew\tkind\taction\tnotes\tscope\npublic/a/CLAUDE.md\tx\tfile\tmove\t\ts\npublic/b/**\ty\tdir\tmove\t\ts\n")
    r = audit.audit(ws, rename_map=rm)
    unmapped = r["rename_map"]["unmapped"]
    assert "public/a/src/big.rs" in unmapped
    assert "public/a/CLAUDE.md" not in unmapped
    assert not any(u.startswith("public/b/") for u in unmapped)


def test_snapshot_appends_to_drift(ws, tmp_path):
    out = tmp_path / "matrices"
    audit.write(audit.audit(ws), out, date="2026-10-26")
    audit.write(audit.audit(ws), out, date="2026-10-27")
    text = (out / "drift.toml").read_text()
    assert text.count("[[snapshot]]") == 2
    assert (out / "conformance.toml").is_file()


def test_cli_json(ws, capsys):
    import json
    assert audit.main(["--workspace", str(ws), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["repositories"]["total"] == 2


def test_write_dry_run_names_the_files_and_writes_nothing(ws, tmp_path, capsys):
    out = tmp_path / "snap"
    assert audit.main(["--workspace", str(ws), "--write", str(out), "--dry-run"]) == 0
    assert not out.exists()
    assert f"would write {out / 'drift.toml'}" in capsys.readouterr().err


def test_json_carries_a_schema(ws, capsys):
    assert audit.main(["--workspace", str(ws), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "opendap.workspace-audit/v1"


def test_no_workspace_is_an_error_with_a_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert audit.main([]) == 2
    assert "error workspace.not_found:" in capsys.readouterr().err


PY_STANZA = """\
import os
import subprocess
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]
subprocess.run(["git", "init"])
"""
CONFTEST = """\
import os
import pytest
@pytest.fixture(autouse=True)
def no_inherited_git_environment(monkeypatch):
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
"""
RS_HELPER = """\
use std::process::Command;
fn command(p: &str) -> Command {
    let mut c = Command::new(p);
    for (k, _) in std::env::vars().filter(|(k, _)| k.starts_with("GIT_")) {
        c.env_remove(k);
    }
    c
}
"""
SH_STANZA = """\
#!/bin/sh
for v in $(env | sed -n 's/^\\(GIT_[A-Za-z0-9_]*\\)=.*/\\1/p'); do unset "$v"; done
git init -q "$tmp"
"""


def test_test_files_running_git_without_the_protection(tmp_path):
    """HL-64: a test file that runs git (subprocess, Command::new, `git ` in shell) needs the GIT_* protection."""
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\n")
    repo(tmp_path, "public/a", {
        "py/tests/test_leaky.py": 'import subprocess\nsubprocess.run(["git", "init"])\n',
        "py/tests/test_per_call.py": 'import os, subprocess\nENV = {k: v for k, v in os.environ.items() '
                                     'if not k.startswith("GIT_")}\nsubprocess.run(["git", "init"], env=ENV)\n',
        "py/tests/test_stanza.py": PY_STANZA,
        "py/tests/test_pure.py": "def test_x():\n    assert True\n",
        "pt/tests/conftest.py": CONFTEST,
        "pt/tests/deep/test_under_conftest.py": 'import subprocess\nsubprocess.run(["git", "init"])\n',
        "rs/tests/leaky.rs": 'use std::process::Command;\nfn t() { Command::new("git"); }\n',
        "rs/tests/helper.rs": RS_HELPER + 'fn t() { command("git"); }\n',
        "rs/tests/cleared.rs": 'fn t() { std::process::Command::new("odw").env_clear(); }\n',
        "rs/src/lib.rs": 'fn x() {}\n#[cfg(test)]\nmod tests {\n    fn t() { std::process::Command::new("git"); }\n}\n',
        "rs/src/main.rs": 'fn main() { std::process::Command::new("git"); }\n',
        "sh/tests/leaky.sh": '#!/bin/sh\n# git is needed\ncd "$tmp" && git init -q\n',
        "sh/tests/stanza.sh": SH_STANZA,
        "sh/tests/no_git.sh": '#!/bin/sh\n# runs no git\necho digit\n',
    })
    r = audit.audit(tmp_path)
    assert r["conformance"]["public/a"]["tests_running_git_unprotected"] == [
        "py/tests/test_leaky.py", "py/tests/test_per_call.py", "rs/src/lib.rs", "rs/tests/leaky.rs",
        "sh/tests/leaky.sh"]
    assert r["software"]["test_files_running_git_unprotected"] == 5
