"""Tests for tools/craft/sexpr.py, the one S-expression model of both craft checks, without KiCad.

Run from the repository root: python3 -m unittest tests.test_sexpr_model
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "craft"))
import pcbmodel  # noqa: E402
import sexpr  # noqa: E402

BOARD = '''(kicad_pcb (version 20241229) (generator "pcbnew")
  (setup
    (stackup
      (layer "F.SilkS" (type "Top Silk Screen") (color "White"))
      (layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (color "Green") (thickness 0.01))
      (layer "B.SilkS" (type "Bottom Silk Screen") (color "White"))
      (copper_finish "ENIG")
    )
  )
  (footprint "R_0402" (layer "F.Cu") (property "Reference" "R1 \\"x\\" (y)"))
)
'''


class Sexpr(unittest.TestCase):
    def test_parse_nested_strings_and_escapes(self):
        tree = sexpr.parse('(a "b \\"c\\" (d)" (e 1 2) f)')
        self.assertEqual(tree, ["a", 'b "c" (d)', ["e", "1", "2"], "f"])

    def test_child_lookups(self):
        tree = sexpr.parse(BOARD)
        self.assertEqual(sexpr.val(tree, "version"), "20241229")
        self.assertEqual(len(sexpr.kids(sexpr.kid(sexpr.kid(tree, "setup"), "stackup"), "layer")), 5)
        self.assertEqual(sexpr.prop_text(sexpr.kid(tree, "footprint"), "Reference"), 'R1 "x" (y)')

    def test_find_form_returns_the_first_balanced_form(self):
        form = sexpr.find_form(BOARD, "stackup")
        self.assertEqual(form[0], "stackup")
        self.assertEqual(sexpr.val(form, "copper_finish"), "ENIG")
        self.assertIsNone(sexpr.find_form(BOARD, "stack"))  # a head is a whole token
        self.assertIsNone(sexpr.find_form("(kicad_pcb (setup))", "stackup"))

    def test_find_form_skips_parentheses_in_strings(self):
        form = sexpr.find_form('(x (y "a ) b" (z 1)) (w))', "y")
        self.assertEqual(form, ["y", "a ) b", ["z", "1"]])

    def test_stackup_colours(self):
        self.assertEqual(pcbmodel.stackup_colours(BOARD),
                         {"F.SilkS": "White", "F.Mask": "Green", "B.Mask": "Green", "B.SilkS": "White"})
        self.assertEqual(pcbmodel.stackup_colours("(kicad_pcb (setup))"), {})

    def test_load_toml(self):
        self.assertEqual(sexpr.load_toml(None), {})
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('version = 1\ntarget = "Q2"\n')
        try:
            self.assertEqual(sexpr.load_toml(f.name, "pcb-craft-check"), {"version": 1, "target": "Q2"})
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
