"""HL-72: the plugins are odw-<tool> (command standard §1); each odapkit-<tool> stays for one minor release as a
link to it, declared as the entry's `replaces`, so every call by the old path keeps working."""
import os
import pathlib
import subprocess
import tomllib

import pytest
from test_cli_conformance import by_name, repo

from odw import audit, cli_inventory

HOST = pathlib.Path(__file__).resolve().parents[1]
TOP = HOST.parent
# The folders renamed so far; image and verify follow once their open identifier work has landed (HL-72).
FOLDERS = ["hardware", "hooks", "host"]
merged = pytest.mark.skipif(not (TOP / "hardware" / "tools.toml").is_file(), reason="not the merged tools root")


def tools():
    for folder in FOLDERS:
        for t in tomllib.loads((TOP / folder / "tools.toml").read_text())["tool"]:
            yield folder, t


@merged
def test_every_entry_is_an_odw_plugin_that_replaces_its_odapkit_name():
    for folder, t in tools():
        assert t["entry"] == f"bin/odw-{t['name']}", (folder, t["name"])
        assert t.get("replaces") == f"odapkit-{t['name']}", (folder, t["name"])
        assert (TOP / folder / t["entry"]).is_file() and os.access(TOP / folder / t["entry"], os.X_OK)


@merged
def test_every_old_name_is_a_tracked_link_to_the_plugin():
    modes = cli_inventory.tracked_modes(TOP)
    for folder, t in tools():
        old = f"{folder}/bin/odapkit-{t['name']}"
        assert modes.get(old) == "120000", old
        assert os.readlink(TOP / old) == f"odw-{t['name']}", old
    # Nothing else keeps the old prefix: the umbrella alias odapkit is the only other odapkit in a bin/.
    stray = [p for p, m in modes.items() if p.split("/")[0] in FOLDERS and "/bin/odapkit" in p and m != "120000"
             and not p.endswith("bin/odapkit")]
    assert stray == []


@merged
@pytest.mark.parametrize("name", ["plan", "sync", "cli"])
def test_the_old_path_runs_the_plugin_and_its_help_names_odw(name):
    for exe in (f"odw-{name}", f"odapkit-{name}"):
        r = subprocess.run([str(HOST / "bin" / exe), "--help"], capture_output=True, text=True,
                           env=audit.git_env(), stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        assert r.stdout.splitlines()[0].startswith(f"odw {name}: "), exe


def test_the_inventory_skips_links(tmp_path):
    """A link is another name for an executable the inventory already lists; its target is what is checked."""
    (tmp_path / "repos.tsv").write_text("path\trepository\npublic/tools\to/t\n")
    repo(tmp_path, "public/tools", {"bin/odw-x": ('#!/bin/sh\ncase "${1:-}" in --help) echo "odw x: x";; esac\n', 0o755)})
    top = tmp_path / "public/tools"
    (top / "bin/odapkit-x").symlink_to("odw-x")
    subprocess.run(["git", "add", "bin/odapkit-x"], cwd=top, check=True, env=audit.git_env())
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "link"], cwd=top, check=True,
                   env=audit.git_env())
    assert set(by_name(cli_inventory.inventory(tmp_path))) == {"odw-x"}
