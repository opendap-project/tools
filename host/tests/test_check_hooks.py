"""odw check hooks (HL-103): pre-commit and pre-push run `odw check` for the stages each root's check.toml declares,
rendered by odw sync (a generated block) and by odw rename config (a cut root's one configuration)."""
import os
import shutil
import subprocess

import pytest
import test_rename_config as trc

from odw import check_hooks, rename_precommit as rp, sync

FAST_FULL = 'schema = "opendap.check/v1"\n[[stage.fast]]\nname = "fmt"\nrun = ["true"]\n' \
            '[[stage.full]]\nname = "test"\nrun = ["true"]\n'
FULL = 'schema = "opendap.check/v1"\n[[stage.full]]\nname = "test"\nrun = ["true"]\n'
RELEASE = 'schema = "opendap.check/v1"\n[[stage.release]]\nname = "sign"\nrun = ["true"]\n'


def write(top, files):
    for name, text in files.items():
        (top / name).parent.mkdir(parents=True, exist_ok=True)
        (top / name).write_text(text)
    return sorted(files)


def test_roots_are_the_folders_with_a_check_toml_and_their_declared_stages(tmp_path):
    files = write(tmp_path, {"check.toml": FAST_FULL, "system/check.toml": FULL, "emulator/check.toml": RELEASE,
                             "tests/fixtures/check/check.toml": FAST_FULL, "README.md": "x\n"})
    assert check_hooks.roots(tmp_path, files) == {"": ["fast", "full"], "system": ["full"]}


def test_a_root_at_the_top_runs_odw_check_on_the_changed_paths(tmp_path):
    cfg = rp.parse("repos:\n" + check_hooks.body({"": ["fast", "full"]}))
    fast, full = cfg.hook("odw-check-fast"), cfg.hook("odw-check-full")
    assert fast.get("entry") == "odw check --stage fast" and fast.get("stages") is None
    assert full.get("entry") == "odw check --stage full" and full.get("stages") == "[pre-push]"
    for h in (fast, full):
        assert h.get("language") == "system" and h.get("require_serial") == "true"
        assert h.get("pass_filenames") is None and h.get("files") is None  # odw check selects by `paths`


def test_a_root_in_a_folder_runs_inside_it(tmp_path):
    cfg = rp.parse("repos:\n" + check_hooks.body({"system": ["full"], "a/b": ["fast"]}))
    sysfull = cfg.hook("odw-check-full-system")
    assert sysfull.get("files") == "^system/" and sysfull.get("entry").startswith("sh -c 'cd system && ")
    assert cfg.hook("odw-check-fast-a-b").get("files") == "^a/b/"
    assert cfg.hook("odw-check-fast") is None  # only the stages each root declares


