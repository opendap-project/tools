"""The command-line contract of every tool here (command standard §5, §6), without KiCad, ngspice or the network.

Run from the repository root: python3 -m unittest tests.test_cli_contract
"""
import json
import os
import re
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
TOOLS = dict(re.findall(r'name = "([^"]+)"\nsummary = "([^"]+)"', open(os.path.join(ROOT, "tools.toml")).read()))
# Under a pre-commit hook git exports GIT_DIR and others; the tools must not see them.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run(*argv, cwd=ROOT, env=None):
    return subprocess.run(list(argv), capture_output=True, text=True, cwd=cwd, env={**ENV, **(env or {})},
                          stdin=subprocess.DEVNULL, timeout=120)


def tool(name):
    return os.path.join(ROOT, "bin", f"odapkit-{name}")


PLUGINS = [n for n in sorted(TOOLS) if n != "component" or shutil.which("uv")]


class Plugins(unittest.TestCase):
    def test_every_plugin_is_declared(self):
        self.assertEqual({f.removeprefix("odapkit-") for f in os.listdir(os.path.join(ROOT, "bin"))}, set(TOOLS))

    def test_every_plugin_is_a_thin_entry_point(self):
        # bin/ holds entry points only: each execs its engine (under tools/, or toolkit-host's helpers).
        for f in sorted(os.listdir(os.path.join(ROOT, "bin"))):
            with open(os.path.join(ROOT, "bin", f)) as fh:
                text = fh.read()
            self.assertTrue(text.startswith("#!/bin/sh"), f)
            self.assertIn("exec ", text, f)
            self.assertLessEqual(len(text.splitlines()), 70, f)

    def test_help_first_line_is_front_tool_and_summary(self):
        for name in PLUGINS:
            r = run(tool(name), "--help")
            self.assertEqual(r.returncode, 0, name)
            self.assertEqual(r.stdout.splitlines()[0], f"odapkit {name}: {TOOLS[name]}", name)

    def test_unknown_flag_exits_2_with_a_code_a_hint_and_a_fix(self):
        for name in ["component", "craft", "parts", "sim", "variant"]:
            if name not in PLUGINS:
                continue
            r = run(tool(name), "--odw-no-such-flag")
            self.assertEqual(r.returncode, 2, name)
            self.assertRegex(r.stderr, r"error input\.invalid: .*\n  hint: .*\n  fix: ", name)

    def test_sim_dry_run_runs_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(os.path.join(ROOT, "tests", "fixtures", "sim"), os.path.join(tmp, "sim"))
            r = run(tool("sim"), os.path.join(tmp, "sim"), "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("would run rc/tau", r.stderr)
            self.assertFalse(any("results" in d for d, _, _ in os.walk(tmp)))

    def test_parts_matrix_dry_run_names_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = os.path.join(tmp, "c.toml")
            with open(cand, "w") as fh:
                fh.write('version = 1\n[matrix]\ntitle = "t"\ncolumns = ["soc"]\n'
                         '[[candidate]]\nmpn = "X1"\nquery = "X1"\n[candidate.attrs]\nsoc = "T/A"\n')
            r = run(tool("parts"), "matrix", cand, "--out", os.path.join(tmp, "out"), "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("would write", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "out")))

    def test_json_documents_carry_a_schema(self):
        cases = [("sim", "list", "--json", "tests/fixtures/sim"),
                 ("craft", "check", "--json", "tests/fixtures/craft-sch/bad.kicad_sym"),
                 ("variant", "list", "--json", "--dir", "tests/fixtures/variant/board/variants")]
        if shutil.which("uv"):
            cases.append(("stackup", "stack", "tests/fixtures/stackup/fixture.toml", "--json"))
        for name, *args in cases:
            r = run(tool(name), *args)
            self.assertTrue(json.loads(r.stdout)["schema"].startswith("opendap."), (name, r.stderr))

    def test_errors_with_json_are_error_documents(self):
        r = run(tool("sim"), "--json", "--odw-no-such-flag")
        self.assertEqual(json.loads(r.stdout)["schema"], "opendap.error/v1")


class Scripts(unittest.TestCase):
    SCRIPTS = [[sys.executable, "tools/parts-sync.py"], [sys.executable, "tools/craft/craftrules.py"],
               [sys.executable, "tools/pcb-craft-check.py"], [sys.executable, "tools/stackup/stackup.py"],
               ["sh", "tools/fab-package.sh"]]

    def test_help_exits_0_with_usage_on_stdout(self):
        for argv in self.SCRIPTS:
            r = run(*argv, "--help")
            self.assertEqual(r.returncode, 0, argv)
            self.assertTrue(r.stdout.strip(), argv)

    def test_unknown_flag_exits_2(self):
        for argv in self.SCRIPTS:
            self.assertEqual(run(*argv, "--odw-no-such-flag").returncode, 2, argv)

    def test_library_modules_are_not_executables(self):
        for f in ("tools/stackup/explore.py", "tools/stackup/pdn.py"):
            with open(os.path.join(ROOT, f)) as fh:
                self.assertNotIn("__main__", fh.read(), f)

    def test_parts_sync_check_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, "manifest.toml")
            with open(manifest, "w") as f:
                f.write('[[file]]\nmpn = "X1"\nmanufacturer = "Acme"\nkind = "datasheet"\n'
                        'url = "https://example.invalid/x1.pdf"\n')
            r = run(sys.executable, "tools/parts-sync.py", manifest, "--check", "--json",
                    env={"XDG_CACHE_HOME": os.path.join(tmp, "cache")})
            self.assertEqual(r.returncode, 1)
            self.assertEqual(json.loads(r.stdout)["items"][0]["status"], "missing")
            self.assertFalse(os.path.exists(os.path.join(tmp, "cache")))

    def test_craft_rules_json(self):
        r = run(sys.executable, "tools/craft/craftrules.py", "render",
                "tests/fixtures/craft-rules/pcb-craft-standard.md", "--check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["schema"], "opendap.craft-rules-render/v1")


if __name__ == "__main__":
    unittest.main()
