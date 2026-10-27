"""Tests for hooks/kicad-dru.py on the fixture in tests/fixtures/dru.

Run from the repository root: python3 -m unittest discover tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "hooks", "kicad-dru.py")
FIXTURE = os.path.join(HERE, "fixtures", "dru")
MANIFEST = "hardware/demo.dru.toml"
TARGET = "hardware/demo.kicad_dru"
PRODUCT = "hardware/rules/product.kicad_dru"


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


if __name__ == "__main__":
    unittest.main()
