"""odw sim's command-line contract (command standard §5, §6), without ngspice: --help, an unknown flag, --dry-run
and its JSON documents. Moved with the plugin from hardware/'s contract test (HL-71).

Run from this folder: python3 -m unittest tests.test_cli
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a fixture's git would act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "odw-sim")
SUMMARY = re.search(r'name = "sim"\nsummary = "([^"]+)"', open(os.path.join(ROOT, "tools.toml")).read()).group(1)
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run(*argv):
    return subprocess.run([TOOL, *argv], capture_output=True, text=True, cwd=ROOT, env=ENV,
                          stdin=subprocess.DEVNULL, timeout=120)


class Contract(unittest.TestCase):
    def test_the_old_name_is_a_link_to_the_plugin(self):
        self.assertEqual(os.readlink(os.path.join(ROOT, "bin", "odapkit-sim")), "odw-sim")

    def test_help_first_line_is_front_tool_and_summary(self):
        r = run("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], f"odw sim: {SUMMARY}")

    def test_unknown_flag_exits_2_with_a_code_a_hint_and_a_fix(self):
        r = run("--odw-no-such-flag")
        self.assertEqual(r.returncode, 2)
        self.assertRegex(r.stderr, r"error input\.invalid: .*\n  hint: .*\n  fix: ")

    def test_dry_run_runs_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(os.path.join(ROOT, "tests", "fixtures", "sim"), os.path.join(tmp, "sim"))
            r = run(os.path.join(tmp, "sim"), "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("would run rc/tau", r.stderr)
            self.assertFalse(any("results" in d for d, _, _ in os.walk(tmp)))

    def test_list_json_carries_a_schema(self):
        r = run("list", "--json", "tests/fixtures/sim")
        self.assertTrue(json.loads(r.stdout)["schema"].startswith("opendap."), r.stderr)

    def test_errors_with_json_are_error_documents(self):
        r = run("--json", "--odw-no-such-flag")
        self.assertEqual(json.loads(r.stdout)["schema"], "opendap.error/v1")


if __name__ == "__main__":
    unittest.main()
