"""Tests for odapkit sim (its engine, tools/sim/simchecks.py) on the RC fixture in tests/fixtures/sim (needs ngspice on PATH).

Run from the repository root: python3 -m unittest discover tests
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "sim", "__main__.py")
FIXTURE = os.path.join(HERE, "fixtures", "sim")
MODEL = b"* fake vendor model\n.subckt FAKE a b\nR1 a b 1k\n.ends\n"


@unittest.skipIf(shutil.which(os.environ.get("NGSPICE", "ngspice")) is None, "ngspice not on PATH")
class Sim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "board")
        shutil.copytree(FIXTURE, self.repo)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        with open(os.path.join(self.repo, ".gitignore"), "w") as f:
            f.write("sim/**/results/\nsim/**/models/\n")
        self.study = os.path.join(self.repo, "sim", "rc")
        self.env = dict(os.environ, XDG_CACHE_HOME=os.path.join(self.tmp, "cache"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_tool(self, *args):
        return subprocess.run([sys.executable, TOOL, *args], cwd=self.repo, capture_output=True,
                              text=True, env=self.env)

    def append_checks(self, text):
        with open(os.path.join(self.study, "checks.toml"), "a") as f:
            f.write(text)

    def report(self):
        with open(os.path.join(self.repo, "sim", "results", "regression.json")) as f:
            return json.load(f)

    def test_fixture_passes(self):
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("4 passed", r.stdout)
        rep = self.report()
        self.assertTrue(rep["ok"])
        byid = {c["id"]: c for c in rep["checks"]}
        self.assertAlmostEqual(byid["tau"]["value"], 1e-3, delta=2e-5)
        self.assertAlmostEqual(byid["tau-2k"]["value"], 2.0, delta=0.04)
        self.assertEqual(byid["tau"]["source"], "FIXTURE-1")

    def test_json_and_only(self):
        r = self.run_tool("run", ".", "--json", "--only", "rc/tau*")
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(rep["schema"], "opendap.sim-report/v1")
        self.assertEqual(sorted(c["id"] for c in rep["checks"]), ["tau", "tau-2k"])

    def test_limit_violation_fails(self):
        self.append_checks('[[check]]\nid = "too-slow"\nnetlist = "rc.cir"\nmeasure = "tau"\n'
                           'max = 0.5e-3\nsource = "FIXTURE-5"\n')
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stdout)
        self.assertEqual({c["id"]: c["status"] for c in self.report()["checks"]}["too-slow"], "fail")

    def test_missing_measure_is_error(self):
        self.append_checks('[[check]]\nid = "nothing"\nnetlist = "rc.cir"\nmeasure = "nope"\n'
                           'max = 1\nsource = "FIXTURE-6"\n')
        r = self.run_tool("--only", "rc/nothing")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no value found", r.stdout)

    def test_unknown_param_is_error(self):
        self.append_checks('[[check]]\nid = "badparam"\nnetlist = "rc.cir"\nparams = { L = 1 }\n'
                           'measure = "tau"\nmax = 1\nsource = "FIXTURE-7"\n')
        r = self.run_tool("--only", "rc/badparam")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no .param L", r.stdout)

    def test_check_without_source_is_rejected(self):
        self.append_checks('[[check]]\nid = "nosource"\nnetlist = "rc.cir"\nmeasure = "tau"\nmax = 1\n')
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("source of its limit", r.stderr)

    def model_check(self, sha):
        self.append_checks(
            '[[model]]\nid = "fake"\nmanufacturer = "Acme Parts"\nmpn = "FAKE1"\nfile = "FAKE1.zip"\n'
            'member = "FAKE1/FAKE1.lib"\ndest = "models/FAKE1.lib"\n'
            f'sha256 = "{sha}"\n'
            '[[check]]\nid = "with-model"\nnetlist = "rc.cir"\nmeasure = "tau"\nmax = 2e-3\n'
            'source = "FIXTURE-8"\nmodels = ["fake"]\n')

    def put_in_cache(self):
        d = os.path.join(self.tmp, "cache", "opendap", "parts", "Acme-Parts", "FAKE1")
        os.makedirs(d)
        with zipfile.ZipFile(os.path.join(d, "FAKE1.zip"), "w") as z:
            z.writestr("FAKE1/FAKE1.lib", MODEL)

    def test_missing_model_blocks(self):
        self.model_check(hashlib.sha256(MODEL).hexdigest())
        r = self.run_tool("--only", "rc/with-model")
        self.assertEqual(r.returncode, 1)
        self.assertIn("BLOCKED", r.stdout)
        r = self.run_tool("--only", "rc/with-model", "--allow-missing-models")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_fetch_extracts_and_verifies_model(self):
        self.model_check(hashlib.sha256(MODEL).hexdigest())
        self.put_in_cache()
        r = self.run_tool("fetch")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.study, "models", "FAKE1.lib"), "rb") as f:
            self.assertEqual(f.read(), MODEL)
        r = self.run_tool("--only", "rc/with-model")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_fetch_rejects_wrong_hash(self):
        self.model_check("0" * 64)
        self.put_in_cache()
        r = self.run_tool("fetch")
        self.assertEqual(r.returncode, 1)
        self.assertIn("sha256", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.study, "models", "FAKE1.lib")))

    def test_fetch_refuses_model_not_ignored(self):
        self.model_check(hashlib.sha256(MODEL).hexdigest())
        self.put_in_cache()
        os.remove(os.path.join(self.repo, ".gitignore"))
        r = self.run_tool("fetch")
        self.assertEqual(r.returncode, 1)
        self.assertIn("never committed", r.stderr)

    def test_identical_runs_execute_once(self):
        self.append_checks(
            '[[check]]\nid = "cmd-a"\ncommand = ["sh", "-c", "echo x >> runs.log; echo v = 1"]\n'
            'measure = "v"\nmax = 2\nsource = "FIXTURE-9"\n'
            '[[check]]\nid = "cmd-b"\ncommand = ["sh", "-c", "echo x >> runs.log; echo v = 1"]\n'
            'measure = "v"\nmin = 0\nsource = "FIXTURE-10"\n')
        r = self.run_tool("--only", "rc/cmd-*")
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(os.path.join(self.study, "runs.log")) as f:
            self.assertEqual(f.read(), "x\n")

    def test_fetch_refuses_model_dest_link(self):
        self.model_check(hashlib.sha256(MODEL).hexdigest())
        self.put_in_cache()
        outside = os.path.join(self.tmp, "victim.lib")
        with open(outside, "w") as f:
            f.write("keep me\n")
        os.makedirs(os.path.join(self.study, "models"))
        os.symlink(outside, os.path.join(self.study, "models", "FAKE1.lib"))
        r = self.run_tool("fetch")
        self.assertEqual(r.returncode, 1)
        with open(outside) as f:
            self.assertEqual(f.read(), "keep me\n")

    def test_usage_error(self):
        self.assertEqual(self.run_tool("--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
