"""odw plan mirror: one public tracking issue per accepted RFC, generated from the programme's public tasks."""
import json

import pytest

from odw import clikit, plan, plan_mirror, schemas

TASK = """\
id = "{id}"
phase = {phase}
title = {title}
source = "test"
repos = {repos}
files = []
depends_on = []
blocked_by = []
status = "{status}"
visibility = "{vis}"
tests = []
docs = {docs}
evidence = {evidence}
notes = "private reasoning that never leaves the programme"
"""

MIRROR = """\
schema = "opendap.plan-mirror-config/v1"
repository = "o/rfcs"
workspace = "o/workspace"
label = "tracking"

[phase_rfc]
"0" = "0001"

[task_rfc]
"T-05" = "0003"

[public_title]
"T-06" = "Public wording of RFC 0002 work"
"""


def task(root, id, title, phase=0, status="todo", vis="public", repos=("public/a",), evidence=None):
    done = status == "done"
    (root / "tasks" / f"{id}.toml").write_text(TASK.format(
        id=id, phase=phase, title=json.dumps(title), status=status, vis=vis, repos=json.dumps(list(repos)),
        docs='["README.md"]' if done else "[]",
        evidence="{ " + ", ".join(f'"{k}" = "{v}"' for k, v in (evidence or {}).items()) + " }" if evidence else "{}"))


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\npublic/rfcs\to/rfcs\n")
    (tmp_path / "private/wp").mkdir(parents=True)
    (tmp_path / "private/wp/repos.tsv").write_text("path\trepository\nprivate/engineering\to/engineering\n")
    text = tmp_path / "public/rfcs/text"
    text.mkdir(parents=True)
    (text / "0001-first.md").write_text("# RFC 0001: First\n\n- **Status:** Accepted (2026-10-01).\n")
    (text / "0002-second.md").write_text("# RFC 0002: Second\n\n- **Status:** Accepted (2026-10-02).\n")
    (text / "0003-draft.md").write_text("# RFC 0003: Draft\n\n- **Status:** Draft.\n")
    root = tmp_path / "programme"
    (root / "tasks").mkdir(parents=True)
    (root / "drafts").mkdir()
    (root / "decisions.toml").write_text('[[decision]]\nid = "D-SECRET"\nstatus = "decided"\n')
    (root / "mirror.toml").write_text(MIRROR)
    task(root, "T-01", "Apply the first rule", status="done",
         evidence={"public/a": "abc1234", "private/engineering": "def5678", "workspace": "1234567"})
    task(root, "T-02", "Second step of RFC 0002", phase=3)
    task(root, "T-03", "A private task", vis="private", repos=("private/engineering",))
    task(root, "T-04", "Unmapped phase", phase=4)
    task(root, "T-05", "Mapped to a draft", phase=0)
    task(root, "T-06", "Internal wording (D-SECRET, drafts/x.md)", phase=4)
    return tmp_path


def build(ws):
    return plan_mirror.build(plan.Programme(ws / "programme"), ws, plan_mirror.load_config(ws / "programme"))


def test_one_issue_per_accepted_rfc_from_public_tasks_only(ws):
    doc = build(ws)
    assert [i["rfc"] for i in doc["issues"]] == ["0001", "0002"]
    assert [t["id"] for t in doc["issues"][0]["tasks"]] == ["T-01"]   # by phase default
    assert [t["id"] for t in doc["issues"][1]["tasks"]] == ["T-02", "T-06"]   # by its (public) title
    assert {u["id"]: u["reason"] for u in doc["unmapped"]} == {
        "T-04": "no RFC: none in the title, task_rfc or phase_rfc", "T-05": "RFC 0003 is not accepted (draft)"}
    assert all("T-03" not in i["body"] for i in doc["issues"])
    assert schemas.errors(doc, "plan-mirror") == []


def test_issue_text_links_the_rfc_and_lists_status_and_public_evidence_only(ws):
    first = build(ws)["issues"][0]
    assert first["title"] == "Tracking: RFC 0001: First"
    body = first["body"]
    assert body.startswith("<!-- odapkit plan mirror: rfc 0001 -->\n")
    assert "https://github.com/o/rfcs/blob/main/text/0001-first.md" in body
    assert "- [x] **T-01** Apply the first rule: done (o/a@abc1234, o/workspace@1234567)" in body
    assert "1 of 1 done" in body
    assert "def5678" not in body and "engineering" not in body and "private reasoning" not in body


def test_public_title_replaces_the_title_and_the_private_one_never_leaves(ws):
    body = build(ws)["issues"][1]["body"]
    assert "**T-06** Public wording of RFC 0002 work: todo" in body
    assert "D-SECRET" not in body and "Internal wording" not in body
    plan_mirror.guard(build(ws), ws, ws / "programme")


