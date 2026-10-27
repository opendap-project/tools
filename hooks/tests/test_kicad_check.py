"""Tests for hooks/kicad-check.py (the kicad-erc and kicad-drc hooks) with a stand-in kicad-cli, and for
hooks/kicad-check.sh, the former entry point, which runs it.

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
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "hooks", "kicad-check.py")
SHELL = os.path.join(ROOT, "hooks", "kicad-check.sh")
# Under a pre-commit hook git exports GIT_DIR and others; the tests must never act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

# A stand-in kicad-cli: writes a report to -o, as text or JSON (--format json), and exits 5 when the input's
# name contains "bad" and --exit-code-violations is given, as kicad-cli does on violations. It logs its argv.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(a) + "\n")
if a == ["version"]:
    print("10.0.7"); sys.exit(0)
out, src = a[a.index("-o") + 1], a[-1]
bad = "bad" in os.path.basename(src)
if "--format" in a and a[a.index("--format") + 1] == "json":
    v = [{"type": "pin_not_connected", "severity": "error", "description": "Pin not connected",
          "items": [{"description": "Pin 1 of U1", "pos": {"x": 1.0, "y": 2.0}}]}] if bad else []
    doc = {"sheets": [{"path": "/", "violations": v}]} if a[0] == "sch" else \
          {"violations": v, "unconnected_items": [], "schematic_parity": []}
    open(out, "w").write(json.dumps(doc))
else:
    lines = ["** Report **"] + [f"line {i}" for i in range(1, 80 if bad else 3)]
    open(out, "w").write("\n".join(lines) + "\n")
sys.exit(5 if bad and "--exit-code-violations" in a else 0)
'''


class KicadCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cli = os.path.join(self.tmp, "kicad-cli")
        with open(self.cli, "w") as f:
            f.write(FAKE)
        os.chmod(self.cli, 0o755)
        self.log = os.path.join(self.tmp, "log")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)
        for name in ("good", "bad"):
            for ext in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
                open(os.path.join(self.proj, name + ext), "w").close()
        # A sheet of a project (no same-named .kicad_pro next to it): not checked on its own.
        open(os.path.join(self.proj, "sub.kicad_sch"), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_tool(self, *args, cli=True, entry=None):
        env = {**os.environ, "FAKE_LOG": self.log}
        if cli:
            env["KICAD_CLI"] = self.cli
        cmd = [entry] if entry else [sys.executable, TOOL]
        return subprocess.run([*cmd, *args], cwd=self.proj, capture_output=True, text=True, env=env)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def test_erc_pass_runs_the_same_command(self):
        r = self.run_tool("erc", "good.kicad_sch")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        (call,) = self.calls()
        self.assertEqual(call[:2] + call[2:5], ["sch", "erc", "--severity-error", "--exit-code-violations", "-o"])
        self.assertEqual(call[-1], "good.kicad_sch")

    def test_drc_runs_with_schematic_parity(self):
        self.run_tool("drc", "good.kicad_pcb")
        (call,) = self.calls()
        self.assertEqual(call[:5], ["pcb", "drc", "--schematic-parity", "--severity-error", "--exit-code-violations"])

    def test_failure_prints_the_header_and_60_report_lines(self):
        r = self.run_tool("erc", "good.kicad_sch", "bad.kicad_sch")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        lines = r.stderr.splitlines()
        self.assertEqual(lines[0], "KiCad ERC failed: bad.kicad_sch")
        self.assertEqual(lines[1:], ["** Report **"] + [f"line {i}" for i in range(1, 60)])

    def test_files_outside_a_project_are_skipped(self):
        r = self.run_tool("erc", "sub.kicad_sch")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_no_kicad_cli(self):
        r = subprocess.run([sys.executable, TOOL, "erc", "good.kicad_sch"], cwd=self.proj, capture_output=True,
                           text=True, env={**os.environ, "KICAD_CLI": "", "KICAD_APP": "/nonexistent",
                                           "PATH": os.path.dirname(sys.executable)})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr, "kicad-cli not found. Install KiCad 10 or set KICAD_CLI.\n")

    def test_shell_entry_point_runs_the_engine(self):
        r = self.run_tool("erc", "bad.kicad_sch", entry=SHELL)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("KiCad ERC failed: bad.kicad_sch\n** Report **\n"))

    def test_json_is_a_check_result(self):
        r = self.run_tool("drc", "--json", "good.kicad_pcb", "bad.kicad_pcb", "sub.kicad_pcb")
        self.assertEqual(r.returncode, 1)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["schema"], "opendap.check-result/v1")
        self.assertEqual(doc["tool"]["name"], "kicad-drc")
        self.assertRegex(doc["generated"], r"^\d{4}-\d\d-\d\dT")
        self.assertIn("repo", doc["subject"])
        by_id = {x["id"]: x for x in doc["results"]}
        self.assertEqual(by_id["kicad-drc:good.kicad_pcb"]["verdict"], "pass")
        self.assertEqual(by_id["kicad-drc:bad.kicad_pcb"]["verdict"], "fail")
        self.assertEqual(by_id["kicad-drc:sub.kicad_pcb"]["verdict"], "skip")
        v = [x for x in doc["results"] if x.get("rule") == "pin_not_connected"]
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["verdict"], "fail")
        self.assertEqual(v[0]["location"], {"path": "bad.kicad_pcb"})
        self.assertIn("Pin 1 of U1", v[0]["message"])
        for x in doc["results"]:
            self.assertEqual(set(x) - {"id", "rule", "verdict", "message", "location"}, set())
            self.assertTrue(x["message"])
        self.assertIn("--format", self.calls()[0])
        self.assertTrue(r.stderr.startswith("KiCad DRC failed: bad.kicad_pcb\n"))

    def test_json_validates_against_the_schema_when_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = os.path.join(ROOT, "..", "host", "schemas", "check-result.schema.json")
        if not os.path.isfile(schema):
            self.skipTest("no toolkit-host checkout next to this one")
        with open(schema) as f:
            s = json.load(f)
        doc = json.loads(self.run_tool("erc", "--json", "good.kicad_sch", "bad.kicad_sch").stdout)
        jsonschema.validate(doc, s)

    def test_usage(self):
        r = self.run_tool("--help")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("kicad-check: "))
        for args in ((), ("lint", "x.kicad_sch"), ("erc", "--no-such-flag")):
            r = self.run_tool(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertRegex(r.stderr, r"error input\.invalid: .*\n  hint: .*\n  fix: ")


class HookDefinitions(unittest.TestCase):
    """The hook ids and arguments other repositories pin stay as they were; the README lists every hook."""

    def hooks(self):
        with open(os.path.join(ROOT, ".pre-commit-hooks.yaml")) as f:
            text = f.read()
        return [dict(re.findall(r"^\s+(\w+): (.*)$", "  id: " + block, re.M))
                for block in text.split("- id: ")[1:]]

    def test_ids_entries_and_files(self):
        got = {h["id"]: (h["entry"], h.get("files")) for h in self.hooks()}
        self.assertEqual(got["kicad-erc"], ("hooks/kicad-check.py erc", r"\.kicad_sch$"))
        self.assertEqual(got["kicad-drc"], ("hooks/kicad-check.py drc", r"\.kicad_pcb$"))
        self.assertEqual(set(got), {"kicad-erc", "kicad-drc", "no-fab-outputs", "no-stray-files",
                                    "kicad-dru-composed"})
        for h in self.hooks():
            self.assertTrue(os.access(os.path.join(ROOT, h["entry"].split()[0]), os.X_OK), h["id"])

    def test_readme_lists_every_hook(self):
        with open(os.path.join(ROOT, "README.md")) as f:
            table = set(re.findall(r"^\| `([a-z-]+)` \|", f.read(), re.M))
        self.assertEqual(table, {h["id"] for h in self.hooks()})


if __name__ == "__main__":
    unittest.main()
