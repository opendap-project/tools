"""Tests for bin/odw-stackup on the fixture in tests/fixtures/stackup (needs uv on PATH).

Run from the repository root: python3 -m unittest discover tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a fixture's git would act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "..", "..", "bin", "odw-stackup")
FIXTURE = os.path.join(HERE, "fixtures", "fixture.toml")
sys.path.insert(0, os.path.join(HERE, ".."))


@unittest.skipIf(shutil.which("uv") is None, "uv not on PATH")
class Stackup(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([TOOL, *args], capture_output=True, text=True)

    def variant(self, old, new):
        with open(FIXTURE) as f:
            text = f.read()
        self.assertIn(old, text)
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w") as f:
            f.write(text.replace(old, new, 1))
        self.addCleanup(os.remove, path)
        return path

    def test_validate(self):
        r = self.run_tool("validate", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = json.loads(r.stdout)["rows"]
        cohn = [x for x in rows if x["formula"] == "Cohn (exact)"]
        self.assertEqual(len(cohn), 3)
        self.assertTrue(all(abs(x["error_pct"]) < 0.5 for x in cohn))

    def test_fixture_check_passes(self):
        r = self.run_tool("check", FIXTURE, "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = json.loads(r.stdout)
        byname = {c["name"]: c for c in rep["classes"]}
        self.assertAlmostEqual(byname["MS50"]["z_ohm"], 50.0, delta=0.5)
        self.assertAlmostEqual(byname["MS90D"]["z_ohm"], 90.0, delta=0.9)
        self.assertAlmostEqual(byname["MS90D"]["zodd"] * 2, byname["MS90D"]["z_ohm"], delta=0.01)
        self.assertAlmostEqual(byname["SL50"]["z_ohm"], 50.0, delta=0.5)
        # thinner dielectric -> lower impedance; wider trace -> lower impedance
        sens = byname["MS50"]["corners"]["sensitivity"]
        self.assertLess(sens["dielectric"][0], 0)
        self.assertLess(sens["width"][1], 0)

    def test_microstrip_matches_hammerstad_jensen(self):
        import analytic
        z, _ = analytic.hammerstad_jensen_microstrip(172.5, 100, 35, 4.2)
        r = self.run_tool("check", FIXTURE, "--only", "MS50", "--no-corners", "--json")
        zs = json.loads(r.stdout)["classes"][0]["z_ohm"]
        self.assertLess(abs(zs - z) / z, 0.015)

    def test_design_reproduces_recorded_width(self):
        r = self.run_tool("design", FIXTURE, "--only", "SL50", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertAlmostEqual(json.loads(r.stdout)["items"][0]["width_um"], 118.5, delta=1.0)

    def test_stale_and_off_target_fail(self):
        path = self.variant("width_um = 172.5\nz_ohm = 50.0", "width_um = 120.0\nz_ohm = 50.0")
        r = self.run_tool("check", path, "--only", "MS50", "--no-corners", "--json")
        self.assertEqual(r.returncode, 1)
        reason = json.loads(r.stdout)["classes"][0]["reason"]
        self.assertIn("outside", reason)
        self.assertIn("stale", reason)

    def test_distance_and_thickness_checks(self):
        path = self.variant("thickness_um = 100\n", "thickness_um = 120\n")
        r = self.run_tool("stack", path, "--json")
        self.assertEqual(r.returncode, 1)
        checks = json.loads(r.stdout)["checks"]
        self.assertFalse(checks[0]["ok"])
        self.assertEqual(checks[0]["value_um"], 120.0)

    def test_mirrored_reference(self):
        # a line referenced only from above (bottom side) is solved on the mirrored stack
        path = self.variant('layer = "L1"\nreference = ["L2"]', 'layer = "L4"\nreference = ["L3"]')
        r = self.run_tool("check", path, "--only", "MS50", "--no-corners", "--json")
        self.assertIn('"z_ohm"', r.stdout, r.stderr)

    def test_bad_file(self):
        path = self.variant("version = 1", "version = 2")
        self.assertEqual(self.run_tool("stack", path).returncode, 2)


if __name__ == "__main__":
    unittest.main()


EXPLORE = os.path.join(HERE, "fixtures", "explore.toml")


@unittest.skipIf(shutil.which("uv") is None, "uv not on PATH")
class Explore(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([TOOL, "explore", EXPLORE, *args], capture_output=True, text=True)

    def test_ranked_matrix(self):
        fd, csv_path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        self.addCleanup(os.remove, csv_path)
        r = self.run_tool("--csv", csv_path, "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        summ = {(s["layers"], s["build"]): s for s in json.loads(r.stdout)["summary"]}
        # 4 layers: an inner fast layer needs grounds on both sides, the outer layers need L2 and
        # L3 as grounds: no room
        four = summ[(4, "through")]
        self.assertEqual(four["pass"], 0)
        self.assertTrue(any("inner_fast" in k for k in four["pruned"]), four["pruned"])
        # 6 layers, through: the digital zone needs F G F G G F (or its mirror), the power zone
        # F G P P G F: L2 and L5 are ground under both, so two full-layer grounds
        six = summ[(6, "through")]
        self.assertEqual(six["pass"], 1)
        self.assertEqual(six["best"]["full_grounds"], 2)
        self.assertEqual(six["best"]["rank"], 1)
        with open(csv_path) as f:
            rows = f.read().splitlines()
        self.assertTrue(rows[0].startswith("variant,rank,layers,build"))
        self.assertEqual(len(rows), 1 + sum(s["candidates"] for s in summ.values()))

    def test_override(self):
        r = self.run_tool("--set", "thickness.max_mm=0.6", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        summ = json.loads(r.stdout)["summary"]
        self.assertTrue(all(s["pass"] == 0 for s in summ))
        r = self.run_tool("--set", "nothing.here=1")
        self.assertEqual(r.returncode, 2)


DUAL = """version = 1
name = "dual stripline"
[[layer]]
name = "L1"
type = "copper"
thickness_um = 18
[[layer]]
name = "D1"
type = "dielectric"
thickness_um = 120
er = 4.4
[[layer]]
name = "L2"
type = "copper"
thickness_um = 18
fills = "above"
[[layer]]
name = "D2"
type = "dielectric"
thickness_um = 200
er = 4.4
[[layer]]
name = "L3"
type = "copper"
thickness_um = 18
fills = "below"
[[layer]]
name = "D3"
type = "dielectric"
thickness_um = 120
er = 4.4
[[layer]]
name = "L4"
type = "copper"
thickness_um = 18
[[coupling]]
name = "stacked"
layers = ["L2", "L3"]
reference = ["L1", "L4"]
width_um = 120
offset_um = 0
[[coupling]]
name = "apart"
layers = ["L2", "L3"]
reference = ["L1", "L4"]
width_um = 120
offset_um = 1000
"""


@unittest.skipIf(shutil.which("uv") is None, "uv not on PATH")
class Coupling(unittest.TestCase):
    def test_broadside_falls_with_offset(self):
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w") as f:
            f.write(DUAL)
        self.addCleanup(os.remove, path)
        r = subprocess.run([TOOL, "coupling", path, "--json"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = {o["name"]: o for o in json.loads(r.stdout)["items"]}
        self.assertGreater(out["stacked"]["kb_pct"], 3.0)
        self.assertLess(out["apart"]["kb_pct"], 0.5)
        self.assertAlmostEqual(out["stacked"]["z_a_ohm"], out["stacked"]["z_b_ohm"], delta=0.5)

    def test_edge_coupling_on_one_layer(self):
        text = DUAL.split("[[coupling]]")[0] + (
            '[[coupling]]\nname = "near"\nlayers = ["L2", "L2"]\nreference = ["L1", "L4"]\n'
            'width_um = 120\noffset_um = 240\n'
            '[[coupling]]\nname = "far"\nlayers = ["L2", "L2"]\nreference = ["L1", "L4"]\n'
            'width_um = 120\noffset_um = 480\n')
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        r = subprocess.run([TOOL, "coupling", path, "--json"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = {o["name"]: o for o in json.loads(r.stdout)["items"]}
        self.assertGreater(out["near"]["kb_pct"], out["far"]["kb_pct"])
        self.assertGreater(out["far"]["kb_pct"], 0)


PDN = os.path.join(HERE, "fixtures", "pdn.toml")


@unittest.skipIf(shutil.which("uv") is None, "uv not on PATH")
class Pdn(unittest.TestCase):
    def test_closed_forms(self):
        import math
        import pdn
        # via pair, 1 mm long, 0.1 mm radius, 0.65 mm pitch: (mu0 h / pi) acosh(3.25) = 0.744 nH
        self.assertAlmostEqual(pdn.via_pair_l(1000, 100, 650) * 1e9, 0.4 * math.acosh(3.25), places=4)
        # radial plane inductance, 100 µm apart, 0.1 mm via to 2 mm: 0.2 nH/mm * 0.1 mm * ln 20
        self.assertAlmostEqual(pdn.spread_l(100, 2.0, 100) * 1e9, 0.02 * math.log(20), places=5)
        self.assertAlmostEqual(pdn.target_ohm({"name": "r", "ripple_mv": 60, "i_step_a": 2}), 0.03)

    def test_thicker_pairing_raises_the_peak(self):
        r = subprocess.run([TOOL, "pdn", PDN, "--json"], capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        rows = {x["case"]: x for x in json.loads(r.stdout)["rows"]}
        thin, thick = rows["thin"], rows["thick"]
        self.assertEqual(thin["target_mohm"], 50.0)
        # plane capacitance eps0 er / d: 57.2 pF/cm² at 65 µm and 4.2
        self.assertAlmostEqual(thin["parts"]["plane"]["pf_per_cm2"], 57.18, delta=0.05)
        self.assertGreater(thick["parts"]["1u"]["spread_nh"], thin["parts"]["1u"]["spread_nh"])
        self.assertGreaterEqual(thick["peak_mohm"], thin["peak_mohm"])
        # series resonance of one capacitor group sits where its total inductance says
        part = thin["parts"]["1u"]
        import math
        self.assertAlmostEqual(part["srf_mhz"], 1 / (2 * math.pi * math.sqrt(part["total_nh"] * 1e-9 * 1e-6)) / 1e6,
                               delta=0.05)

    def test_bad_file(self):
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w") as f:
            f.write("version = 1\n[[rail]]\nname = 'x'\nbogus = 1\n[[case]]\nname = 'c'\n")
        self.addCleanup(os.remove, path)
        self.assertEqual(subprocess.run([TOOL, "pdn", path], capture_output=True).returncode, 2)


@unittest.skipIf(shutil.which("uv") is None, "uv not on PATH")
class ExploreVariants(unittest.TestCase):
    def write(self, extra):
        with open(EXPLORE) as f:
            text = f.read()
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w") as f:
            f.write(text + extra)
        self.addCleanup(os.remove, path)
        return path

    def run_json(self, path, *args):
        r = subprocess.run([TOOL, "explore", path, "--json", *args], capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)
        return json.loads(r.stdout)["summary"]

    def test_variants_in_one_matrix(self):
        path = self.write('\n[[variant]]\nname = "as is"\n\n[[variant]]\nname = "two inner"\n'
                          'set = { "zone.digital.inner_fast" = 2 }\n')
        summ = {(s["variant"], s["layers"], s["build"]): s for s in self.run_json(path)}
        self.assertEqual(summ[("as is", 6, "through")]["pass"], 1)
        # two inner fast layers between grounds do not fit six layers with both outer layers fast
        self.assertEqual(summ[("two inner", 6, "through")]["pass"], 0)

    def test_variant_unset_and_bad_key(self):
        path = self.write('\n[[variant]]\nname = "no pours"\nunset = ["zone.power.pour_layers"]\n')
        summ = {(s["layers"], s["build"]): s for s in self.run_json(path)}
        self.assertEqual(summ[(6, "through")]["pass"], 1)
        bad = self.write('\n[[variant]]\nname = "bad"\nset = { "zone.nowhere.inner_fast" = 1 }\n')
        r = subprocess.run([TOOL, "explore", bad], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_asymmetric_counts_every_sequence(self):
        import explore
        doc = explore.load(EXPLORE)
        doc["rules"] = {"symmetric": False}
        # 6 layers through: 5 dielectrics, prepreg/core alternating, one choice each in the fixture
        self.assertEqual(len(explore.dielectric_sequences(6, "through", doc)), 1)
        doc["dielectric"].append({"name": "pp60", "kind": "prepreg", "um": 60, "er": 4.2, "laser": True})
        # three prepreg positions with two choices each, independent: 8 (symmetric: 4)
        self.assertEqual(len(explore.dielectric_sequences(6, "through", doc)), 8)
        doc["rules"]["symmetric"] = True
        self.assertEqual(len(explore.dielectric_sequences(6, "through", doc)), 4)

    def test_any_layer_reaches_every_level(self):
        import explore
        zone = {"name": "core", "soc": "bottom", "outer_fast": "soc", "outer_power_ok": ["top"],
                "soc_side_inner_fast": 1, "power_planes": 1}
        # L1 P, L2 G, L3 F, L4 G, L5 G, L6 F: the escape layer three levels in, not two
        v = ("P", "G", "F", "G", "G", "F")
        self.assertFalse(explore.needs_ok(v, 6, "2+N+2", zone))
        self.assertTrue(explore.needs_ok(v, 6, "any-layer", zone))
        self.assertEqual(explore.laser_levels(6, "any-layer"), 5)

    def test_escape_laser_only_rules_out_one_laser_level(self):
        import explore
        zone = {"name": "core", "soc": "bottom", "outer_fast": "soc", "soc_side_inner_fast": 1}
        v = ("F", "G", "F", "G", "G", "F")
        self.assertTrue(explore.needs_ok(v, 6, "1+N+1", zone))
        zone["escape_laser_only"] = True
        self.assertFalse(explore.needs_ok(v, 6, "1+N+1", zone))
        self.assertIn("escape_laser_only", explore.zone_failure(6, "1+N+1", zone))

    def test_outer_power_does_not_count_when_it_holds_parts(self):
        import explore
        zone = {"name": "core", "soc": "bottom", "outer_fast": "soc", "outer_power_ok": ["top"],
                "soc_side_inner_fast": 1, "power_planes": 2, "paired_power": 1}
        v = ("P", "P", "G", "F", "G", "F")
        self.assertTrue(explore.needs_ok(v, 6, "2+N+2", zone))
        zone["outer_power_counts"] = False
        self.assertFalse(explore.needs_ok(v, 6, "2+N+2", zone))
        zone["power_planes"] = 1
        self.assertTrue(explore.needs_ok(v, 6, "2+N+2", zone))