@pytest.mark.parametrize("title", ["Move notes to private/engineering", "Ask o/engineering first",
                                   "Write it in engineering", "From drafts/rfc.md", "Per D-SECRET"])
def test_private_facts_are_refused(ws, title):
    task(ws / "programme", "T-01", title)
    with pytest.raises(clikit.CliError) as e:
        plan_mirror.guard(build(ws), ws, ws / "programme")
    assert e.value.code == "mirror.private_fact"


def test_the_word_private_alone_is_not_a_private_fact(ws):
    task(ws / "programme", "T-01", "Keep private know-how out of public specs")
    plan_mirror.guard(build(ws), ws, ws / "programme")


class FakeGitHub:
    """The GitHub REST calls the mirror makes, in memory."""

    def __init__(self, issues=(), labels=()):
        self.issues, self.labels, self.calls = [dict(i) for i in issues], set(labels), []

    def api(self, method, path, payload=None):
        self.calls.append((method, path))
        if path == "user":
            return {"login": "me"}
        if path.startswith("repos/o/rfcs/labels/"):
            if path.rsplit("/", 1)[1] not in self.labels:
                raise plan_mirror.NotFound(path)
            return {"name": path.rsplit("/", 1)[1]}
        if path == "repos/o/rfcs/labels":
            self.labels.add(payload["name"])
            return payload
        if path.startswith("repos/o/rfcs/issues?"):
            return [i for i in self.issues if "tracking" in i["labels"]]
        if method == "POST" and path == "repos/o/rfcs/issues":
            n = len(self.issues) + 1
            self.issues.append({"number": n, "user": "me", "html_url": f"u/{n}", "state": "open", **payload})
            return {"number": n, "html_url": f"u/{n}"}
        if method == "PATCH":
            n = int(path.rsplit("/", 1)[1])
            issue = next(i for i in self.issues if i["number"] == n)
            issue.update(payload)
            return issue
        raise AssertionError((method, path))


def to_rest(i):
    return {"number": i["number"], "title": i["title"], "body": i["body"], "html_url": i["html_url"],
            "state": i["state"], "user": {"login": i["user"]}, "labels": [{"name": n} for n in i["labels"]]}


class RestView(FakeGitHub):
    def api(self, method, path, payload=None):
        out = super().api(method, path, payload)
        return [to_rest(i) for i in out] if path.startswith("repos/o/rfcs/issues?") else out


def test_publish_creates_then_is_idempotent_then_updates(ws):
    gh = RestView()
    doc = build(ws)
    assert [a["action"] for a in plan_mirror.publish(doc, gh)] == ["created", "created"]
    assert "tracking" in gh.labels
    assert [a["action"] for a in plan_mirror.publish(build(ws), gh)] == ["unchanged", "unchanged"]
    task(ws / "programme", "T-02", "Second step of RFC 0002", phase=3, status="done", evidence={"public/a": "aaaaaaa"})
    actions = plan_mirror.publish(build(ws), gh)
    assert [a["action"] for a in actions] == ["unchanged", "updated"]
    assert not any(m == "DELETE" for m, _ in gh.calls)


def test_publish_never_touches_issues_it_did_not_create(ws):
    marker = "<!-- odapkit plan mirror: rfc 0001 -->"
    theirs = {"number": 7, "title": "x", "body": marker + "\ncopied", "html_url": "u/7", "state": "open",
              "user": "someone", "labels": ["tracking"]}
    gh = RestView(issues=[theirs], labels=["tracking"])
    actions = plan_mirror.publish(build(ws), gh)
    assert [a["action"] for a in actions] == ["created", "created"]
    assert gh.issues[0]["body"] == marker + "\ncopied"
    assert not any(m == "PATCH" for m, _ in gh.calls)


def test_cli_dry_run_prints_issues_and_calls_nothing(ws, capsys, monkeypatch):
    monkeypatch.setattr(plan_mirror, "GitHub", lambda: pytest.fail("a dry run must not reach GitHub"))
    args = ["--programme", str(ws / "programme"), "--json", "mirror", "--workspace", str(ws), "--dry-run"]
    assert plan.main(args) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "opendap.plan-mirror/v1" and doc["dry_run"] is True
    assert [i["rfc"] for i in doc["issues"]] == ["0001", "0002"]


def test_cli_refusal_exits_1_with_code(ws, capsys):
    task(ws / "programme", "T-01", "Move notes to private/engineering")
    args = ["--programme", str(ws / "programme"), "--json", "mirror", "--workspace", str(ws), "--dry-run"]
    assert plan.main(args) == 1
    assert '"code": "mirror.private_fact"' in capsys.readouterr().out


def test_cli_without_config_is_a_usage_error(ws):
    (ws / "programme" / "mirror.toml").unlink()
    assert plan.main(["--programme", str(ws / "programme"), "mirror", "--workspace", str(ws), "--dry-run"]) == 2
