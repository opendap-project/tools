"""odw docs: the docs gates (links, private mentions, terms, working notes, status vocabulary) and the docs index."""
import json
import pytest

from odw import docs

SPEC = """\
# Thing spec

- **Status:** Draft, 2026-10-26.
- **Requirements:** SYS-001

The thing is Open Source. See [the other spec](other.md) and [cli](https://github.com/opendap-project/cli).
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".git").mkdir()
    d = tmp_path / "docs" / "specs"
    d.mkdir(parents=True)
    (d / "thing.md").write_text(SPEC)
    (d / "other.md").write_text("# Other\n\n- **Status:** accepted\n\nText.\n")
    return tmp_path


def kinds(findings):
    return [(f.rule, f.severity) for f in findings]


def test_clean_doc_has_no_findings(repo):
    assert docs.check_file(repo / "docs/specs/thing.md", repo_root=repo) == []


def test_broken_relative_link_is_an_error(repo):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC.replace("other.md", "missing.md"))
    assert kinds(docs.check_file(p, repo_root=repo)) == [("link", "error")]


def test_relative_link_out_of_the_repository_is_an_error(repo):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC + "\nSee [board](../../../board/README.md).\n")
    assert ("cross-repo-link", "error") in kinds(docs.check_file(p, repo_root=repo))


def test_link_to_a_private_repository_is_an_error(repo):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC + "\nSee [x](https://github.com/opendap-project/secret-repo).\n")
    found = docs.check_file(p, repo_root=repo, private_repos={"secret-repo"})
    assert ("private-link", "error") in kinds(found)


def test_lower_case_terms_are_errors_outside_code(repo):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC + "\nIt is open source and a digital audio player. `open source` in code is fine.\n")
    found = [f for f in docs.check_file(p, repo_root=repo) if f.rule == "term"]
    assert len(found) == 2 and all(f.severity == "error" for f in found)


def test_working_notes_warn_outside_open_items(repo):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC + "\nFor now this is enough.\n\n## Open items\n\n- OI-1: TODO pick a value.\n")
    found = [f for f in docs.check_file(p, repo_root=repo) if f.rule == "working-note"]
    assert len(found) == 1 and found[0].severity == "warning" and found[0].line == 8


# Lines taken from published docs where "next step" is UI wording, not a working note.
NEXT_STEP_AS_UI_WORDING = [
    'Failures have "a reason and next step" (devices-and-sync spec §6), never colour alone.',
    "| **Error** | One sentence with what failed and why, the next step, and one action that takes it |",
    "Error: icon and words, what failed and why, the next step and its action, never looking like loading.",
    "| SYS-014 | Errors shall be explained in a human sentence with the next step, never blaming the user. |",
    'Each failure has its reason and next step ("Not enough space: 2.1 GB needed").',
    '| Cannot sync | "Can\'t sync", and the reason with the next step one step down | Cross |',
]
NEXT_STEP_AS_WORKING_NOTE = [
    "Next step: measure the noise floor.",
    "- Next steps: wire the codec.",
    "The next step is to pick a value.",
    "Our next steps are the layout and the DRC.",
]


@pytest.mark.parametrize("line", NEXT_STEP_AS_UI_WORDING)
def test_next_step_as_ui_wording_is_not_a_working_note(repo, line):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC + "\n" + line + "\n")
    assert [f for f in docs.check_file(p, repo_root=repo) if f.rule == "working-note"] == []


@pytest.mark.parametrize("line", NEXT_STEP_AS_WORKING_NOTE)
def test_next_step_as_a_working_note_still_warns(repo, line):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC + "\n" + line + "\n")
    assert [f.rule for f in docs.check_file(p, repo_root=repo)] == ["working-note"]


def test_working_phrase_inside_double_quotes_is_quoted_wording(repo):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC + '\nThe button reads \u201cFor now, skip\u201d and the toast "TODO list saved".\n')
    assert [f for f in docs.check_file(p, repo_root=repo) if f.rule == "working-note"] == []


def test_status_outside_the_vocabulary_warns(repo):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC.replace("Draft, 2026-10-26.", "v1, kind of done"))
    assert ("status", "warning") in kinds(docs.check_file(p, repo_root=repo))


def test_header_is_parsed():
    h = docs.header(SPEC)
    assert h["status"].startswith("Draft") and h["requirements"] == "SYS-001"


def test_index_lists_titles_and_status_between_markers(repo):
    readme = repo / "docs" / "README.md"
    readme.write_text("# Documentation\n\nIntro.\n\n<!-- index:start -->\n<!-- index:end -->\n")
    out = docs.render_index(repo / "docs")
    assert "| [Thing spec](specs/thing.md) | Draft, 2026-10-26. |" in out
    assert out.startswith("# Documentation\n\nIntro.\n")
    readme.write_text(out)
    assert docs.index_is_current(repo / "docs")


def test_cli_exit_status(repo):
    assert docs.main(["check", str(repo / "docs/specs/thing.md")]) == 0
    (repo / "docs/specs/thing.md").write_text(SPEC.replace("other.md", "gone.md"))
    assert docs.main(["check", str(repo / "docs/specs/thing.md")]) == 1


def test_private_repository_may_link_private_repositories(repo):
    p = repo / "docs/specs/thing.md"
    p.write_text(SPEC + "\nSee [x](https://github.com/opendap-project/secret-repo).\n")
    found = docs.check_file(p, repo_root=repo, private_repos={"secret-repo"}, repo_is_private=True)
    assert "private-link" not in [f.rule for f in found]


def test_private_paths_come_from_the_overlay(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\n")
    (tmp_path / "private" / "overlay").mkdir(parents=True)
    (tmp_path / "private" / "overlay" / "repos.tsv").write_text("path\trepository\nprivate/eng\topendap-project/secret-overlay\n")
    assert docs.private_repo_names(tmp_path) == {"secret-overlay"}
    assert docs.private_repo_paths(tmp_path) == {"private/eng"}


def test_index_of_a_folder_from_a_readme_elsewhere(tmp_path):
    (tmp_path / "text").mkdir()
    (tmp_path / "text" / "0001-a.md").write_text("# RFC 0001: A\n\n- **Status:** Accepted (2026-10-22).\n")
    readme = tmp_path / "README.md"
    readme.write_text("# RFCs\n\n<!-- index:start -->\n<!-- index:end -->\n")
    out = docs.render_index(tmp_path / "text", readme=readme)
    assert "| [RFC 0001: A](text/0001-a.md) | Accepted (2026-10-22). |" in out
    readme.write_text(out)
    assert docs.index_is_current(tmp_path / "text", readme=readme)


def test_files_skip_linked_worktrees_and_test_fixtures(tmp_path):
    (tmp_path / "repo/docs").mkdir(parents=True)
    (tmp_path / "repo/docs/a.md").write_text("# A\n")
    (tmp_path / "repo/tests/fixtures/x").mkdir(parents=True)
    (tmp_path / "repo/tests/fixtures/x/copy.md").write_text("# Copy\n")
    (tmp_path / "repo-task/docs").mkdir(parents=True)
    (tmp_path / "repo-task/.git").write_text("gitdir: ../repo/.git/worktrees/repo-task\n")
    (tmp_path / "repo-task/docs/a.md").write_text("# A\n")
    (tmp_path / "repo/vendor/lib").mkdir(parents=True)
    (tmp_path / "repo/vendor/lib/README.md").write_text("# Theirs\n")
    found = [f.relative_to(tmp_path).as_posix() for f in docs._files([tmp_path])]
    assert found == ["repo/docs/a.md"]
    assert [f.name for f in docs._files([tmp_path / "repo-task"])] == ["a.md"]


def test_check_json_is_a_check_result(tmp_path, capsys):
    from odw import schemas
    f = tmp_path / "x.md"
    f.write_text("# X\n\nSee [gone](missing.md).\n")
    assert docs.main(["check", "--json", str(f)]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert schemas.errors(doc, "check-result") == [] and doc["results"][0]["verdict"] == "fail"


def test_index_dry_run_writes_nothing(tmp_path, capsys):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "README.md").write_text("# Docs\n\n<!-- index:start -->\n<!-- index:end -->\n")
    (d / "a.md").write_text("# A\n")
    before = (d / "README.md").read_text()
    assert docs.main(["index", str(d), "--dry-run"]) == 0
    assert (d / "README.md").read_text() == before and "would write" in capsys.readouterr().err