@pytest.mark.parametrize("folder,up", [("system", "../"), ("a/b", "../../")])
def test_a_folder_hook_gives_the_paths_back_relative_to_the_folder(tmp_path, folder, up):
    """The entry, run as pre-commit runs it (the repository's paths as arguments), calls odw inside the folder."""
    (tmp_path / folder).mkdir(parents=True)
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "odw").write_text('#!/bin/sh\npwd > "$OUT"\nfor a; do echo "$a"; done >> "$OUT"\n')
    (fake / "odw").chmod(0o755)
    entry = rp.parse("repos:\n" + check_hooks.body({folder: ["fast"]})).hooks()[0].get("entry")
    out = tmp_path / "out.txt"
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "OUT": str(out)}
    r = subprocess.run(["sh", "-c", f'{entry} "$@"', "sh", f"{folder}/src/a b.rs", "README.md"], cwd=tmp_path,
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lines = out.read_text().splitlines()
    assert os.path.realpath(lines[0]) == os.path.realpath(tmp_path / folder)
    assert lines[1:] == ["check", "--stage", "fast", f"{up}{folder}/src/a b.rs", f"{up}README.md"]


SYNC = """schema = "opendap.sync/v1"

[[block]]
target = ".pre-commit-config.yaml"
repos = ["public/*"]
place = "end"

[[block.fragment]]
generator = "odw-check"
"""
CONFIG = "# Local checks.\nrepos:\n  - repo: local\n    hooks:\n      - id: own\n        name: own\n" \
         "        entry: 'true'\n        language: system\n"


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/a\to/a\npublic/b\to/b\npublic/c\to/c\n")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates/sync.toml").write_text(SYNC)
    for r, files in {"a": {".pre-commit-config.yaml": CONFIG, "check.toml": FAST_FULL},
                     "b": {".pre-commit-config.yaml": CONFIG},
                     "c": {"check.toml": FULL}}.items():
        top = tmp_path / "public" / r
        top.mkdir(parents=True)
        write(top, files)
        trc.git(top, "init", "-q")
        trc.git(top, "add", "-A")
        trc.git(top, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    return tmp_path


def _config_drift(ws):
    """The drift of the configurations (sync also reports git hooks a fixture never installs)."""
    return [(d.repo, d.target, d.state) for d in sync.check(ws, ws / "templates") if d.template == "block"]


def test_sync_appends_the_check_hooks_where_a_check_toml_is(ws):
    drift = _config_drift(ws)
    assert drift == [("public/a", ".pre-commit-config.yaml", "no-block")]  # b has no check.toml; c no config
    sync.apply(ws, ws / "templates")
    text = (ws / "public/a/.pre-commit-config.yaml").read_text()
    assert text.startswith(CONFIG) and text.rstrip().endswith("# <<< odapkit sync")
    cfg = rp.parse(text)
    assert [h.get("id") for h in cfg.hooks()] == ["own", "odw-check-fast", "odw-check-full"]
    assert _config_drift(ws) == []
    assert not (ws / "public/c/.pre-commit-config.yaml").exists()


def test_sync_replaces_the_block_in_place_when_the_stages_change(ws):
    sync.apply(ws, ws / "templates")
    (ws / "public/a/check.toml").write_text(FULL)
    assert [d[2] for d in _config_drift(ws)] == ["differs"]
    sync.apply(ws, ws / "templates")
    text = (ws / "public/a/.pre-commit-config.yaml").read_text()
    assert text.count(">>> odapkit sync") == 1 and "odw-check-fast" not in text and "odw-check-full" in text


@pytest.mark.skipif(shutil.which("pre-commit") is None, reason="pre-commit is not installed")
def test_the_synced_config_is_valid_for_pre_commit(ws):
    sync.apply(ws, ws / "templates")
    r = subprocess.run(["pre-commit", "validate-config", str(ws / "public/a/.pre-commit-config.yaml")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def _synced(config, roots):
    begin, end = sync._markers({})
    return config + f"{begin}\n{check_hooks.body(roots)}{end}\n"


def test_rename_config_renders_one_block_for_the_cut_root(tmp_path, capsys, monkeypatch):
    """Each source brought its own synced block (a root-level check); the cut root gets one, per folder."""
    landings, folders = trc.CUTS["tools"]
    folders = {**folders,
               "host": {**folders["host"], "check.toml": FAST_FULL,
                        ".pre-commit-config.yaml": _synced(trc.HOST, {"": ["fast", "full"]})},
               "image": {**folders["image"], "check.toml": FULL,
                         ".pre-commit-config.yaml": _synced(trc.IMAGE, {"": ["full"]})}}
    monkeypatch.setitem(trc.CUTS, "tools", (landings, folders))
    ws, top = trc.make_root(tmp_path, "tools")
    code, _, err = trc.run(capsys, "tools", ws, top)
    assert code == 0, err
    text = (top / ".pre-commit-config.yaml").read_text()
    assert text.count(">>> odapkit sync") == 1 and text.rstrip().endswith("# <<< odapkit sync")
    cfg = rp.parse(text)
    ids = [h.get("id") for h in cfg.hooks()]
    assert [i for i in ids if i.startswith("odw-check")] == ["odw-check-fast-host", "odw-check-full-host",
                                                             "odw-check-full-image"]
    assert cfg.hook("odw-check-full-image").get("files") == "^image/"
    # The block is what odw sync renders for the cut root: nothing to repair after the cut.
    assert text.endswith(_synced("", check_hooks.roots(top, trc.tracked(top))))
