"""odw req: requirements as data (opendap.requirements/v1), rendered to the README they come from."""
import json
import pathlib

import pytest

from odw import requirements, schemas, workspace

README = """\
# HAT standard requirements

Requirements for the standard.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| ZZQ-001 | The standard shall cover both sides. | must | Confirmed |
| ZZQ-007 | A HAT shall identify itself through an EEPROM. | must | Withdrawn (2026-10-25: identity is in USB descriptors, ZZQ-029) |
| ZZQ-018 | A lock shall hold the HAT \\| a button releases it. | should | Draft |

## Context

Some context.
"""


def test_import_reads_rows_and_keeps_prose():
    doc = requirements.parse(README)
    assert doc["schema"] == "opendap.requirements/v1"
    reqs = requirements.rows(doc)
    assert [r["id"] for r in reqs] == ["ZZQ-001", "ZZQ-007", "ZZQ-018"]
    assert reqs[1]["status"] == "withdrawn"
    assert reqs[1]["note"] == "2026-10-25: identity is in USB descriptors, ZZQ-029"
    assert reqs[2]["priority"] == "should" and "| a button" in reqs[2]["text"]
    assert schemas.errors(doc, "requirements") == []


def test_round_trip_is_lossless():
    assert requirements.render(requirements.parse(README)) == README


def test_render_from_toml_file(tmp_path):
    src = tmp_path / "requirements.toml"
    src.write_text(requirements.dumps(requirements.parse(README)))
    assert requirements.render(requirements.load(src)) == README


def test_unknown_status_is_a_schema_error():
    doc = requirements.parse(README.replace("| Draft |", "| Maybe |"))
    assert schemas.errors(doc, "requirements") != []


def test_duplicate_ids_are_reported():
    doc = requirements.parse(README.replace("ZZQ-018", "ZZQ-001"))
    assert requirements.problems(doc) == ["duplicate id ZZQ-001"]


def test_check_detects_a_stale_readme(tmp_path):
    (tmp_path / "requirements.toml").write_text(requirements.dumps(requirements.parse(README)))
    (tmp_path / "README.md").write_text(README)
    assert requirements.is_current(tmp_path / "requirements.toml", tmp_path / "README.md")
    (tmp_path / "README.md").write_text(README + "\nedited by hand\n")
    assert not requirements.is_current(tmp_path / "requirements.toml", tmp_path / "README.md")


def test_cli_import_and_render(tmp_path, capsys):
    md = tmp_path / "README.md"
    md.write_text(README)
    assert requirements.main(["import", str(md), "--write"]) == 0
    assert (tmp_path / "requirements.toml").is_file()
    assert requirements.main(["render", str(tmp_path / "requirements.toml"), "--check"]) == 0


def workspace_readmes(ws):
    """Every public requirements README of the workspace, outside linked worktrees and submodules (a folder whose
    .git is a file): other sessions create and remove those while the tests run."""
    ws = pathlib.Path(ws)
    out = []
    for p in sorted(ws.glob("public/**/docs/requirements/README.md")):
        parents = p.relative_to(ws).parents
        if not any((ws / d / ".git").is_file() for d in parents if d != pathlib.Path(".")):
            out.append(p)
    return out


def test_readmes_skip_linked_worktrees(tmp_path):
    for repo, git in (("public/a", "dir"), ("public/a-T-01", "file")):
        (tmp_path / repo / "docs/requirements").mkdir(parents=True)
        (tmp_path / repo / "docs/requirements/README.md").write_text("x\n")
        if git == "dir":
            (tmp_path / repo / ".git").mkdir()
        else:
            (tmp_path / repo / ".git").write_text("gitdir: elsewhere\n")
    assert [p.relative_to(tmp_path).as_posix() for p in workspace_readmes(tmp_path)] == [
        "public/a/docs/requirements/README.md"]


WS = workspace.root(pathlib.Path(__file__))
REAL = workspace_readmes(WS) if WS else []


