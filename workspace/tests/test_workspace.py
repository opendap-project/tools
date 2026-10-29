"""odw.workspace: the one lookup of the workspace root."""
from odw import workspace


def test_finds_the_nearest_repos_tsv(tmp_path, monkeypatch):
    monkeypatch.delenv("ODAP_WORKSPACE_DIR", raising=False)
    (tmp_path / "repos.tsv").write_text("path\trepository\n")
    deep = tmp_path / "public" / "x" / "y"
    deep.mkdir(parents=True)
    assert workspace.root(deep) == tmp_path.resolve()
    assert workspace.state_dir(deep) == tmp_path.resolve() / ".state"


def test_none_outside_a_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ODAP_WORKSPACE_DIR", raising=False)
    assert workspace.root(tmp_path) is None


def test_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ODAP_WORKSPACE_DIR", str(tmp_path))
    assert workspace.root("/") == tmp_path
