import json

from odw import plan, schemas

HOMES = """\
schema = "opendap.homes/v1"

[[rule]]
id = "worktrees"
rule = "One worktree per task and repository."
homes = [{ path = "methods/coordination.md", contains = "worktree per task" }]

[[rule]]
id = "budgets"
rule = "Functions stay under 80 lines."
homes = []

[[rule]]
id = "moved"
rule = "A home that lost the rule."
homes = [{ path = "CONTRIBUTING.md", contains = "never written there" }]

[[rule]]
id = "naming"
rule = "Names come from the hierarchy."
pending = "D-NAMING"
"""


def make(tmp_path):
    ws = tmp_path
    (ws / "repos.tsv").write_text("path\trepository\n")
    (ws / "methods").mkdir()
    (ws / "methods/coordination.md").write_text("Use one Worktree  per task,\nalways.\n")
    (ws / "CONTRIBUTING.md").write_text("Rules.\n")
    p = ws / "programme"
    (p / "tasks").mkdir(parents=True)
    (p / "decisions.toml").write_text('schema = "opendap.decisions/v1"\n')
    (p / "homes.toml").write_text(HOMES)
    return ws, p


def test_homes_lists_rules_only_in_the_programme_and_broken_homes(tmp_path):
    ws, p = make(tmp_path)
    found = plan.Programme(p, claims_dir=tmp_path / "c").homes(ws)
    assert {(f["rule"], f["problem"]) for f in found} == {("budgets", "no home"), ("moved", "home lacks the rule")}


def test_a_pending_rule_waits_for_its_decision(tmp_path):
    ws, p = make(tmp_path)
    found = plan.Programme(p, claims_dir=tmp_path / "c").homes(ws)
    assert "naming" not in {f["rule"] for f in found}


def test_homes_command_and_schema(tmp_path, capsys):
    ws, p = make(tmp_path)
    import tomllib
    assert schemas.errors(tomllib.loads(HOMES), "homes") == []
    assert plan.main(["--programme", str(p), "--claims", str(tmp_path / "c"), "--json", "homes", "--workspace", str(ws)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert {f["rule"] for f in out} == {"budgets", "moved"}
