"""The toolkit-host lookups of this repository's tools in both toolkit layouts (F2 of the tools cut).

Run from the repository root: python3 -m unittest tests.test_host_lookup
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a fixture's git would act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Under a pre-commit hook git exports GIT_DIR and others; neither git nor the tools may see them.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("ODAP_TOOLKIT_HOST_DIR", "ODAP_TOOLS_DIR")}
FAKE_SH = 'cli_fail() { echo "helpers of $FAKE_HOST"; exit 0; }\n'


def fake_host(d, name):
    """A toolkit-host checkout whose helpers say which one they are."""
    os.makedirs(os.path.join(d, "lib"))
    os.makedirs(os.path.join(d, "src", "odw"))
    with open(os.path.join(d, "lib", "cli.sh"), "w") as f:
        f.write(f"FAKE_HOST={name}\n" + FAKE_SH)
    with open(os.path.join(d, "src", "odw", "clikit.py"), "w") as f:
        f.write(f"MARK = {name!r}\n")


def git_init(d):
    subprocess.run(["git", "init", "-q", d], check=True, env=ENV)


def build(tmp, layout, folder, files, host=True):
    """A workspace in one of the two toolkit layouts; returns the folder that holds this checkout's files.

    today: public/toolkit/host and public/toolkit/<folder>, each a repository listed in repos.tsv.
    merged: public/tools, one repository whose folders are host/ and <folder>/, the only row of repos.tsv.
    """
    ws = os.path.join(tmp, "ws")
    if layout == "today":
        base, rows = os.path.join(ws, "public", "toolkit"), ["public/toolkit/host\to/toolkit-host",
                                                             f"public/toolkit/{folder}\to/toolkit-{folder}"]
    else:
        base, rows = os.path.join(ws, "public", "tools"), ["public/tools\to/tools"]
    mine = os.path.join(base, folder)
    for rel in files:
        os.makedirs(os.path.dirname(os.path.join(mine, rel)), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, rel), os.path.join(mine, rel))
    if host:
        fake_host(os.path.join(base, "host"), "host")
    if layout == "today":
        git_init(mine)
        if host:
            git_init(os.path.join(base, "host"))
    else:
        git_init(base)
    with open(os.path.join(ws, "repos.tsv"), "w") as f:
        f.write("path\trepository\n" + "\n".join(rows) + "\n")
    fake_host(os.path.join(tmp, "elsewhere"), "elsewhere")
    return mine


def run(argv, cwd, env=None):
    return subprocess.run(argv, capture_output=True, text=True, cwd=cwd, env={**ENV, **(env or {})},
                          stdin=subprocess.DEVNULL, timeout=60)

FOLDER = "hooks"
FILES = ['lib/cli.sh']


def probe_sh(mine, env):
    """The helpers lib/cli.sh sourced, by a probe tool next to it; None for its own minimal fallback."""
    exe = os.path.join(mine, "bin", "odapkit-probe")
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    with open(exe, "w") as f:
        f.write('#!/bin/sh\n. "$(cd "$(dirname "$0")/.." && pwd)/lib/cli.sh"\ncli_fail probe.none "no helpers" - -\n')
    os.chmod(exe, 0o755)
    out = run([exe], mine, env).stdout.strip()
    return out.removeprefix("helpers of ") if out else None


class ShellHelpers(unittest.TestCase):
    """lib/cli.sh: host/ of the same repository first (the merged tools root), then
    $ODAP_TOOLS_DIR/host, then ODAP_TOOLKIT_HOST_DIR (deprecated), then
    the toolkit-host row of repos.tsv. Built in both toolkit layouts."""

    def found(self, layout, env=False, host=True, tools=False):
        with tempfile.TemporaryDirectory() as tmp:
            mine = build(tmp, layout, FOLDER, FILES, host)
            extra = {"ODAP_TOOLKIT_HOST_DIR": os.path.join(tmp, "elsewhere")} if env else {}
            if tools:  # ODAP_TOOLS_DIR: the tools root, whose host/ holds the helpers (HL-71)
                fake_host(os.path.join(tmp, "troot", "host"), "troot")
                extra["ODAP_TOOLS_DIR"] = os.path.join(tmp, "troot")
            return probe_sh(mine, extra)

    def test_today_reads_the_toolkit_host_row(self):
        self.assertEqual(self.found("today"), "host")

    def test_today_the_variable_moves_it(self):
        self.assertEqual(self.found("today", env=True), "elsewhere")

    def test_merged_finds_host_beside_it(self):
        self.assertEqual(self.found("merged"), "host")

    def test_merged_host_beside_it_comes_before_the_variable(self):
        self.assertEqual(self.found("merged", env=True), "host")

    def test_merged_without_host_takes_the_variable(self):
        self.assertEqual(self.found("merged", env=True, host=False), "elsewhere")

    def test_merged_without_host_or_variable_finds_none(self):
        self.assertIsNone(self.found("merged", host=False))

    def test_merged_without_host_takes_the_tools_root(self):
        self.assertEqual(self.found("merged", host=False, tools=True), "troot")

    def test_the_tools_root_comes_before_the_deprecated_variable(self):
        self.assertEqual(self.found("merged", env=True, host=False, tools=True), "troot")

    def test_merged_host_beside_it_comes_before_the_tools_root(self):
        self.assertEqual(self.found("merged", host=True, tools=True), "host")


if __name__ == "__main__":
    unittest.main()
