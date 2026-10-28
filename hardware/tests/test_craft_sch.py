"""Tests for the schematic and symbol part of bin/odapkit-craft (tools/sch-craft-check.py).

Fixtures in tests/fixtures/craft-sch/: good/ is a copy of kicad-lib's examples/load-switch and must
pass every check and reach Q4; bad/ and bad.kicad_sym are written from it by make_fixture.py and must
fail every check. The S-expression checks need only Python; the ERC fold and the netlist check need
kicad-cli (KiCad 10) and are skipped without it.

Run from the repository root: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import shutil
import subprocess
import unittest

# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a fixture's git would act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "bin", "odapkit-craft")
FIX = os.path.join(HERE, "fixtures", "craft-sch")
APP = os.environ.get("KICAD_APP", "/Applications/KiCad/KiCad.app")
HAVE_CLI = bool(os.path.exists(os.path.join(APP, "Contents", "MacOS", "kicad-cli")) or shutil.which("kicad-cli"))
GOOD = os.path.join(FIX, "good", "load-switch.kicad_sch")
BAD = os.path.join(FIX, "bad", "load-switch.kicad_sch")

# Every check the script runs on a schematic, with its level; each passes on good and fails on bad.
SCHEMATIC = {
    "CRAFT-80": "Q1", "CRAFT-81": "Q2", "CRAFT-82": "Q1", "CRAFT-84": "Q1", "CRAFT-85": "Q1",
    "CRAFT-86": "Q2", "CRAFT-100": "Q1", "CRAFT-101": "Q2", "CRAFT-102": "Q2", "CRAFT-103": "Q2",
    "CRAFT-110": "Q2", "CRAFT-111": "Q3", "CRAFT-112": "Q1", "CRAFT-113": "Q2", "CRAFT-114": "Q3",
    "CRAFT-115": "Q2", "CRAFT-116": "Q3", "CRAFT-133": "Q2", "CRAFT-141": "Q2", "CRAFT-143": "Q2",
    "CRAFT-144": "Q3", "CRAFT-145": "Q1", "CRAFT-146": "Q3", "CRAFT-147": "Q1", "CRAFT-148": "Q3",
    "CRAFT-150": "Q1", "CRAFT-151": "Q2", "CRAFT-152": "Q1", "CRAFT-153": "Q2", "CRAFT-154": "Q2",
    "CRAFT-155": "Q2", "CRAFT-157": "Q3", "CRAFT-158": "Q1", "CRAFT-172": "Q3", "CRAFT-173": "Q2",
    "CRAFT-176": "Q2",
}
SYMBOL = {
    "CRAFT-121": "Q2", "CRAFT-122": "Q1", "CRAFT-123": "Q2", "CRAFT-124": "Q1", "CRAFT-125": "Q1",
    "CRAFT-126": "Q2", "CRAFT-127": "Q3", "CRAFT-128": "Q2", "CRAFT-129": "Q2", "CRAFT-130": "Q1",
    "CRAFT-131": "Q3", "CRAFT-140": "Q1",
}
NEEDS_CLI = {"CRAFT-152", "CRAFT-157"}  # ERC and the netlist


def run(*args):
    return subprocess.run([TOOL, *args], capture_output=True, text=True)


def results(report):
    return {r["id"]: r for lv in report["levels"].values() for r in lv}


class SchematicCraft(unittest.TestCase):
    def check(self, path, *extra):
        r = run("check", "--json", *extra, path)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r.returncode, json.loads(r.stdout)

    @unittest.skipIf(not HAVE_CLI, "kicad-cli not found")
    def test_good_passes_everything(self):
        code, report = self.check(GOOD)
        got = results(report)
        for cid, level in {**SCHEMATIC, **SYMBOL}.items():
            self.assertEqual(got[cid]["level"], level, cid)
            self.assertEqual(got[cid]["status"], "pass", (cid, got[cid]["findings"]))
        self.assertEqual(got["ERC"]["status"], "pass", got["ERC"]["findings"])
        self.assertEqual(report["reached"], "Q4")
        self.assertEqual(report["target"], "Q4")  # from load-switch.craft.toml
        self.assertEqual(code, 0)

    def test_good_without_erc(self):
        code, report = self.check(GOOD, "--no-erc")
        got = results(report)
        for cid in set(SCHEMATIC) - NEEDS_CLI:
            self.assertEqual(got[cid]["status"], "pass", (cid, got[cid]["findings"]))
        self.assertNotIn("ERC", got)

    @unittest.skipIf(not HAVE_CLI, "kicad-cli not found")
    def test_bad_fails_everything(self):
        code, report = self.check(BAD)
        got = results(report)
        for cid in {**SCHEMATIC, **SYMBOL}:
            self.assertEqual(got[cid]["status"], "fail", cid)
        self.assertEqual(got["ERC"]["level"], "Q0")
        self.assertEqual(got["ERC"]["status"], "fail")  # unconnected wires and pins
        self.assertEqual(got["CRAFT-152"]["enforcement"], "ERC")
        self.assertIsNone(report["reached"])  # a Q0 item fails
        self.assertEqual(code, 1)

    def test_bad_without_erc(self):
        code, report = self.check(BAD, "--no-erc")
        got = results(report)
        for cid in set(SCHEMATIC) - NEEDS_CLI:
            self.assertEqual(got[cid]["status"], "fail", cid)
        self.assertEqual(report["reached"], "Q0")
        self.assertEqual(code, 1)

    def test_target_and_debt(self):
        _, report = self.check(BAD, "--no-erc", "--target", "Q0")
        self.assertTrue(report["target_met"])
        self.assertIn("CRAFT-111", report["debt"])
        _, report = self.check(GOOD, "--no-erc", "--target", "Q1")
        self.assertEqual(report["debt"], [])

    def test_symbol_libraries(self):
        code, report = self.check(os.path.join(FIX, "good", "example.kicad_sym"))
        got = results(report)
        self.assertEqual(set(got), set(SYMBOL))
        for cid in SYMBOL:
            self.assertEqual(got[cid]["status"], "pass", (cid, got[cid]["findings"]))
        self.assertEqual(code, 0)
        code, report = self.check(os.path.join(FIX, "bad.kicad_sym"))
        got = results(report)
        for cid in SYMBOL:
            self.assertEqual(got[cid]["status"], "fail", cid)
        self.assertEqual(code, 1)

    def test_usage(self):
        self.assertEqual(run("check", "--target", "Q9", GOOD).returncode, 2)
        self.assertEqual(run("check", os.path.join(FIX, "missing.kicad_sch")).returncode, 2)


def load_check():
    spec = importlib.util.spec_from_file_location(
        "sch_craft_check", os.path.join(HERE, "..", "tools", "sch-craft-check.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ValueFormat(unittest.TestCase):
    """CRAFT-141: a component value is a number, one space, an SI prefix and the unit."""

    GOOD = [("22 Ω", "Ω"), ("4.7 kΩ", "Ω"), ("5 mΩ", "Ω"), ("0 Ω", "Ω"), ("1 Ω", "Ω"), ("100 nF", "F"),
            ("10 pF", "F"), ("2.2 µH", "H"), ("24.576 MHz", "Hz"), ("32.768 kHz", "Hz")]
    BAD = [("1", "Ω"), ("4.7k", "Ω"), ("100n", "F"), ("2.2u", "H"), ("4k7", "Ω"), ("1R", "Ω"),
           ("4.7kΩ", "Ω"), ("100 uF", "F"), ("2.2 \u03bcH", "H"), ("1 \u2126", "Ω"), ("4,7 kΩ", "Ω"),
           ("0.1 µF", "Ω"), ("1uF 10V", "F"), ("24.576M", "Hz"), (".1 µF", "F"), ("1  Ω", "Ω")]

    def test_accepts_the_format(self):
        check = load_check()
        for v, unit in self.GOOD:
            self.assertIsNone(check.value_problem(v, unit), v)

    def test_rejects_everything_else(self):
        check = load_check()
        for v, unit in self.BAD:
            self.assertIsNotNone(check.value_problem(v, unit), v)



class ActiveLowNets(unittest.TestCase):
    """CRAFT-84: active low is the suffix _N on a net name; overbars belong to pin names only."""

    # names that must pass: the _N suffix, differential pairs, and names that merely start with N or
    # end in _B (the T1's NFC controller nets, LPDDR4X channel-B names) - the 2026-10-25 false positives
    GOOD = ["RESET_N", "FP_SPI_CS_N", "ACTION_BTN_N", "USB_D_P", "USB_D_N",
            "NFC_VEN", "NFC_IRQ", "NFC_DWL_REQ", "NFC_FD_N", "NTC_SENSE", "NVME_PERST_N",
            "DDR_CA0_B", "DDR_CS0_B", "DDR_CK_B_P", "DDR_CK_B_N", "DDR_DQ[0..15]_B", "LPDDR4_DQS0_B",
            "DDR_CHA_CS0_N", "DDR_CHA_CKE0", "HP_L", "I2S_BCLK", "PMIC_BUCK1_SW", "SPI0{SCK MOSI MISO}",
            "ENABLE", "INTERNAL_MIC", "BOOTSEL"]
    # names that must fail, one per way of marking active low other than _N
    BAD = ["~{RESET}", "SPI_~{CS}", "RESET#", "CS!", "nRESET", "nCS",
           "NRESET", "NRST", "SPI_NCS", "PMIC_NIRQ", "NFC_NRST", "NEN",
           "RSTN", "PMIC_RESETB", "CSB", "SPI_CS0N", "INTB",
           "CS_BAR"]

    def test_accepts_the_convention(self):
        check = load_check()
        for name in self.GOOD:
            self.assertIsNone(check.active_low_problem(name), name)

    def test_rejects_other_markers(self):
        check = load_check()
        for name in self.BAD:
            self.assertIsNotNone(check.active_low_problem(name), name)


class RailNames(unittest.TestCase):
    """CRAFT-113: which sheet pins count as supplies (top edge, or right for a rail the block makes)."""

    RAILS = ["vcc3v3_sys", "vcca1v8_pmu"]
    GOOD = ["vcc3v3_sys", "VBUS", "VSYS_LV", "VBAT", "P5V1_SRC", "HAT_5V", "FP_3V3"]
    BAD = ["VOLDN_N", "VBUS_DET_N", "I2C2_SDA", "PWRON_N", "SARADC_VOLUP", "ACTION_BTN_N"]

    def test_rails_and_rail_shaped_names(self):
        check = load_check()
        for name in self.GOOD:
            self.assertTrue(check.rail_like(name, self.RAILS), name)

    def test_signals_including_active_low_names_starting_with_v(self):
        check = load_check()
        for name in self.BAD:
            self.assertFalse(check.rail_like(name, self.RAILS), name)


class GroundNames(unittest.TestCase):
    """CRAFT-113, -123: ground pins and nets, including NXP's parenthesised domains."""

    GOOD = ["GND", "AGND", "PGND", "VSS", "VSS_1", "VSS(PAD)", "VSS(A)", "VSS(TX)", "VEE", "EP", "~{GND}"]
    BAD = ["VDD", "VDD(PAD)", "VSS(", "VSYS", "GNDX(A B)", "I2C2_SDA"]

    def test_grounds(self):
        check = load_check()
        for name in self.GOOD:
            self.assertTrue(check.GROUND.match(name), name)

    def test_not_grounds(self):
        check = load_check()
        for name in self.BAD:
            self.assertFalse(check.GROUND.match(name), name)


class BusNames(unittest.TestCase):
    """CRAFT-84: buses as vectors, prefixed groups, or unprefixed groups of a project bus alias."""

    ALIASES = {"DDR_CHA": ["DDR_A0_A"], "I2C0": ["I2C0_SCL", "I2C0_SDA"]}
    GOOD = ["{DDR_CHA}", "{I2C0}", "I2C2{SCL SDA}", "D[0..7]", "EMMC_D0"]
    BAD = ["{DDR_CHC}", "{ddr_cha}", "{DDR CHA}", "{}", "{I2C0 }", "i2c0{SCL}"]

    def test_accepts_vectors_groups_and_known_aliases(self):
        check = load_check()
        for name in self.GOOD:
            self.assertIsNone(check.net_name_problem(name, self.ALIASES), name)

    def test_rejects_unknown_aliases_and_malformed_groups(self):
        check = load_check()
        for name in self.BAD:
            self.assertIsNotNone(check.net_name_problem(name, self.ALIASES), name)

    def test_alias_needs_the_project(self):
        check = load_check()
        self.assertIsNotNone(check.net_name_problem("{DDR_CHA}", {}))


if __name__ == "__main__":
    unittest.main()