@pytest.mark.skipif(not REAL, reason="no workspace checkout")
@pytest.mark.parametrize("path", REAL, ids=lambda p: str(p.relative_to(WS)))
def test_every_workspace_readme_round_trips(path):
    text = path.read_text()
    doc = requirements.parse(text)
    assert requirements.render(doc) == text
    assert schemas.errors(doc, "requirements") == []


def test_index_reads_listed_repositories_and_skips_nested_ones(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\n")
    base = tmp_path / "public" / "a"
    (base / ".git").mkdir(parents=True)
    (base / "docs" / "requirements").mkdir(parents=True)
    (base / "docs" / "requirements" / "README.md").write_text(README)
    nested = base / "lib" / "copy"
    (nested / "docs" / "requirements").mkdir(parents=True)
    (nested / ".git").write_text("gitdir: elsewhere\n")
    (nested / "docs" / "requirements" / "README.md").write_text(README)
    ids = [r["id"] for r in requirements.index(tmp_path)]
    assert ids == ["ZZQ-001", "ZZQ-007", "ZZQ-018"]


PREFIXES = "prefix\tpath\tnote\nZZQ\tpublic/a/docs/requirements\t\nXYZ\tpublic/a/docs/requirements\tlegacy\n"


def ws_with(tmp_path, readme=README, prefixes=PREFIXES, extra=None):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\n")
    base = tmp_path / "public" / "a"
    (base / ".git").mkdir(parents=True)
    (base / "docs" / "requirements").mkdir(parents=True)
    (base / "docs" / "requirements" / "README.md").write_text(readme)
    (tmp_path / "id-prefixes.tsv").write_text(prefixes)
    for name, text in (extra or {}).items():
        (base / name).parent.mkdir(parents=True, exist_ok=True)
        (base / name).write_text(text)
    return tmp_path


def test_prefix_check_passes_when_every_prefix_is_registered(tmp_path):
    assert requirements.check_prefixes(ws_with(tmp_path)) == []


def test_prefix_check_reports_unregistered_prefix(tmp_path):
    ws = ws_with(tmp_path, prefixes="prefix\tpath\tnote\n")
    assert requirements.check_prefixes(ws) == ["ZZQ: used in public/a/docs/requirements but not in any id-prefixes.tsv"]


def test_prefix_check_reports_wrong_home(tmp_path):
    ws = ws_with(tmp_path, prefixes="prefix\tpath\tnote\nZZQ\tpublic/b/docs/requirements\t\n")
    assert any("registered for public/b" in p for p in requirements.check_prefixes(ws))


def test_refs_report_unknown_and_withdrawn_ids(tmp_path):
    ws = ws_with(tmp_path, extra={"docs/specs/s.md": "Follows ZZQ-001 and ZZQ-007, not ZZQ-099. DRV-001 is not ours.\n"})
    errors, warnings = requirements.check_refs(ws)
    assert errors == ["public/a/docs/specs/s.md: unknown requirement ZZQ-099"]
    assert warnings == ["public/a/docs/specs/s.md: cites ZZQ-007, which is withdrawn"]


def test_refs_ignore_the_requirements_files_themselves(tmp_path):
    ws = ws_with(tmp_path)
    assert requirements.check_refs(ws) == ([], [])


def test_render_and_import_dry_run_write_nothing(tmp_path, capsys):
    md = tmp_path / "README.md"
    md.write_text(README)
    assert requirements.main(["import", str(md), "--write", "--dry-run"]) == 0
    assert not (tmp_path / "requirements.toml").exists()
    requirements.main(["import", str(md), "--write"])
    md.write_text("stale\n")
    assert requirements.main(["render", str(tmp_path / "requirements.toml"), "--dry-run"]) == 0
    assert md.read_text() == "stale\n" and "(changed)" in capsys.readouterr().err


def test_index_json_carries_a_schema(tmp_path, capsys):
    ws = ws_with(tmp_path)
    assert requirements.main(["index", "--workspace", str(ws), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "opendap.requirements-index/v1" and doc["requirements"]
