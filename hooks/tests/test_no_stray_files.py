"""Tests for hooks/no-stray-files.sh and the visual-output hooks' entry points (RFC 0021 §6, §7).

Run from the repository root: python3 -m unittest discover tests
"""
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STRAY = os.path.join(ROOT, "hooks", "no-stray-files.sh")
REGENERATE = os.path.join(ROOT, "hooks", "outputs-regenerate.sh")
# Under a pre-commit hook git exports GIT_DIR and others; the tests must never act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]


def run(script, *files, cwd):
    return subprocess.run([script, *files], capture_output=True, text=True, cwd=cwd, stdin=subprocess.DEVNULL)


class NoStrayFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        os.makedirs(os.path.join(self.dir, "board", "outputs", "main"))
        os.makedirs(os.path.join(self.dir, "docs"))
        with open(os.path.join(self.dir, "board", "outputs.toml"), "w") as fh:
            fh.write('schema = "opendap.outputs/v1"\n')
        with open(os.path.join(self.dir, "board", "outputs", "stamp.json"), "w") as fh:
            fh.write('{"sources": [{"outputs": {"main/schematic.pdf": "00"}}]}\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_stamped_output_pdf_passes(self):
        r = run(STRAY, "board/outputs/main/schematic.pdf", cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_other_pdfs_fail(self):
        for f in ("docs/datasheet.pdf", "board/outputs/main/other.pdf", "outputs/x.pdf", "board/x/outputs/a.pdf"):
            with self.subTest(f=f):
                r = run(STRAY, f, cwd=self.dir)
                self.assertEqual(r.returncode, 1)
                self.assertIn("PDF documents are not committed", r.stderr)

    def test_without_a_manifest_a_stamped_pdf_fails(self):
        os.remove(os.path.join(self.dir, "board", "outputs.toml"))
        self.assertEqual(run(STRAY, "board/outputs/main/schematic.pdf", cwd=self.dir).returncode, 1)

    def test_html_rule_unchanged(self):
        self.assertEqual(run(STRAY, "case/renders/web/preview.html", cwd=self.dir).returncode, 0)
        self.assertEqual(run(STRAY, "docs/page.html", cwd=self.dir).returncode, 1)

    def test_regenerate_with_nothing_pushed_passes(self):
        self.assertEqual(run(REGENERATE, cwd=self.dir).returncode, 0)


if __name__ == "__main__":
    unittest.main()
