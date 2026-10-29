"""odw parts and parts-sync.py: the command-line contract (command standard §5, §6), offline. Moved with the plugin
from hardware/'s contract test (HL-71).

Run from this folder: python3 -m unittest discover tests
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a fixture's git would act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "odw-parts")
SUMMARY = re.search(r'name = "parts"\nsummary = "([^"]+)"', open(os.path.join(ROOT, "tools.toml")).read()).group(1)
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run(*argv, env=None):
    return subprocess.run(list(argv), capture_output=True, text=True, cwd=ROOT, env={**ENV, **(env or {})},
                          stdin=subprocess.DEVNULL, timeout=120)


class Plugin(unittest.TestCase):
    def test_the_old_name_is_a_link_to_the_plugin(self):
        self.assertEqual(os.readlink(os.path.join(ROOT, "bin", "odapkit-parts")), "odw-parts")

    def test_help_first_line_is_front_tool_and_summary(self):
        r = run(TOOL, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], f"odw parts: {SUMMARY}")

    def test_unknown_flag_exits_2_with_a_code_a_hint_and_a_fix(self):
        r = run(TOOL, "--odw-no-such-flag")
        self.assertEqual(r.returncode, 2)
        self.assertRegex(r.stderr, r"error input\.invalid: .*\n  hint: .*\n  fix: ")

    def test_matrix_dry_run_names_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cand = os.path.join(tmp, "c.toml")
            with open(cand, "w") as fh:
                fh.write('version = 1\n[matrix]\ntitle = "t"\ncolumns = ["soc"]\n'
                         '[[candidate]]\nmpn = "X1"\nquery = "X1"\n[candidate.attrs]\nsoc = "T/A"\n')
            r = run(TOOL, "matrix", cand, "--out", os.path.join(tmp, "out"), "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("would write", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "out")))


class PartsSync(unittest.TestCase):
    SCRIPT = [sys.executable, os.path.join(ROOT, "parts-sync.py")]

    def test_help_exits_0_with_usage_on_stdout(self):
        r = run(*self.SCRIPT, "--help")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip())

    def test_unknown_flag_exits_2(self):
        self.assertEqual(run(*self.SCRIPT, "--odw-no-such-flag").returncode, 2)

    def test_check_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, "manifest.toml")
            with open(manifest, "w") as f:
                f.write('[[file]]\nmpn = "X1"\nmanufacturer = "Acme"\nkind = "datasheet"\n'
                        'url = "https://example.invalid/x1.pdf"\n')
            r = run(*self.SCRIPT, manifest, "--check", "--json", env={"XDG_CACHE_HOME": os.path.join(tmp, "cache")})
            self.assertEqual(r.returncode, 1)
            self.assertEqual(json.loads(r.stdout)["items"][0]["status"], "missing")
            self.assertFalse(os.path.exists(os.path.join(tmp, "cache")))


if __name__ == "__main__":
    unittest.main()
