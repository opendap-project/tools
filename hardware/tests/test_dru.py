"""Smoke test for bin/odapkit-dru, which runs toolkit-hooks' kicad-dru.py (the composition itself is
tested there). Skipped without a toolkit-hooks checkout (sibling ../hooks or $OPENDAP_TOOLKIT_HOOKS).

Run from the repository root: python3 -m unittest discover tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "bin", "odapkit-dru")
HOOKS = os.environ.get("OPENDAP_TOOLKIT_HOOKS", os.path.join(HERE, "..", "..", "hooks"))

FAB = '(version 1)\n(rule "Fab: track width"\n\t(constraint track_width (min 0.1mm))\n)\n'
PRODUCT = '(version 1)\n(rule "Product: track width"\n\t(constraint track_width (min 0.2mm))\n)\n'
MANIFEST = 'version = 1\nsources = ["hardware/lib/opendap/fab.kicad_dru", "hardware/product.kicad_dru"]\n'


@unittest.skipIf(not os.path.isfile(os.path.join(HOOKS, "hooks", "kicad-dru.py")), "no toolkit-hooks checkout")
class Dru(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        for rel, text in (("hardware/lib/opendap/fab.kicad_dru", FAB), ("hardware/product.kicad_dru", PRODUCT),
                          ("hardware/demo.dru.toml", MANIFEST)):
            os.makedirs(os.path.dirname(os.path.join(self.repo, rel)), exist_ok=True)
            with open(os.path.join(self.repo, rel), "w") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.repo)

    def run_tool(self, *args):
        return subprocess.run([TOOL, *args], cwd=self.repo, capture_output=True, text=True)

    def test_compose_then_check(self):
        self.assertEqual(self.run_tool("check").returncode, 1)
        r = self.run_tool("compose", "hardware/demo.dru.toml")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.repo, "hardware", "demo.kicad_dru")) as f:
            text = f.read()
        self.assertEqual(text.count("(version 1)"), 1)
        self.assertIn("# Source: hardware/lib/opendap/fab.kicad_dru", text)
        self.assertLess(text.index("Fab: track width"), text.index("Product: track width"))
        self.assertEqual(self.run_tool("check").returncode, 0)

    def test_help_and_usage(self):
        self.assertEqual(self.run_tool("--help").returncode, 0)
        self.assertEqual(self.run_tool().returncode, 2)


if __name__ == "__main__":
    unittest.main()
