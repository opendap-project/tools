"""repo_dir of lib/odapkit.sh and bin/odw-security-check: a repository merged into the blackbird root (RFC 0017)
is only its folder there, under ODAP_BLACKBIRD_DIR or the root's repos.tsv row (HL-84); the old variable and row
are not read any more (HL-91).

Run from the repository root: python3 -m unittest tests.test_repo_lookup
"""
import os
import subprocess
import sys
import tempfile
import unittest

from tests.test_host_lookup import ENV, ROOT, fake_host, git_init

ENV = {k: v for k, v in ENV.items() if not (k.startswith("ODAP_") and k.endswith("_DIR"))}
PROBE = '#!/bin/sh\n. "$(cd "$(dirname "$0")/.." && pwd)/lib/odapkit.sh"\nrepo_dir "$1"\n'


def workspace(tmp, rows, folders):
    """public/tools/image with this checkout's lookups, a fake host/ beside it, and the given rows and folders."""
    ws = os.path.join(tmp, "ws")
    mine = os.path.join(ws, "public", "tools", "image")
    for rel in ("lib/odapkit.sh", "bin/odw-security-check"):
        os.makedirs(os.path.dirname(os.path.join(mine, rel)), exist_ok=True)
        with open(os.path.join(ROOT, rel)) as src, open(os.path.join(mine, rel), "w") as dst:
            dst.write(src.read())
    with open(os.path.join(mine, "bin", "odapkit-probe"), "w") as f:
        f.write(PROBE)
    os.chmod(os.path.join(mine, "bin", "odapkit-probe"), 0o755)
    fake_host(os.path.join(ws, "public", "tools", "host"), "host")
    git_init(os.path.join(ws, "public", "tools"))
    for d in folders:
        os.makedirs(os.path.join(ws, d), exist_ok=True)
    with open(os.path.join(ws, "repos.tsv"), "w") as f:
        f.write("path\trepository\npublic/tools\to/tools\n" + "".join(f"{p}\to/{n}\n" for p, n in rows))
    return ws, mine


def found(tmp, rows, folders, name="blackbird-os", env=None):
    """What each lookup returns: (shell, python); None when it finds nothing."""
    ws, mine = workspace(tmp, rows, folders)
    env = {**ENV, **(env or {})}
    sh = subprocess.run([os.path.join(mine, "bin", "odapkit-probe"), name], capture_output=True, text=True, cwd=mine,
                        env=env, stdin=subprocess.DEVNULL, timeout=60)
    py = subprocess.run([sys.executable, "-c", "import importlib.machinery as m, sys; mod = m.SourceFileLoader('sc', "
                         "'bin/odw-security-check').load_module(); print(mod.repo_dir(sys.argv[1]) or '')", name],
                        capture_output=True, text=True, cwd=mine, env=env, stdin=subprocess.DEVNULL, timeout=60)
    out = [p.stdout.strip() if p.returncode == 0 and p.stdout.strip().startswith("/") else None for p in (sh, py)]
    return [os.path.relpath(os.path.realpath(o), os.path.realpath(ws)) if o else None for o in out]


class RepoDir(unittest.TestCase):
    def test_a_repository_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = found(tmp, [("public/hats", "hats")], ["public/hats"], "hats")
            self.assertEqual(got, ["public/hats"] * 2)

    def test_a_merged_repository_ignores_its_old_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = found(tmp, [("public/blackbird-os", "blackbird-os")], ["public/blackbird-os"])
            self.assertEqual(got, [None, None])

    def test_a_merged_repository_through_its_root_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = found(tmp, [("public/blackbird", "blackbird")], ["public/blackbird/os"])
            self.assertEqual(got, ["public/blackbird/os"] * 2)

    def test_a_merged_repository_through_the_root_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = found(tmp, [], ["other/os"], env={"ODAP_BLACKBIRD_DIR": os.path.join(tmp, "ws", "other")})
            self.assertEqual(got, ["other/os"] * 2)

    def test_a_merged_repository_ignores_its_old_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = found(tmp, [("public/blackbird", "blackbird")], ["public/blackbird/os", "mine"],
                        env={"ODAP_BLACKBIRD_OS_DIR": os.path.join(tmp, "ws", "mine")})
            self.assertEqual(got, ["public/blackbird/os"] * 2)

    def test_an_unknown_repository_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(found(tmp, [("public/blackbird", "blackbird")], ["public/blackbird/os"], "nope"),
                             [None, None])


if __name__ == "__main__":
    unittest.main()
