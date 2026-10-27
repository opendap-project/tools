"""Tests for hooks/kicad-dru.py on the fixture in tests/fixtures/dru, and for bin/odapkit-dru, the same engine
reached as `odapkit dru` (the one DRU test suite: the hook and the tool never disagree).

Run from the repository root: python3 -m unittest discover tests
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "hooks", "kicad-dru.py")
WRAPPER = os.path.join(HERE, "..", "bin", "odapkit-dru")
TOOLS_TOML = os.path.join(HERE, "..", "tools.toml")
FIXTURE = os.path.join(HERE, "fixtures", "dru")
MANIFEST = "hardware/demo.dru.toml"
TARGET = "hardware/demo.kicad_dru"
PRODUCT = "hardware/rules/product.kicad_dru"
# Under a pre-commit hook git exports GIT_DIR, GIT_INDEX_FILE and others; the tests' own repositories must never
# see them, or their git commands would act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]


def read(path):
    with open(path) as f:
        return f.read()


class KicadDru(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        shutil.copytree(FIXTURE, self.repo, ignore=shutil.ignore_patterns("expected.kicad_dru"))
        subprocess.run(["git", "init", "-q", self.repo], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_tool(self, *args):
        return subprocess.run([sys.executable, TOOL, *args], cwd=self.repo, capture_output=True, text=True)

    def path(self, rel):
        return os.path.join(self.repo, rel)

    def test_compose_matches_expected(self):
        r = self.run_tool("compose", MANIFEST)
        self.assertEqual(r.returncode, 0, r.stderr)
        want = open(os.path.join(FIXTURE, "expected.kicad_dru")).read()
        self.assertEqual(open(self.path(TARGET)).read(), want)
        self.assertEqual(want.count("(version 1)"), 1)

    def test_check_missing_then_ok_then_stale(self):
        r = self.run_tool("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing", r.stderr)
        self.run_tool("compose", MANIFEST)
        r = self.run_tool("check")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.path(PRODUCT), "a") as f:
            f.write('(rule "Product: extra"\n\t(constraint track_width (min 0.2mm))\n)\n')
        r = self.run_tool("check", "--json")
        self.assertEqual(r.returncode, 1)
        report = json.loads(r.stdout)
        self.assertEqual(report["items"][0]["status"], "stale")

    def test_hand_edit_is_stale(self):
        self.run_tool("compose", MANIFEST)
        with open(self.path(TARGET), "a") as f:
            f.write("# hand edit\n")
        self.assertEqual(self.run_tool("check", MANIFEST).returncode, 1)

    def test_duplicate_rule_name_fails(self):
        with open(self.path(PRODUCT), "a") as f:
            f.write('(rule "Fab: via hole"\n\t(constraint hole_size (min 0.3mm))\n)\n')
        r = self.run_tool("compose", MANIFEST)
        self.assertEqual(r.returncode, 1)
        self.assertIn('duplicate rule name "Fab: via hole"', r.stderr)
        self.assertFalse(os.path.exists(self.path(TARGET)))

    def test_missing_source_fails(self):
        os.remove(self.path("hardware/lib/opendap/design-rules/fab-demo/fab-demo.kicad_dru"))
        r = self.run_tool("compose", MANIFEST)
        self.assertEqual(r.returncode, 1)
        self.assertIn("submodule", r.stderr)

    def test_bad_manifest_fails(self):
        with open(self.path(MANIFEST), "w") as f:
            f.write('version = 1\nsources = ["../outside.kicad_dru"]\n')
        r = self.run_tool("compose", MANIFEST)
        self.assertEqual(r.returncode, 1)
        self.assertIn("inside the repository", r.stderr)

    def test_unbalanced_source_fails(self):
        with open(self.path(PRODUCT), "a") as f:
            f.write('(rule "Product: broken"\n\t(constraint clearance (min 0.2mm))\n')
        r = self.run_tool("compose", MANIFEST)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unbalanced", r.stderr)

    def outside_file(self, name, text=""):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_target_symlink_is_refused(self):
        outside = self.outside_file("victim.txt", "keep me\n")
        os.symlink(outside, self.path(TARGET))
        for args in (("compose", MANIFEST), ("check", MANIFEST)):
            r = self.run_tool(*args)
            self.assertEqual(r.returncode, 1)
            self.assertIn("symbolic link", r.stderr + r.stdout)
            self.assertNotIn("keep me", r.stderr + r.stdout)
        with open(outside) as f:
            self.assertEqual(f.read(), "keep me\n")

    def test_target_dir_outside_is_refused(self):
        outdir = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outdir)
        os.symlink(outdir, self.path("hardware/out"))
        shutil.copy(self.path(MANIFEST), self.path("hardware/out/demo.dru.toml"))
        r = self.run_tool("compose", "hardware/out/demo.dru.toml")
        self.assertEqual(r.returncode, 1)
        self.assertIn("outside the repository", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(outdir, "demo.kicad_dru")))

    def test_source_symlink_outside_is_refused(self):
        outside = self.outside_file("secret.kicad_dru", '(version 1)\n(rule "secret"\n\t(constraint clearance (min 1mm))\n)\n')
        os.remove(self.path(PRODUCT))
        os.symlink(outside, self.path(PRODUCT))
        r = self.run_tool("compose", MANIFEST)
        self.assertEqual(r.returncode, 1)
        self.assertIn("resolves outside the repository", r.stderr)
        self.assertFalse(os.path.exists(self.path(TARGET)))

    def test_source_inside_via_symlink_is_allowed(self):
        os.symlink("lib/opendap", self.path("hardware/fablink"))
        with open(self.path(MANIFEST), "w") as f:
            f.write('version = 1\nsources = ["hardware/fablink/design-rules/fab-demo/fab-demo.kicad_dru"]\n')
        self.assertEqual(self.run_tool("compose", MANIFEST).returncode, 0)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                               "-c", "commit.gpgsign=false", "-c", "core.hooksPath=",
                               *args], cwd=cwd or self.repo, capture_output=True, text=True)

    def linked_worktree(self):
        """Commit the fixture with its composed file and add a linked worktree of it."""
        self.run_tool("compose", MANIFEST)
        self.git("add", "-A")
        self.assertEqual(self.git("commit", "-q", "-m", "fixture").returncode, 0)
        wt = os.path.join(self.tmp, "wt")
        r = self.git("worktree", "add", "-q", wt)
        self.assertEqual(r.returncode, 0, r.stderr)
        return wt

    def test_check_in_linked_worktree(self):
        wt = self.linked_worktree()
        r = subprocess.run([sys.executable, TOOL, "check"], cwd=wt, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_check_from_a_commit_hook_in_linked_worktree(self):
        # git runs hooks in a linked worktree with GIT_DIR=<repo>/.git/worktrees/<name> set and
        # no GIT_WORK_TREE; the hook must still resolve the worktree, not the manifest's folder.
        wt = self.linked_worktree()
        hooks = os.path.join(self.tmp, "githooks")
        os.makedirs(hooks)
        hook = os.path.join(hooks, "pre-commit")
        with open(hook, "w") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.abspath(TOOL)}" check\n')
        os.chmod(hook, 0o755)
        with open(os.path.join(wt, "hardware", "notes.txt"), "w") as f:
            f.write("change\n")
        self.git("add", "-A", cwd=wt)
        r = subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={hooks}",
                            "commit", "-q", "-m", "change"],
                           cwd=os.path.join(wt, "hardware"), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # A stale composition is still caught from the worktree's hook.
        with open(os.path.join(wt, PRODUCT), "a") as f:
            f.write('(rule "Product: extra"\n\t(constraint track_width (min 0.2mm))\n)\n')
        self.git("add", "-A", cwd=wt)
        r = subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={hooks}",
                            "commit", "-q", "-m", "stale"],
                           cwd=wt, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("stale", r.stderr)

    def test_confinement_holds_in_linked_worktree(self):
        wt = self.linked_worktree()
        outside = self.outside_file("secret.kicad_dru", '(version 1)\n(rule "secret"\n\t(constraint clearance (min 1mm))\n)\n')
        os.remove(os.path.join(wt, PRODUCT))
        os.symlink(outside, os.path.join(wt, PRODUCT))
        r = subprocess.run([sys.executable, TOOL, "compose", MANIFEST], cwd=wt, capture_output=True, text=True,
                           env={**os.environ, "GIT_DIR": os.path.join(self.repo, ".git", "worktrees", "wt")})
        self.assertEqual(r.returncode, 1)
        self.assertIn("resolves outside the repository", r.stderr)

    def test_usage_error(self):
        self.assertEqual(self.run_tool("frobnicate").returncode, 2)


class OdapkitDru(unittest.TestCase):
    """bin/odapkit-dru: `odapkit dru` runs hooks/kicad-dru.py, the kicad-dru-composed hook's engine."""

    FAB = '(version 1)\n(rule "Fab: track width"\n\t(constraint track_width (min 0.1mm))\n)\n'
    PRODUCT = '(version 1)\n(rule "Product: track width"\n\t(constraint track_width (min 0.2mm))\n)\n'
    MANIFEST = 'version = 1\nsources = ["hardware/lib/opendap/fab.kicad_dru", "hardware/product.kicad_dru"]\n'

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        for rel, text in (("hardware/lib/opendap/fab.kicad_dru", self.FAB), ("hardware/product.kicad_dru", self.PRODUCT),
                          ("hardware/demo.dru.toml", self.MANIFEST)):
            os.makedirs(os.path.dirname(os.path.join(self.repo, rel)), exist_ok=True)
            with open(os.path.join(self.repo, rel), "w") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.repo)

    def run_tool(self, *args):
        return subprocess.run([WRAPPER, *args], cwd=self.repo, capture_output=True, text=True)

    def test_compose_then_check(self):
        self.assertEqual(self.run_tool("check").returncode, 1)
        r = self.run_tool("compose", "hardware/demo.dru.toml")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.repo, "hardware", "demo.kicad_dru")) as f:
            text = f.read()
        self.assertEqual(text.count("(version 1)"), 1)
        self.assertIn("# Composed by `odapkit dru` from hardware/demo.dru.toml.", text)
        self.assertIn("# Source: hardware/lib/opendap/fab.kicad_dru", text)
        self.assertLess(text.index("Fab: track width"), text.index("Product: track width"))
        self.assertEqual(self.run_tool("check").returncode, 0)

    def test_same_output_as_the_hook(self):
        a = self.run_tool("compose", "--stdout", "hardware/demo.dru.toml")
        b = subprocess.run([sys.executable, TOOL, "compose", "--stdout", "hardware/demo.dru.toml"], cwd=self.repo,
                           capture_output=True, text=True)
        self.assertEqual((a.returncode, a.stdout), (b.returncode, b.stdout))

    def test_help_and_usage(self):
        r = self.run_tool("--help")
        self.assertEqual(r.returncode, 0)
        summary = re.search(r'name = "dru"\nsummary = "([^"]+)"', read(TOOLS_TOML)).group(1)
        self.assertEqual(r.stdout.splitlines()[0], f"odapkit dru: {summary}")
        self.assertEqual(self.run_tool().returncode, 2)

    def test_declared_in_tools_toml(self):
        names = set(re.findall(r'^name = "([^"]+)"', read(TOOLS_TOML), re.M))
        self.assertEqual({f.removeprefix("odapkit-") for f in os.listdir(os.path.dirname(WRAPPER))}, names)


if __name__ == "__main__":
    unittest.main()
