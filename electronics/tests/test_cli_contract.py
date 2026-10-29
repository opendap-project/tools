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
    return os.path.join(ROOT, "bin", f"odw-{name}")


PLUGINS = [n for n in sorted(TOOLS) if n != "component" or shutil.which("uv")]


class Plugins(unittest.TestCase):
    def test_every_plugin_is_declared(self):
        bin_ = os.path.join(ROOT, "bin")
        self.assertEqual({f.removeprefix("odw-") for f in os.listdir(bin_) if f.startswith("odw-")}, set(TOOLS))
        # Each old name is a link to its plugin for one minor release (HL-72).
        self.assertEqual({f: os.readlink(os.path.join(bin_, f)) for f in os.listdir(bin_) if f.startswith("odapkit-")},
                         {f"odapkit-{n}": f"odw-{n}" for n in TOOLS})

    def test_every_plugin_is_a_thin_entry_point(self):
        # bin/ holds entry points only: each execs its engine (in this folder, or core's helpers).
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
            self.assertEqual(r.stdout.splitlines()[0], f"odw {name}: {TOOLS[name]}", name)

    def test_unknown_flag_exits_2_with_a_code_a_hint_and_a_fix(self):
        for name in ["craft", "variant"]:
            if name not in PLUGINS:
                continue
            r = run(tool(name), "--odw-no-such-flag")
            self.assertEqual(r.returncode, 2, name)
            self.assertRegex(r.stderr, r"error input\.invalid: .*\n  hint: .*\n  fix: ", name)

    def test_json_documents_carry_a_schema(self):
        cases = [("craft", "check", "--json", "schematic/craft/tests/fixtures/bad.kicad_sym"),
                 ("variant", "list", "--json", "--dir", "variant/tests/fixtures/variant/board/variants")]
        if shutil.which("uv"):
            cases.append(("stackup", "stack", "pcb/stackup/tests/fixtures/fixture.toml", "--json"))
        for name, *args in cases:
            r = run(tool(name), *args)
            self.assertTrue(json.loads(r.stdout)["schema"].startswith("opendap."), (name, r.stderr))

    def test_errors_with_json_are_error_documents(self):
        r = run(tool("variant"), "--json", "--odw-no-such-flag")
        self.assertEqual(json.loads(r.stdout)["schema"], "opendap.error/v1")


class Scripts(unittest.TestCase):
    SCRIPTS = [[sys.executable, "craft/craftrules.py"],
               [sys.executable, "pcb/craft/pcb-craft-check.py"], [sys.executable, "pcb/stackup/stackup.py"],
               ["sh", "pcb/fab-package.sh"]]

    def test_help_exits_0_with_usage_on_stdout(self):
        for argv in self.SCRIPTS:
            r = run(*argv, "--help")
            self.assertEqual(r.returncode, 0, argv)
            self.assertTrue(r.stdout.strip(), argv)

    def test_unknown_flag_exits_2(self):
        for argv in self.SCRIPTS:
            self.assertEqual(run(*argv, "--odw-no-such-flag").returncode, 2, argv)

    def test_library_modules_are_not_executables(self):
        for f in ("pcb/stackup/explore.py", "pcb/stackup/pdn.py"):
            with open(os.path.join(ROOT, f)) as fh:
                self.assertNotIn("__main__", fh.read(), f)

    def test_craft_rules_json(self):
        r = run(sys.executable, "craft/craftrules.py", "render",
                "craft/tests/fixtures/craft-rules/pcb-craft-standard.md", "--check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["schema"], "opendap.craft-rules-render/v1")


if __name__ == "__main__":
    unittest.main()
