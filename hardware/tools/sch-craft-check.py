#!/usr/bin/env python3
"""Check a KiCad schematic or symbol library against the schematic part of the OpenDAP craft standard.

Usage:
    sch-craft-check.py [--json] [--config FILE] [--target Qn] [--no-erc] ROOT.kicad_sch
    sch-craft-check.py [--json] [--target Qn] LIBRARY.kicad_sym

`odapkit craft check` runs it for .kicad_sch and .kicad_sym files. It needs only Python 3.9 or
later and kicad-cli (KiCad 10): it reads the S-expressions itself and runs kicad-cli for ERC and the
netlist. Standard: kicad-lib's docs/specs/pcb-craft-standard.md, section 9
(https://github.com/opendap-project/kicad-lib/blob/main/docs/specs/pcb-craft-standard.md). Each
check carries its CRAFT ID and its quality level (RFC 0010, Q0 to Q4).

For a schematic, give the project's root sheet: the check walks the hierarchy. Unless --no-erc, the
project's ERC (kicad-cli, with the project's own severities) is folded in: violations of the types
that implement a CRAFT rule count against that rule; any other ERC error counts at Q0 (an electrical
rule error); ERC warnings are listed but gate nothing. A symbol library is checked against the
symbol rules only.

Configuration, optional, next to the root sheet as <project>.craft.toml (or --config); the same file
the board check reads:

    version = 1
    target = "Q1"                       # the level this build targets (default Q1)

    [schematic]
    rails = ["VSYS", "vcc_1v8"]         # rail names of the power tree (CRAFT-84, -85, -150);
                                        # default: the power symbols found in the design
    netclasses = "../rules/netclasses.toml"   # CRAFT-155, relative to this file
    blocks = "../variants/blocks.toml"        # optional: [[block]] tables, as below

    [[schematic.block]]                 # one per feature block (RFC 0011): its interface nets
    id = "blk.example"
    interface = ["VIN", "VOUT", "EN", "PG"]

Exit status: 0 every item at or below the target passes, 1 otherwise, 2 usage error.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "craft"))
import craftrules  # noqa: E402  (tools/craft: the rules as data, tools/craft/rules.toml)

RULES = craftrules.load()
LEVELS = RULES.levels
SPEC = RULES.spec
KICAD_APP = os.environ.get("KICAD_APP", "/Applications/KiCad/KiCad.app")

GRID = RULES.limit("CRAFT-86", "grid_mm")                  # KiCad's 50 mil schematic grid
PIN_GRID = RULES.limit("CRAFT-122", "pin_grid_mm")         # KLC S4.1, 100 mil
TWO_PIN_GRID = RULES.limit("CRAFT-122", "two_pin_grid_mm")  # two-pin discretes, as KiCad's own
PIN_LENGTHS = RULES.series("CRAFT-121", "pin_length_min_mm", "pin_length_step_mm", "pin_length_max_mm")  # KLC S4.1
TEXT_SIZES = tuple(RULES.limit("CRAFT-173", "text_sizes_mm"))
SYM_TEXT = RULES.limit("CRAFT-129", "text_mm")             # KLC S3.2
SYM_TEXT_MIN = RULES.limit("CRAFT-129", "small_text_min_mm")  # KLC S3.2, pin names and numbers on small symbols
BODY_STROKE = RULES.limit("CRAFT-128", "body_stroke_mm")   # KLC S3.3
UNIT_MAX_PINS = RULES.limit("CRAFT-126", "unit_pins_max")
HOLDER = RULES.limit("CRAFT-80", "company")
LICENCES = tuple(RULES.limit("CRAFT-101", "licences"))
ROLES = ("root", "power", "interconnect")
DRAWING_SHEETS = ("opendap.kicad_wks",)
TEXT_VARS = ("OPENDAP_PROJECT", "OPENDAP_BOARD", "OPENDAP_LICENCE", "OPENDAP_SOURCE")
# CRAFT-130: KLC S6.1 designators, plus NT (net tie, KiCad's library) and FID (the boards' fiducials).
PREFIXES = ("A", "AE", "BT", "C", "D", "DS", "F", "FB", "FD", "FID", "FL", "H", "J", "JP", "K", "L",
            "LS", "M", "MK", "NT", "P", "Q", "R", "RN", "RT", "RV", "SW", "T", "TC", "TJ", "TP", "U",
            "Y", "Z")
PASSIVE_PREFIXES = ("R", "C", "L", "FB", "RN", "RT", "RV")
REQUIRED_FIELDS = ("Reference", "Value", "Footprint", "Datasheet", "Description", "Manufacturer",
                   "MPN", "Block")
SHOWN_FIELDS = ("Reference", "Value", "Spec")
# CRAFT-158: ERC checks the project must hold at error.
# KiCad 10 key names (eeschema/erc/erc_item.cpp); several default to warning or ignore.
ERC_ERRORS = ("endpoint_off_grid", "four_way_junction", "pin_not_connected", "label_dangling",
              "hier_label_mismatch", "power_pin_not_driven", "unresolved_variable", "unannotated",
              "duplicate_reference", "missing_power_pin", "multiple_net_names", "similar_labels",
              "similar_power", "similar_label_and_power", "same_local_global_label",
              "same_local_global_power", "no_connect_connected", "no_connect_dangling",
              "wire_dangling", "unconnected_wire_endpoint", "pin_not_driven", "undefined_netclass",
              "ground_pin_not_ground")
# ERC types that implement a CRAFT rule: violations count against that rule.
ERC_RULES = {"endpoint_off_grid": "CRAFT-86", "four_way_junction": "CRAFT-153",
             "pin_not_connected": "CRAFT-152", "hier_label_mismatch": "CRAFT-112",
             "unresolved_variable": "CRAFT-101", "similar_labels": "CRAFT-84",
             "similar_power": "CRAFT-85", "similar_label_and_power": "CRAFT-84",
             "multiple_net_names": "CRAFT-84", "undefined_netclass": "CRAFT-155"}
# Bus pins follow the KiCad library's bus pin order (KLC S4.2), not the input/output sides.
BUS_PIN = re.compile(r"^(SCL|SDA|SCK|SCLK|MOSI|MISO|SDI|SDO|CS|NSS|SS|TXD?|RXD?|D[+-]|CLK|DIO\d*|XT[IO]|XIN|XOUT|FB\d*)$", re.I)
# a ground name, with an optional parenthesised domain as NXP writes them: VSS(PAD), VSS(TX)
GROUND = re.compile(r"^(~\{)?([A-Z0-9_]*(GND|VSS)[A-Z0-9_]*(\([A-Z0-9_]+\))?|VEE[A-Z0-9_]*|V-|EP|PAD)\}?$", re.I)
NET_SIGNAL = re.compile(r"^[A-Z][A-Z0-9_]*(\[\d+\.\.\d+\])?$")
BUS_GROUP = re.compile(r"^[A-Z0-9_]+\{[A-Z0-9_ ]+\}$")       # a prefixed group: I2C2{SCL SDA}
BUS_ALIAS = re.compile(r"^\{([A-Z0-9_]+)\}$")               # an unprefixed alias: {DDR_CHA}


import schdesign  # noqa: E402  (tools/craft: the design checks' bodies)
import schmodel  # noqa: E402  (tools/craft: results, symbol rules, the schematic model)
import schreport  # noqa: E402  (tools/craft: the ERC fold, netlist names and the report)
import sexpr  # noqa: E402  (tools/craft: S-expressions)

for _m in (sexpr, schmodel, schreport, schdesign):
    _m.bind(globals())
from schmodel import Checks, Design, check_symbol  # noqa: E402
# The name rules, read by tests/test_craft_sch.py through this script.
from schmodel import active_low_problem, net_name_problem, rail_like, value_problem  # noqa: E402,F401
from schreport import fold_erc, netlist_names, report  # noqa: E402
from sexpr import kid, kids, load_toml, parse, prop_text  # noqa: E402


def symbol_checks():
    c = Checks()
    c.add("CRAFT-121", "Q2", "Pin length 2.54 mm, longer in 1.27 mm steps for long pin numbers, one per symbol")
    c.add("CRAFT-122", "Q1", "Pin connection points on the 2.54 mm grid")
    c.add("CRAFT-123", "Q2", "Supply pins at the top (converter inputs left), ground at the bottom, outputs right")
    c.add("CRAFT-124", "Q1", "Electrical pin types set for ERC: none unspecified; passive parts passive")
    c.add("CRAFT-125", "Q1", "No hidden pins")
    c.add("CRAFT-126", "Q2", f"Large parts split into units by function (at most {UNIT_MAX_PINS} pins per unit)")
    c.add("CRAFT-127", "Q3", "Inputs on the left, outputs on the right (ICs)")
    c.add("CRAFT-128", "Q2", "IC bodies: 0.254 mm stroke, background fill")
    c.add("CRAFT-129", "Q2", "Symbol text 1.27 mm (pin text down to 0.508 mm on small symbols)")
    c.add("CRAFT-130", "Q1", "Reference prefixes from the KLC set")
    c.add("CRAFT-131", "Q3", "IEC 60617 graphic forms")
    c.add("CRAFT-140", "Q1", "Required fields present and filled")
    return c


def check_library(path):
    with open(path, encoding="utf-8") as fh:
        lib = parse(fh.read())
    c = symbol_checks()
    syms = kids(lib, "symbol")
    for sym in syms:
        if kid(sym, "extends") is not None:
            continue  # derived symbols inherit the parent's graphics and pins
        check_symbol(sym[1], sym, c)
        fp = prop_text(sym, "Footprint", "")
        if fp:  # fully specified (KLC S5.1): orderable part data belongs with it
            for f in ("Manufacturer", "MPN"):
                if not prop_text(sym, f, ""):
                    c["CRAFT-140"].fail(f"{sym[1]}: fully specified symbol without {f}")
    for k in c.values():
        k.info.append(f"{len(syms)} symbol(s) in {os.path.basename(path)}")
    return list(c.values())


def check_design(design, cfg, cfg_dir):
    aliases = (design.pro.get("schematic") or {}).get("bus_aliases") or {}
    c = symbol_checks()
    c.add("CRAFT-80", "Q1", "Title block on every sheet, fields filled per the field map")
    c.add("CRAFT-81", "Q2", "Paper A4 landscape (A3 for overviews and dense blocks)")
    c.add("CRAFT-100", "Q1", "The OpenDAP drawing sheet set in the project")
    c.add("CRAFT-101", "Q2", "Project text variables for the drawing sheet defined")
    c.add("CRAFT-102", "Q2", "One typeface: KiCad's stroke font on the sheet")
    c.add("CRAFT-103", "Q2", "No hand-set colours: colour comes from the theme")
    c.add("CRAFT-82", "Q1", "One feature block per sheet, the sheet's Block field and file named after it")
    c.add("CRAFT-110", "Q2", "Root, power and interconnect overview sheets; no parts on the root")
    c.add("CRAFT-111", "Q3", "Page order follows the reading order of the root's block diagram")
    c.add("CRAFT-112", "Q1", "Sheet pins named exactly as the block interface and its hierarchical labels")
    c.add("CRAFT-113", "Q2", "Sheet pins by direction: inputs left, outputs right, supplies top, ground bottom")
    c.add("CRAFT-114", "Q3", "Hierarchical labels on the side of their direction")
    c.add("CRAFT-115", "Q2", "A Notes text box on every block sheet, with the block's absent state")
    c.add("CRAFT-116", "Q3", "References numbered by sheet, one hundred-block per sheet")
    c.add("CRAFT-133", "Q2", "IC symbols upright: not mirrored")
    c.add("CRAFT-141", "Q2", "Component values carry their unit, with an SI prefix and a space")
    c.add("CRAFT-143", "Q2", "Field visibility: Reference, Value, Spec shown; the rest hidden")
    c.add("CRAFT-144", "Q3", "Second source fields in pairs")
    c.add("CRAFT-145", "Q1", "Block field on every part equals its sheet's block")
    c.add("CRAFT-146", "Q3", "Constraints field holds constraint IDs")
    c.add("CRAFT-147", "Q1", "No DNP in the base design except alternates (Alternate field)")
    c.add("CRAFT-148", "Q3", "Simulation models referenced inside the project")
    c.add("CRAFT-84", "Q1", "Net names: UPPER_SNAKE_CASE, rails as the power tree, _N active low, _P/_N pairs")
    c.add("CRAFT-85", "Q1", "Rails through power symbols named as the power tree; ground symbols point down")
    c.add("CRAFT-86", "Q2", "Everything on the 1.27 mm grid; rotations in multiples of 90°")
    c.add("CRAFT-150", "Q1", "Global labels only for rails")
    c.add("CRAFT-151", "Q2", "Label shapes agree: sheet pin shape equals its hierarchical label's")
    c.add("CRAFT-152", "Q1", "No-connect flag on every unused pin", "ERC")
    c.add("CRAFT-153", "Q2", "No four-way junctions")
    c.add("CRAFT-154", "Q2", "Wires and buses orthogonal, default widths")
    c.add("CRAFT-155", "Q2", "Net classes in the project match the board's netclasses.toml")
    c.add("CRAFT-157", "Q3", "Nets between ICs are named")
    c.add("CRAFT-158", "Q1", "ERC severities: the craft checks at error in the project")
    c.add("CRAFT-172", "Q3", "Sheets with decoupling carry a rail table")
    c.add("CRAFT-173", "Q2", "Sheet text sizes from the closed set")
    c.add("CRAFT-176", "Q2", "Tables are KiCad tables, never text drawn as a table")

    schdesign.run(design, cfg, cfg_dir, c, aliases)
    return c


def main(argv):
    args = list(argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    as_json, no_erc = "--json" in args, "--no-erc" in args
    cfg_path, target, rest, i = None, None, [], 0
    while i < len(args):
        a = args[i]
        if a in ("--json", "--no-erc", "--no-drc"):
            pass
        elif a in ("--config", "--target"):
            if i + 1 >= len(args):
                print(f"sch-craft-check: {a} needs a value", file=sys.stderr)
                return 2
            cfg_path, target = (args[i + 1], target) if a == "--config" else (cfg_path, args[i + 1])
            i += 1
        elif a.startswith("-"):
            print(f"sch-craft-check: unknown option {a}", file=sys.stderr)
            return 2
        else:
            rest.append(a)
        i += 1
    if len(rest) != 1 or not rest[0].endswith((".kicad_sch", ".kicad_sym")):
        print("usage: sch-craft-check.py [--json] [--config FILE] [--target Qn] [--no-erc] ROOT.kicad_sch|LIB.kicad_sym",
              file=sys.stderr)
        return 2
    path = os.path.abspath(rest[0])
    if not os.path.exists(path):
        print(f"sch-craft-check: {path} not found", file=sys.stderr)
        return 2
    stem = os.path.splitext(path)[0]
    cfg_path = cfg_path or stem + ".craft.toml"
    cfg = load_toml(cfg_path)
    target = target or cfg.get("target", "Q1")
    if target not in LEVELS:
        print(f"sch-craft-check: target {target!r} is not one of {', '.join(LEVELS)}", file=sys.stderr)
        return 2
    if path.endswith(".kicad_sym"):
        return report(path, check_library(path), target, as_json, None, False)
    design = Design(path)
    checks = check_design(design, cfg.get("schematic", {}), os.path.dirname(os.path.abspath(cfg_path)))
    netlist_names(path, checks["CRAFT-157"])
    results = list(checks.values())
    if not no_erc:
        fold_erc(path, checks, results)

    def order(r):
        m = re.search(r"-(\d+)$", r.id)
        return (not r.id.startswith("CRAFT"), int(m.group(1)) if m else 0, r.id)
    results.sort(key=order)
    return report(path, results, target, as_json, cfg_path, not no_erc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
