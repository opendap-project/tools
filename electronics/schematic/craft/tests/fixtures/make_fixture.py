#!/usr/bin/env python3
"""Write the failing fixtures of the schematic craft check from the passing ones.

good/ is a copy of kicad-lib's examples/load-switch (CERN-OHL-W-2.0), with its drawing-sheet path
pointing at ../drawing-sheets/ here; it passes every check. This script copies it to bad/ and
breaks every rule the check implements, one edit each, and writes bad.kicad_sym, a symbol library
that breaks every symbol rule. Run with Python 3.9 or later from anywhere:

    python3 schematic/craft/tests/fixtures/make_fixture.py
"""

import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
GOOD, BAD = os.path.join(HERE, "good"), os.path.join(HERE, "bad")


def edit(name, *pairs):
    path = os.path.join(BAD, name)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for old, new in pairs:
        if old not in text:
            raise SystemExit(f"make_fixture: {old!r} not found in {name}")
        text = text.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def append(name, sexpr):
    path = os.path.join(BAD, name)
    with open(path, encoding="utf-8") as fh:
        text = fh.read().rstrip()
    assert text.endswith(")")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text[:-1] + sexpr + "\n)\n")


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def bad_symbol(name="Bad:BAD_US"):
    """A library symbol that breaks every symbol rule (CRAFT-121 to -131, -140)."""
    pins = []
    for i in range(66):  # CRAFT-126: more than 64 pins in one unit
        pins.append(f'(pin bidirectional line (at -10.16 {2.54 * i:g} 0) (length 2.54) '
                    f'(name "IO{i}" (effects (font (size 1.27 1.27)))) (number "{i + 10}" (effects (font (size 1.27 1.27)))))')
    pins += [
        '(pin input line (at 0.5 0 0) (length 1) (name "A" (effects (font (size 2 2)))) (number "1" (effects (font (size 1.27 1.27)))))',
        '(pin power_in line (at 0 10.16 270) (length 2.54) (name "GND" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))',
        '(pin unspecified line (at 10.16 0 180) (length 2.54) (name "B" (effects (font (size 1.27 1.27)))) (number "3" (effects (font (size 1.27 1.27)))))',
        '(pin passive line (at 10.16 2.54 180) (length 2.54) (hide yes) (name "C" (effects (font (size 1.27 1.27)))) (number "4" (effects (font (size 1.27 1.27)))))',
        '(pin input line (at 10.16 5.08 180) (length 2.54) (name "EN" (effects (font (size 1.27 1.27)))) (number "5" (effects (font (size 1.27 1.27)))))',
    ]
    return (f'(symbol "{name}" (exclude_from_sim no) (in_bom yes) (on_board yes) '
            '(property "Reference" "X" (at 0 0 0) (effects (font (size 1.27 1.27)))) '
            '(property "Value" "BAD" (at 0 0 0) (effects (font (size 1.27 1.27)))) '
            '(property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            '(property "Datasheet" "none" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            '(property "Description" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            f'(symbol "{name.split(":")[-1]}_0_1" (rectangle (start -7.62 7.62) (end 7.62 -7.62) '
            '(stroke (width 0.1) (type default)) (fill (type none)))) '
            f'(symbol "{name.split(":")[-1]}_1_1" ' + " ".join(pins) + "))")


def main():
    if os.path.exists(BAD):
        shutil.rmtree(BAD)
    shutil.copytree(GOOD, BAD)

    # project file: drawing sheet, text variables, ERC severities, an undeclared net class
    pro_path = os.path.join(BAD, "load-switch.kicad_pro")
    with open(pro_path, encoding="utf-8") as fh:
        pro = json.load(fh)
    pro["schematic"]["page_layout_descr_file"] = "custom.kicad_wks"                     # CRAFT-100
    pro["text_variables"]["OPENDAP_LICENCE"] = "MIT"                                    # CRAFT-101
    del pro["text_variables"]["OPENDAP_SOURCE"]
    pro["erc"]["rule_severities"]["four_way_junction"] = "ignore"                       # CRAFT-158
    pro["net_settings"]["classes"].append({"name": "EXTRA", "priority": 9})            # CRAFT-155
    with open(pro_path, "w", encoding="utf-8") as fh:
        json.dump(pro, fh, indent=2)

    # root: title block, paper, page order, sheet fields and pins, a lower-case net
    edit("load-switch.kicad_sch",
         ('(company "The OpenDAP Project")', '(company "ACME")'),                       # CRAFT-80
         ('(paper "A4")', '(paper "A2")'),                                              # CRAFT-81
         ('(property "Block" "blk.periph-switch"', '(property "Block" "blk.other"'),    # CRAFT-82
         ('(pin "VIN" input\n\t\t\t(at 109.22 78.74 90)', '(pin "VIN" input\n\t\t\t(at 101.6 83.82 180)'),  # CRAFT-113
         ('(pin "VOUT" output', '(pin "VOUT" input'),                                   # CRAFT-151
         ('(pin "EN" input', '(pin "EN_X" input'),                                      # CRAFT-112
         ('(label "EN_PERIPH"', '(label "nEn_periph"'),                                 # CRAFT-84
         ('(page "2")', '(page "9")'))                                                  # CRAFT-111
    # the overview: its role removed
    edit("interconnect.kicad_sch", ('(comment 1 "interconnect")', '(comment 1 "overview")'))  # CRAFT-110
    # the header block: notes, a rail renamed, a ground rotated, a global label, a four-way junction,
    # a diagonal wire, an unconnected part (ERC: pin not connected)
    edit("header.kicad_sch",
         ("3. Absent state: this block", "3. This block"),                              # CRAFT-115
         ('(property "Value" "PERIPH_3V3"', '(property "Value" "+3V3"'))                # CRAFT-85
    append("header.kicad_sch", f"""
	(global_label "SIGNAL_X" (shape input) (at 200.66 170.18 0) (effects (font (size 1.27 1.27)) (justify left)) (uuid "{uid(1)}"))
	(wire (pts (xy 200.66 149.86) (xy 205.74 149.86)) (stroke (width 0) (type default)) (uuid "{uid(2)}"))
	(wire (pts (xy 205.74 149.86) (xy 210.82 149.86)) (stroke (width 0) (type default)) (uuid "{uid(3)}"))
	(wire (pts (xy 205.74 144.78) (xy 205.74 149.86)) (stroke (width 0) (type default)) (uuid "{uid(4)}"))
	(wire (pts (xy 205.74 149.86) (xy 205.74 154.94)) (stroke (width 0) (type default)) (uuid "{uid(5)}"))
	(junction (at 205.74 149.86) (diameter 0) (color 0 0 0 0) (uuid "{uid(6)}"))
	(wire (pts (xy 215.9 160.02) (xy 226.06 170.18)) (stroke (width 0) (type default)) (uuid "{uid(7)}"))
	(text "| a | b |\\n| c | d |" (exclude_from_sim no) (at 200.66 180.34 0) (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{uid(8)}"))
""")                                                                                    # 150, 153, 154, 176
    # the switch block: fonts, colour, grid, fields, values, annotation, a bad symbol
    edit("periph-switch.kicad_sch",
         ('(text "Interface"\n\t\t(exclude_from_sim no)\n\t\t(at 19.05 104.14 0)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 2.54 2.54)',
          '(text "Interface"\n\t\t(exclude_from_sim no)\n\t\t(at 19.05 104.14 0)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(face "Arial")\n\t\t\t\t(size 3 3)'),  # CRAFT-102, -173
         ('(type dash)', '(type dash)\n\t\t\t(color 255 0 0 1)'),                       # CRAFT-103
         ('(junction\n\t\t(at 59.69 55.88)', '(junction\n\t\t(at 59.7 55.88)'),        # CRAFT-86
         ('(property "Value" "1 µF"', '(property "Value" "1uF 10V"'),                   # CRAFT-141: rating packed in
         ('(property "Value" "1 Ω"', '(property "Value" "1"'),                          # CRAFT-141: a bare shunt value
         ('(table_cell "Rail"', '(table_cell "Supply"'),                                # CRAFT-172
         ('(lib_symbols', '(lib_symbols\n' + bad_symbol()))                             # CRAFT-121 to -131
    path = os.path.join(BAD, "periph-switch.kicad_sch")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    def on_symbol(ref, old, new):
        nonlocal text
        i = text.index(f'(reference "{ref}")')
        start = text.rindex("\n\t(symbol\n", 0, i)
        block = text[start:i]
        if old not in block:
            raise SystemExit(f"make_fixture: {old!r} not in {ref}")
        text = text[:start] + block.replace(old, new, 1) + text[i:]

    on_symbol("U101", "(unit 1)", "(mirror x)\n\t\t(unit 1)")                         # CRAFT-133
    on_symbol("U101", '"Package_TO_SOT_SMD:SOT-363_SC-70-6"\n\t\t\t(at 104.14 58.42 0)\n\t\t\t(hide yes)',
              '"Package_TO_SOT_SMD:SOT-363_SC-70-6"\n\t\t\t(at 104.14 58.42 0)')       # CRAFT-143
    on_symbol("C101", '(property "Block"', '(property "MPN_2" "X1" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n\t\t(property "Block"')  # CRAFT-144
    on_symbol("C102", '"blk.periph-switch"', '"blk.header"')                          # CRAFT-145
    on_symbol("C102", "(dnp no)", "(dnp yes)")                                         # CRAFT-147
    on_symbol("R102", '(property "Block"', '(property "Constraints" "audio rules" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n\t\t(property "Sim.Library" "/abs/model.lib" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n\t\t(property "Block"')  # CRAFT-146, -148
    text = text.replace('(reference "C102")', '(reference "C202")', 1)                 # CRAFT-116
    text = text.replace('(reference "TP101")', '(reference "U102")', 1)               # CRAFT-157
    text = text.replace('(property "Reference" "TP101"', '(property "Reference" "U102"', 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    # CRAFT-114: an input label on the right half
    edit("periph-switch.kicad_sch", ('(hierarchical_label "EN"\n\t\t(shape input)\n\t\t(at 38.1 60.96 180)',
                                     '(hierarchical_label "EN"\n\t\t(shape input)\n\t\t(at 254 60.96 0)'))
    # power symbol: a ground rotated (CRAFT-85, second finding)
    edit("header.kicad_sch", ('(lib_id "power:GND")\n\t\t(at 129.54 81.28 0)', '(lib_id "power:GND")\n\t\t(at 129.54 81.28 90)'))

    with open(os.path.join(HERE, "bad.kicad_sym"), "w", encoding="utf-8") as fh:
        fh.write('(kicad_symbol_lib (version 20251024) (generator "make_fixture") (generator_version "10.0")\n'
                 + bad_symbol("BAD_US") + "\n)\n")
    print("written: bad/, bad.kicad_sym")


if __name__ == "__main__":
    main()
