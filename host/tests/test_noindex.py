"""odw noindex: generated trees become `<name>.noindex` (Spotlight skips them) behind a `<name>` symlink."""
import os

import pytest

from odw import noindex


MARKER = {"target": "CACHEDIR.TAG", ".venv": "pyvenv.cfg", "node_modules": ".modules.yaml"}


@pytest.fixture(autouse=True)
def no_build_running(monkeypatch):
    """Other sessions' builds must not make these tests skip."""
    monkeypatch.setattr(noindex, "busy", lambda: [])


def make(tmp_path, *paths, marker=True):
    for p in paths:
        d = tmp_path / p
        d.mkdir(parents=True, exist_ok=True)
        (d / "f").write_text("x")
        for part in d.relative_to(tmp_path).parts:
            if marker and part in MARKER:
                top = tmp_path / p.split(part)[0] / part
                (top / MARKER[part]).write_text("")


def test_finds_generated_trees_and_skips_their_insides(tmp_path):
    make(tmp_path, "a/target/debug", "b/.venv/lib", "c/src", "d/node_modules/x/node_modules")
    found = sorted(p.relative_to(tmp_path).as_posix() for p in noindex.candidates(tmp_path, names=noindex.DEFAULT))
    assert found == ["a/target", "b/.venv"]


def test_node_modules_only_when_asked(tmp_path):
    make(tmp_path, "d/node_modules/x")
    found = [p.name for p in noindex.candidates(tmp_path, names=noindex.DEFAULT | {"node_modules"})]
    assert found == ["node_modules"]


def test_skips_git_and_existing_noindex(tmp_path):
    make(tmp_path, ".git/target", "e/target.noindex")
    os.symlink("target.noindex", tmp_path / "e" / "target")
    assert list(noindex.candidates(tmp_path, names=noindex.DEFAULT)) == []


def test_apply_moves_and_links(tmp_path):
    make(tmp_path, "a/target/debug")
    changed = noindex.apply(tmp_path, names=noindex.DEFAULT, dry_run=False)
    t = tmp_path / "a" / "target"
    assert changed == [t]
    assert t.is_symlink() and os.readlink(t) == "target.noindex"
    assert (tmp_path / "a" / "target.noindex" / "debug" / "f").read_text() == "x"
    assert (t / "debug" / "f").read_text() == "x"


def test_dry_run_changes_nothing(tmp_path):
    make(tmp_path, "a/target")
    assert noindex.apply(tmp_path, names=noindex.DEFAULT, dry_run=True) == [tmp_path / "a" / "target"]
    assert not (tmp_path / "a" / "target").is_symlink()


def test_apply_is_idempotent(tmp_path):
    make(tmp_path, "a/target")
    noindex.apply(tmp_path, names=noindex.DEFAULT, dry_run=False)
    assert noindex.apply(tmp_path, names=noindex.DEFAULT, dry_run=False) == []


def test_refuses_when_target_noindex_already_exists(tmp_path):
    make(tmp_path, "a/target", "a/target.noindex")
    with pytest.raises(noindex.NoindexError):
        noindex.apply(tmp_path, names=noindex.DEFAULT, dry_run=False)


def test_cli_lists_then_applies(tmp_path, capsys):
    make(tmp_path, "a/target")
    assert noindex.main([str(tmp_path)]) == 0
    assert "a/target" in capsys.readouterr().out
    assert not (tmp_path / "a" / "target").is_symlink()
    assert noindex.main(["--apply", str(tmp_path)]) == 0
    assert (tmp_path / "a" / "target").is_symlink()


def test_tree_without_its_marker_is_not_generated(tmp_path):
    make(tmp_path, "a/target", "b/build/ios/PIFCache/target", marker=False)
    assert list(noindex.candidates(tmp_path, names=noindex.DEFAULT)) == []


def test_never_descends_into_node_modules_unless_linking_it(tmp_path):
    make(tmp_path, "web/node_modules/pkg/target")
    assert list(noindex.candidates(tmp_path, names=noindex.DEFAULT)) == []


def test_check_fails_while_a_tree_is_unlinked(tmp_path, capsys, monkeypatch):
    make(tmp_path, "target/debug")
    assert noindex.main(["--check", str(tmp_path)]) == 1
    assert "not linked" in capsys.readouterr().err
    assert noindex.main(["--apply", str(tmp_path)]) == 0
    assert noindex.main(["--check", str(tmp_path)]) == 0


def test_apply_waits_while_a_build_runs(tmp_path, capsys, monkeypatch):
    make(tmp_path, "target/debug")
    monkeypatch.setattr(noindex, "busy", lambda: ["cargo"])
    assert noindex.main(["--apply", str(tmp_path)]) == 0
    assert "skipped" in capsys.readouterr().out
    assert not (tmp_path / "target").is_symlink()
    assert noindex.main(["--apply", "--force", str(tmp_path)]) == 0
    assert (tmp_path / "target").is_symlink()
