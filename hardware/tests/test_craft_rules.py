"""Tests for the craft rules as data (tools/craft/rules.toml, tools/craft/craftrules.py).

- golden: tests/fixtures/craft-rules/pcb-craft-standard.md is kicad-lib's craft standard with its rule
  tables between `odapkit craft rules` markers; rendering it from rules.toml changes nothing, and
  without the markers it is byte-identical to the standard at kicad-lib 0157d44;
- every limit a check reads is stated in its rule's row, and every rule cites a source cell;
- drift: the board and schematic checks take their limits and levels from rules.toml, and each
  level written in a check equals the data.

Run from the repository root: python3 -m unittest discover tests
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
TOOLS = os.path.join(ROOT, "tools")
RULES = os.path.join(TOOLS, "craft", "rules.toml")
SCHEMA = os.path.join(ROOT, "schemas", "craft-rules.schema.json")
TOOLS_PROJECT = os.path.join(ROOT, "tools")
SPEC = os.path.join(HERE, "fixtures", "craft-rules", "pcb-craft-standard.md")
TOOL = os.path.join(ROOT, "bin", "odapkit-craft")
# sha256 of kicad/docs/specs/pcb-craft-standard.md at libs v0.1.1 (52fe7dd)
SPEC_SHA256 = "9386df44dc23d2ba00330d19c2591f2c867f0b611adc4c5fa72dae45ccb92349"

sys.path.insert(0, os.path.join(TOOLS, "craft"))
import craftrules  # noqa: E402

# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a fixture's git would act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]


def load_script(name):
    """A check script as a module. The board check imports pcbnew at load, which only KiCad's Python
    has: a stand-in is enough, as only its module-level constants are read."""
    stub = "pcbnew" not in sys.modules
    if stub:
        sys.modules["pcbnew"] = mock.MagicMock()
    try:
        return _load(name)
    finally:
        if stub:
            del sys.modules["pcbnew"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(TOOLS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def unmark(text):
    """The standard without the markers (and the blank line each adds)."""
    text = re.sub(r"<!-- >>> odapkit craft rules §[0-9.]+:[^\n]*-->\n\n", "", text)
    return text.replace("\n" + craftrules.END + "\n", "")


def spellings(v):
    """How the standard may write a number: 2.0 as 2 or 2.0, 0.3 as 0.3 or 0.30."""
    if isinstance(v, int):
        return {str(v)}
    return {repr(v), "%g" % v, "%.2f" % v}


class Golden(unittest.TestCase):
    def setUp(self):
        with open(SPEC, encoding="utf-8") as fh:
            self.text = fh.read()
        self.rules = craftrules.load()

    def test_render_is_unchanged(self):
        self.assertEqual(craftrules.render(self.text, self.rules), self.text)

    def test_unmarked_is_the_standard(self):
        digest = hashlib.sha256(unmark(self.text).encode("utf-8")).hexdigest()
        self.assertEqual(digest, SPEC_SHA256)

    def test_every_table_is_rendered(self):
        self.assertEqual(self.rules.sections(), ["3", "4", "5", "6", "7", "8", "9.1", "9.2", "9.3", "9.4", "9.5", "9.6"])
        self.assertEqual(len(self.rules.rules), 111)
        self.assertEqual(self.text.count(craftrules.END), 12)

    def test_edited_table_is_caught(self):
        stale = self.text.replace("| CRAFT-08 | Q2 |", "| CRAFT-08 | Q3 |", 1)
        self.assertNotEqual(stale, self.text)
        self.assertEqual(craftrules.render(stale, self.rules), self.text)

    def test_missing_marker_is_an_error(self):
        cut = re.sub(r"<!-- >>> odapkit craft rules §9\.6:.*?" + re.escape(craftrules.END), "", self.text, flags=re.S)
        with self.assertRaisesRegex(ValueError, "missing §9.6"):
            craftrules.render(cut, self.rules)

    def test_cli_check(self):
        if not shutil.which("python3"):
            self.skipTest("python3 not found")
        ok = subprocess.run([TOOL, "rules", "render", SPEC, "--check"], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        with tempfile.TemporaryDirectory() as d:
            stale = os.path.join(d, "spec.md")
            with open(stale, "w", encoding="utf-8") as fh:
                fh.write(self.text.replace("| CRAFT-08 | Q2 |", "| CRAFT-08 | Q3 |", 1))
            bad = subprocess.run([TOOL, "rules", "render", stale, "--check"], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("not current", bad.stderr)
            fix = subprocess.run([TOOL, "rules", "render", stale], capture_output=True, text=True)
            self.assertEqual(fix.returncode, 0, fix.stderr)
            with open(stale, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), self.text)


class Data(unittest.TestCase):
    def setUp(self):
        self.rules = craftrules.load()

    def test_schema(self):
        if not shutil.which("uv"):
            self.skipTest("uv not found")
        code = ("import json,sys,tomllib,jsonschema;"
                "jsonschema.Draft202012Validator(json.load(open(sys.argv[1]))).validate(tomllib.load(open(sys.argv[2],'rb')))")
        # jsonschema from the tools' environment (tools/pyproject.toml, tools/uv.lock).
        res = subprocess.run(["uv", "run", "--quiet", "--locked", "--project", TOOLS_PROJECT,
                              "python", "-c", code, SCHEMA, RULES], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_limits_are_stated_in_their_row(self):
        for r in self.rules.rules:
            text = r["rule"] + " " + r["value"]
            for key, v in r.get("limits", {}).items():
                items = v if isinstance(v, list) else [v]
                flat = [x for i in items for x in (i if isinstance(i, list) else [i])]
                for x in flat:
                    with self.subTest(rule=r["id"], limit=key, value=x):
                        if isinstance(x, str):
                            self.assertIn(x, text)
                        else:
                            self.assertTrue(any(re.search(r"(?<![0-9.])" + re.escape(s) + r"(?![0-9])", text) for s in spellings(x)),
                                            f"{r['id']} {key}={x} not in its row")

    def test_ids_unique_and_sourced(self):
        ids = [r["id"] for r in self.rules.rules]
        self.assertEqual(len(ids), len(set(ids)))
        for r in self.rules.rules:
            self.assertTrue(r["source"].strip(), r["id"])


class Drift(unittest.TestCase):
    """The checks read rules.toml; nothing in them restates a level or a limit differently."""

    @classmethod
    def setUpClass(cls):
        cls.rules = craftrules.load()
        cls.pcb = load_script("pcb-craft-check")
        cls.sch = load_script("sch-craft-check")

    def test_pcb_limits(self):
        r, m = self.rules, self.pcb
        self.assertEqual(m.LEVELS, r.levels)
        self.assertEqual(m.SPEC, r.spec)
        self.assertEqual(m.GRID, r.limit("CRAFT-30", "placement_grid_mm"))
        self.assertEqual(m.TEXT_SET, tuple(tuple(p) for p in r.limit("CRAFT-02", "text_set_mm")))
        self.assertEqual(m.SILK_EDGE, r.limit("CRAFT-08", "silk_to_edge_mm"))
        self.assertEqual(m.REF_NEAR, r.limit("CRAFT-12", "reference_to_courtyard_max_mm"))
        self.assertEqual(m.MAX_DRILLS, r.limit("CRAFT-46", "drill_sizes_max"))
        self.assertEqual(m.ZONE_MIN_WIDTH, r.limit("CRAFT-50", "zone_min_width_mm"))
        self.assertEqual(m.HOLDER, r.limit("CRAFT-21", "holder"))
        self.assertEqual(m.HOLDER, r.limit("CRAFT-24", "company"))
        self.assertEqual(m.LICENCES, tuple(r.limit("CRAFT-21", "licences")))

    def test_sch_limits(self):
        r, m = self.rules, self.sch
        self.assertEqual(m.LEVELS, r.levels)
        self.assertEqual(m.SPEC, r.spec)
        self.assertEqual(m.GRID, r.limit("CRAFT-86", "grid_mm"))
        self.assertEqual(m.TWO_PIN_GRID, r.limit("CRAFT-122", "two_pin_grid_mm"))
        self.assertEqual(m.PIN_GRID, r.limit("CRAFT-122", "pin_grid_mm"))
        self.assertEqual(m.PIN_LENGTHS, (2.54, 3.81, 5.08, 6.35, 7.62))
        self.assertEqual(m.TEXT_SIZES, tuple(r.limit("CRAFT-173", "text_sizes_mm")))
        self.assertEqual(m.SYM_TEXT, r.limit("CRAFT-129", "text_mm"))
        self.assertEqual(m.SYM_TEXT_MIN, r.limit("CRAFT-129", "small_text_min_mm"))
        self.assertEqual(m.BODY_STROKE, r.limit("CRAFT-128", "body_stroke_mm"))
        self.assertEqual(m.UNIT_MAX_PINS, r.limit("CRAFT-126", "unit_pins_max"))
        self.assertEqual(m.HOLDER, r.limit("CRAFT-80", "company"))
        self.assertEqual(m.LICENCES, tuple(r.limit("CRAFT-101", "licences")))

    def test_no_restated_limits(self):
        """The constants are read from the data, never written as literals."""
        names = ("GRID", "PIN_GRID", "TWO_PIN_GRID", "PIN_LENGTHS", "TEXT_SIZES", "SYM_TEXT", "SYM_TEXT_MIN", "BODY_STROKE",
                 "UNIT_MAX_PINS", "TEXT_SET", "SILK_EDGE", "REF_NEAR", "MAX_DRILLS", "ZONE_MIN_WIDTH",
                 "HOLDER", "LICENCES", "LEVELS", "SPEC")
        for script in ("pcb-craft-check.py", "sch-craft-check.py"):
            with open(os.path.join(TOOLS, script), encoding="utf-8") as fh:
                src = fh.read()
            for n in names:
                for m in re.finditer(r"^%s = (.*)$" % n, src, re.M):
                    with self.subTest(script=script, name=n):
                        self.assertIn("RULES", m.group(1), f"{script}: {n} = {m.group(1)}")

    def test_levels_in_checks_match(self):
        seen = 0
        for script in ("pcb-craft-check.py", "sch-craft-check.py"):
            with open(os.path.join(TOOLS, script), encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(r'"(CRAFT-[0-9]+)", "(Q[0-9])"', src):
                seen += 1
                with self.subTest(script=script, rule=m.group(1)):
                    self.assertEqual(m.group(2), self.rules.level(m.group(1)))
        self.assertGreater(seen, 70)

    def test_levels_in_sch_tests_match(self):
        sch = load_test("test_craft_sch")
        for table in (sch.SCHEMATIC, sch.SYMBOL):
            for rid, lv in table.items():
                with self.subTest(rule=rid):
                    self.assertEqual(lv, self.rules.level(rid))


def load_test(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    unittest.main()
