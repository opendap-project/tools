"""odw plan: readiness, consistency checks, claims, recording and the rendered index."""
import json
import textwrap

import pytest

from odw import plan

TASK = """\
id = "{id}"
phase = {phase}
title = "{title}"
source = "test"
repos = []
files = []
depends_on = {deps}
blocked_by = {blocked}
status = "{status}"
visibility = "private"
tests = []
docs = {docs}
evidence = {evidence}
notes = ""
"""


def task(root, id, phase=0, deps=(), blocked=(), status="todo", docs=(), evidence=None):
    ev = "{ " + ", ".join(f'"{k}" = "{v}"' for k, v in (evidence or {}).items()) + " }" if evidence else "{}"
    (root / "tasks" / f"{id}.toml").write_text(TASK.format(
        id=id, phase=phase, title=f"Task {id}", deps=json.dumps(list(deps)), blocked=json.dumps(list(blocked)),
        status=status, docs=json.dumps(list(docs)), evidence=ev))


@pytest.fixture
def prog(tmp_path):
    root = tmp_path / "programme"
    (root / "tasks").mkdir(parents=True)
    (root / "decisions.toml").write_text(textwrap.dedent("""\
        [[decision]]
        id = "D-OPEN"
        question = "Open question?"
        status = "open"

        [[decision]]
        id = "D-DONE"
        question = "Decided question?"
        status = "decided"
        decision = "yes"
        decided_on = "2026-10-26"
        decided_by = "maintainers"
        """))
    task(root, "AA-01", status="done", docs=["README.md"], evidence={"repo": "abc1234"})
    task(root, "AA-02", deps=["AA-01"])
    task(root, "AA-03", deps=["AA-02"])
    task(root, "AA-04", blocked=["D-OPEN"])
    task(root, "AA-05", blocked=["D-DONE"], phase=-1)
    claims = tmp_path / "claims"
    return plan.Programme(root, claims_dir=claims)


def test_ready_needs_done_deps_and_decided_blockers(prog):
    assert [t["id"] for t in prog.ready()] == ["AA-05", "AA-02"]


def test_next_is_lowest_phase_ready(prog):
    assert prog.next()["id"] == "AA-05"


def test_check_passes_on_a_consistent_programme(prog):
    assert prog.check() == []


def test_check_reports_unknown_dependency_and_decision(prog):
    task(prog.root, "AA-06", deps=["ZZ-99"], blocked=["D-NOPE"])
    problems = prog.check()
    assert any("ZZ-99" in p for p in problems)
    assert any("D-NOPE" in p for p in problems)


def test_check_reports_cycles(prog):
    task(prog.root, "AA-07", deps=["AA-08"])
    task(prog.root, "AA-08", deps=["AA-07"])
    assert any("cycle" in p for p in prog.check())


def test_check_reports_schema_errors(prog):
    task(prog.root, "AA-09", status="done")
    assert any("AA-09" in p and "evidence" in p for p in prog.check())


def test_check_reports_file_name_mismatch(prog):
    (prog.root / "tasks" / "AA-02.toml").rename(prog.root / "tasks" / "AA-20.toml")
    assert any("AA-20.toml" in p for p in prog.check())


def test_claim_sets_doing_and_writes_the_claim(prog):
    prog.claim("AA-02", session="s1", hours=2)
    assert prog.task("AA-02")["status"] == "doing"
    assert (prog.claims_dir / "AA-02.toml").is_file()


def test_claim_refuses_a_live_claim_of_another_session(prog):
    prog.claim("AA-02", session="s1", hours=2)
    with pytest.raises(plan.PlanError):
        prog.claim("AA-02", session="s2", hours=2)


def test_expired_claim_can_be_taken(prog):
    prog.claim("AA-02", session="s1", hours=-1)
    prog.claim("AA-02", session="s2", hours=2)
    assert "s2" in (prog.claims_dir / "AA-02.toml").read_text()


def test_claim_refuses_a_task_that_is_not_ready(prog):
    with pytest.raises(plan.PlanError):
        prog.claim("AA-03", session="s1", hours=2)
    with pytest.raises(plan.PlanError):
        prog.claim("AA-04", session="s1", hours=2)


def test_done_records_evidence_and_releases_the_claim(prog):
    prog.claim("AA-02", session="s1", hours=2)
    prog.done("AA-02", evidence={"public/x": "def5678"}, docs=["docs/x.md"], tests=["tests/test_x.py"])
    t = prog.task("AA-02")
    assert t["status"] == "done" and t["evidence"] == {"public/x": "def5678"}
    assert t["docs"] == ["docs/x.md"] and t["tests"] == ["tests/test_x.py"]
    assert not (prog.claims_dir / "AA-02.toml").exists()
    assert prog.check() == []


def test_done_refuses_without_evidence(prog):
    with pytest.raises(plan.PlanError):
        prog.done("AA-02", evidence={}, docs=["x"], tests=[])


def test_written_record_keeps_the_field_order(prog):
    prog.claim("AA-02", session="s1", hours=2)
    keys = [line.split(" = ")[0] for line in (prog.root / "tasks" / "AA-02.toml").read_text().splitlines()]
    assert keys == list(plan.FIELDS)


def test_render_and_check_index(prog):
    out = prog.render()
    assert out.startswith("<!-- @generated")
    assert "| AA-02 |" in out and "ready" in out
    prog.write_index()
    assert prog.index_is_current()
    task(prog.root, "AA-10")
    assert not prog.index_is_current()


def test_cli_list_json(prog, capsys):
    rc = plan.main(["--programme", str(prog.root), "--claims", str(prog.claims_dir), "list", "--ready", "--json"])
    assert rc == 0
    assert [t["id"] for t in json.loads(capsys.readouterr().out)] == ["AA-05", "AA-02"]


def test_cli_check_exit_status(prog):
    args = ["--programme", str(prog.root), "--claims", str(prog.claims_dir), "check"]
    assert plan.main(args) == 0
    task(prog.root, "AA-11", deps=["ZZ-01"])
    assert plan.main(args) == 1


def test_cli_usage_error_is_2(prog):
    assert plan.main(["--programme", str(prog.root), "nonsense"]) == 2


def test_done_keeps_every_repeated_evidence_and_docs_flag(prog, monkeypatch):
    root, claims = prog.root, prog.claims_dir
    task(root, "XX-01", docs=())
    monkeypatch.setenv("ODAP_SESSION", "s")
    assert plan.main(["--programme", str(root), "--claims", str(claims), "claim", "XX-01"]) == 0
    assert plan.main(["--programme", str(root), "--claims", str(claims), "done", "XX-01",
                      "--evidence", "a=1111111", "--evidence", "b=2222222", "--docs", "d1", "--docs", "d2"]) == 0
    t = plan.Programme(root, claims_dir=claims).task("XX-01")
    assert t["evidence"] == {"a": "1111111", "b": "2222222"} and t["docs"] == ["d1", "d2"]
