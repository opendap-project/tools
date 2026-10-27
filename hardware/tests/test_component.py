"""Tests for odapkit component (bin/odapkit-component, engine tools/component/validate.py) on the board example in tests/fixtures/component (needs uv).

Run from the repository root: python3 -m unittest discover tests
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "bin", "odapkit-component")
FIXTURE = os.path.join(HERE, "fixtures", "component", "board")


@unittest.skipIf(shutil.which("uv") is None, "uv not on PATH")
class Component(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "board")
        shutil.copytree(FIXTURE, self.repo)
        self.export = os.path.join(self.repo, "export")
        self.yaml = os.path.join(self.export, "component.yaml")
        with open(self.yaml) as f:
            self.base = f.read()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def validate(self, text=None):
        if text is not None:
            with open(self.yaml, "w") as f:
                f.write(text)
        r = subprocess.run([TOOL, "validate", "--json", self.repo], capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r.returncode, json.loads(r.stdout)["items"][0]

    def test_example_is_valid(self):
        rc, item = self.validate()
        self.assertEqual(rc, 0, item)
        self.assertEqual(item["warnings"], [])

    def test_missing_file(self):
        os.remove(os.path.join(self.export, "board.glb"))
        rc, item = self.validate()
        self.assertEqual(rc, 1)
        self.assertIn("files: board.glb does not exist in export/", item["errors"])

    def test_unlisted_file_warns(self):
        open(os.path.join(self.export, "extra.step"), "w").close()
        rc, item = self.validate()
        self.assertEqual(rc, 0)
        self.assertIn("extra.step is in export/ but not listed in files", item["warnings"])

    def test_mechanics_file_needs_mass_properties(self):
        open(os.path.join(self.export, "body.step"), "w").close()
        text = self.base + "  - path: body.step\n    domain: mechanics\n"
        rc, item = self.validate(text)
        self.assertEqual(rc, 1)
        self.assertTrue(any("mechanics" in e for e in item["errors"]), item)
        rc, item = self.validate(text + "mechanics:\n  mass_g: 12.5\n  center_of_gravity_mm: [1.0, 2.0, 0.5]\n")
        self.assertEqual(rc, 0, item)

    def test_path_outside_export(self):
        rc, item = self.validate(self.base + "  - path: ../hardware/board.kicad_pcb\n    domain: electronics\n")
        self.assertEqual(rc, 1)

    def test_unknown_field_and_domain(self):
        rc, _ = self.validate(self.base + "license: MIT\n")
        self.assertEqual(rc, 1)
        rc, _ = self.validate(self.base.replace("domain: renders", "domain: marketing"))
        self.assertEqual(rc, 1)

    def test_duplicates(self):
        rc, item = self.validate(self.base.replace(
            "interfaces:\n", "interfaces:\n  - name: hat-interface\n    version: \"2\"\n"))
        self.assertEqual(rc, 1)
        self.assertIn("interfaces: hat-interface is listed more than once", item["errors"])

    def test_missing_required(self):
        rc, item = self.validate(self.base.replace("version: v0.1.0\n", ""))
        self.assertEqual(rc, 1)
        self.assertTrue(any("'version' is a required property" in e for e in item["errors"]))

    def test_bad_yaml(self):
        rc, item = self.validate("name: [unclosed\n")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
