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

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

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


def net_name_problem(name, aliases=None):
    """Why a label name breaks CRAFT-84, or None. A bus is a vector (D[0..7]), a prefixed group
    (I2C2{SCL SDA}) or an unprefixed group naming one of the project's bus aliases ({DDR_CHA},
    whose members keep their own names); the alias must exist in the project's
    schematic.bus_aliases."""
    m = BUS_ALIAS.match(name)
    if m:
        if m.group(1) not in (aliases or {}):
            return f"names bus alias {m.group(1)!r}, which the project does not define"
        return None
    low = active_low_problem(name)
    if low:
        return low
    if not NET_SIGNAL.match(name) and not BUS_GROUP.match(name):
        return "is not UPPER_SNAKE_CASE"
    return None
# CRAFT-141: number, one space, SI prefix, unit ("4.7 kΩ", "100 nF", "24.576 MHz"); µ is U+00B5, Ω U+03A9.
VALUE_UNIT = {"R": "Ω", "C": "F", "L": "H", "Y": "Hz"}
UNIT_VALUE = re.compile(r"^(0|[1-9]\d*)(\.\d+)? [pnµmkMG]?(Ω|F|H|Hz)$")
URL = re.compile(r"^https://\S+$")
CONSTRAINT = re.compile(r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*-\d+( [A-Z][A-Z0-9]*(-[A-Z0-9]+)*-\d+)*$")


# ---------------------------------------------------------------- S-expressions

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text):
    """Parse one S-expression into nested lists of strings."""
    stack, cur, pos = [], [], 0
    while True:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise ValueError(f"unreadable S-expression at offset {pos}")
            break
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done, cur = cur, stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            s = m.group(3)
            cur.append(re.sub(r'\\(.)', lambda x: {"n": "\n", "t": "\t"}.get(x.group(1), x.group(1)), s))
        else:
            cur.append(m.group(4))
    return cur[0] if cur else []


def kids(node, name):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def kid(node, name):
    for c in node[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def val(node, name, default=None):
    k = kid(node, name)
    return k[1] if k is not None and len(k) > 1 else default


def xy(node):
    at = kid(node, "at")
    if at is None:
        return None
    return float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0


def flag(node, name):
    """True for (name yes), a bare `name` token, or (name); (power global) and (power local) also
    count for `power` — KiCad writes all of these across versions."""
    for c in node[1:]:
        if c == name:
            return True
        if isinstance(c, list) and c and c[0] == name:
            return len(c) == 1 or c[1] in ("yes", "global", "local")
    return False


def props(node):
    out = {}
    for p in kids(node, "property"):
        out[p[1]] = p
    return out


def prop_text(node, name, default=None):
    p = props(node).get(name)
    return p[2] if p is not None and len(p) > 2 else default


def hidden(prop):
    eff = kid(prop, "effects")
    return flag(prop, "hide") or (eff is not None and flag(eff, "hide"))


def font_of(node):
    eff = kid(node, "effects")
    return kid(eff, "font") if eff is not None else None


def text_size(node):
    f = font_of(node)
    size = kid(f, "size") if f is not None else None
    return float(size[1]) if size is not None else None


def on_grid(v, g=GRID):
    return abs(v / g - round(v / g)) < 1e-3


def fmt(x, y):
    return f"({x:g}, {y:g})"


# ---------------------------------------------------------------- results

class Check:
    def __init__(self, cid, level, title, enforcement="CHK"):
        self.id, self.level, self.title, self.enforcement = cid, level, title, enforcement
        self.findings, self.info, self.applicable = [], [], True

    def fail(self, msg):
        self.findings.append(msg)

    @property
    def status(self):
        if not self.applicable:
            return "n/a"
        return "fail" if self.findings else "pass"

    def as_dict(self):
        return {"id": self.id, "level": self.level, "title": self.title,
                "enforcement": self.enforcement, "status": self.status,
                "findings": self.findings, "exempt": [], "info": self.info}


class Checks(dict):
    def add(self, cid, level, title, enforcement="CHK"):
        self[cid] = Check(cid, level, title, enforcement)
        return self[cid]


# ---------------------------------------------------------------- symbols (libraries and embedded)

# CRAFT-84: functions that are commonly active low, so that an N prefix (NRESET, SPI_NCS) or an N/B
# suffix (RSTN, PMIC_RESETB) on them is read as an active-low marker. A bare N prefix is not: NFC_IRQ,
# NFC_VEN start with N, and _B ends LPDDR channel-B names (DDR_CA0_B).
ACTIVE_LOW_FUNC = ("RST|RESET|PRST|POR|CS|SS|CE|OE|WE|WR|RD|IRQ|INT|EN|PD|PWDN|SHDN|WP|HOLD|ALERT|"
                   "FAULT|FLT|PG|PGOOD|BUSY|RDY|READY|STBY|WAKE|BOOT")
ACTIVE_LOW_TOKEN = re.compile(rf"^(N({ACTIVE_LOW_FUNC})\d*|({ACTIVE_LOW_FUNC})\d*[NB]|BAR)$")


def rail_like(name, rails=()):
    """CRAFT-113: a sheet pin that carries a supply. A declared rail, or a name shaped like one
    (VBUS, VSYS_LV, P5V1_SRC); an active-low signal (_N, CRAFT-84) never is one, whatever its
    first letter (VOLDN_N, the T1's Vol- button, was read as a rail on 2026-10-26)."""
    if name in rails:
        return True
    if name.upper().endswith("_N"):
        return False
    return bool(re.match(r"^(V[A-Z0-9_]*|.*\d+V\d*.*)$", name, re.I))


def active_low_problem(name):
    """Why a net name marks active low other than with the suffix _N (CRAFT-84), or None."""
    if "~{" in name:
        return "uses an overbar; active-low nets end in _N"
    if re.search(r"[#!]", name):
        return "uses '#' or '!'; active low is the suffix _N"
    if re.match(r"^n[A-Z]", name):
        return "uses an 'n' prefix; active low is the suffix _N"
    base = re.sub(r"(\[\d+\.\.\d+\]|\{.*\})$", "", name)
    for token in base.split("_"):
        if ACTIVE_LOW_TOKEN.match(token):
            return f"marks active low in {token!r}; active low is the suffix _N"
    return None


def value_problem(v, unit):
    """Why a passive's Value breaks CRAFT-141, or None: it must read "<number> <SI prefix><unit>"."""
    m = UNIT_VALUE.match(v)
    if m and m.group(3) == unit:
        return None
    if "\u2126" in v:
        return "use Ω (U+03A9), not the ohm sign U+2126"
    if "\u03bc" in v:
        return "use µ (U+00B5), not the Greek mu U+03BC"
    if re.match(r"^[\d.]+ ?u", v):
        return "use µ for micro, not u"
    if re.match(r"^[\d.]+[pnµmkMG]?(Ω|F|H|Hz)$", v):
        return "one space between the number and the unit"
    if m:
        return f"unit {m.group(3)} on a part that takes {unit}"
    example = {"Ω": "4.7 kΩ", "F": "100 nF", "H": "2.2 µH", "Hz": "24.576 MHz"}[unit]
    return f"number, space, SI prefix and unit, such as {example}"


def ref_prefix(ref):
    m = re.match(r"#?([A-Za-z]+)", ref or "")
    return m.group(1).upper() if m else ""


def lib_units(sym):
    """Units of a library symbol: {unit number: [pins]}; unit 0 is common to all units."""
    units = {}
    for sub in kids(sym, "symbol"):
        m = re.search(r"_(\d+)_(\d+)$", sub[1])
        unit = int(m.group(1)) if m else 0
        units.setdefault(unit, []).extend(kids(sub, "pin"))
    return units


def lib_pins(sym):
    return [p for pins in lib_units(sym).values() for p in pins]


def lib_body_rects(sym):
    out = []
    for sub in kids(sym, "symbol"):
        out += kids(sub, "rectangle")
    return out


def pin_side(pin):
    """Side of the body the pin sits on: KiCad's pin angle points from the connection to the body."""
    a = round(xy(pin)[2]) % 360
    return {0: "left", 180: "right", 90: "bottom", 270: "top"}.get(a, "angled")


def pin_name(pin):
    n = kid(pin, "name")
    return n[1] if n is not None else ""


def check_symbol(name, sym, c, generic_ok=True):
    """Run the symbol rules on one library symbol; c is a Checks map. Returns nothing."""
    is_power = flag(sym, "power")
    ref = prop_text(sym, "Reference", "")
    pfx = ref_prefix(ref)
    pins = lib_pins(sym)
    if is_power or ref.startswith("#"):
        for p in pins:
            if flag(p, "hide"):
                c["CRAFT-125"].fail(f"{name}: power symbol pin hidden (KLC S7.1: visible since KiCad 8)")
        return
    if pfx not in PREFIXES:
        c["CRAFT-130"].fail(f"{name}: reference prefix {ref!r} not in the set")
    lengths = set()
    for p in pins:
        x, y, _ = xy(p)
        num = val(p, "number", "?")
        length = float(val(p, "length", "0"))
        lengths.add(round(length, 3))
        g = PIN_GRID if len(pins) > 2 else TWO_PIN_GRID  # two-pin discretes: 1.27 mm, as KiCad's own (KLC S4.1)
        if not (on_grid(x, g) and on_grid(y, g)):
            c["CRAFT-122"].fail(f"{name} pin {num}: connection point {fmt(x, y)} off the {g} mm grid")
        if flag(p, "hide"):
            c["CRAFT-125"].fail(f"{name} pin {num} ({pin_name(p)}): hidden")
        ptype = p[1] if len(p) > 1 else "?"
        if ptype == "unspecified":
            c["CRAFT-124"].fail(f"{name} pin {num} ({pin_name(p)}): electrical type unspecified")
        if pfx in PASSIVE_PREFIXES and ptype != "passive":
            c["CRAFT-124"].fail(f"{name} pin {num}: {ptype} on a passive part (must be passive)")
        side = pin_side(p)
        nm = pin_name(p)
        if ptype == "power_in" and GROUND.match(nm) and side != "bottom":
            c["CRAFT-123"].fail(f"{name} pin {num} ({nm}): ground pin on the {side}, not the bottom")
        elif ptype == "power_in" and not GROUND.match(nm) and side not in ("top", "left"):
            c["CRAFT-123"].fail(f"{name} pin {num} ({nm}): supply input on the {side} (top, or left for a converter)")
        elif ptype == "power_out" and side not in ("right", "top"):
            c["CRAFT-123"].fail(f"{name} pin {num} ({nm}): supply output on the {side} (right)")
        bus = bool(BUS_PIN.match(re.sub(r"~\{|\}", "", nm)))
        black_box = len(pins) > 2 and pfx not in PASSIVE_PREFIXES + ("J", "P", "TP")
        if black_box and ptype == "input" and side == "right" and not bus:
            c["CRAFT-127"].fail(f"{name} pin {num} ({nm}): input on the right")
        if black_box and ptype in ("output", "open_collector", "open_emitter") and side == "left" and not bus:
            c["CRAFT-127"].fail(f"{name} pin {num} ({nm}): output on the left")
        for part in ("name", "number"):
            node = kid(p, part)
            s = text_size(node) if node is not None else None
            if s is not None and not (abs(s - SYM_TEXT) < 1e-3 or (SYM_TEXT_MIN - 1e-3 <= s < SYM_TEXT)):
                c["CRAFT-129"].fail(f"{name} pin {num}: {part} text {s} mm")
    sized = pfx not in PASSIVE_PREFIXES and len(pins) > 2  # KLC S4.1: short pins on simple discretes
    if sized and len(lengths) > 1:
        c["CRAFT-121"].fail(f"{name}: {len(lengths)} pin lengths {sorted(lengths)} (one per symbol)")
    for length in lengths:
        if sized and length not in PIN_LENGTHS:
            c["CRAFT-121"].fail(f"{name}: pin length {length} mm not in {PIN_LENGTHS}")
    for unit, upins in lib_units(sym).items():
        if len(upins) > UNIT_MAX_PINS:
            c["CRAFT-126"].fail(f"{name} unit {unit}: {len(upins)} pins (at most {UNIT_MAX_PINS}; split by function)")
    for r in lib_body_rects(sym):
        st = kid(r, "stroke")
        w = float(val(st, "width", "0")) if st is not None else 0.0
        fill = val(kid(r, "fill"), "type", "none") if kid(r, "fill") is not None else "none"
        big = abs(float(kid(r, "start")[1]) - float(kid(r, "end")[1])) > 2.54
        if big and len(pins) > 2 and pfx not in PASSIVE_PREFIXES + ("J", "P", "TP"):
            if abs(w - BODY_STROKE) > 1e-3:
                c["CRAFT-128"].fail(f"{name}: body stroke {w} mm (KLC S3.3: {BODY_STROKE} mm)")
            if fill != "background":
                c["CRAFT-128"].fail(f"{name}: black-box body fill {fill!r} (KLC S3.3: background)")
    for fname, p in props(sym).items():
        s = text_size(p)
        if fname in ("Reference", "Value") and s is not None and abs(s - SYM_TEXT) > 1e-3:
            c["CRAFT-129"].fail(f"{name}: {fname} text {s} mm")
    if "_US" in name.split(":")[-1]:
        c["CRAFT-131"].fail(f"{name}: US graphic form; OpenDAP draws IEC 60617 forms")
    ds = prop_text(sym, "Datasheet", "")
    desc = prop_text(sym, "Description", "")
    if not desc:
        c["CRAFT-140"].fail(f"{name}: library symbol without a Description")
    if pfx not in PASSIVE_PREFIXES and pfx not in ("TP", "H", "FID", "FD", "NT", "JP") and not URL.match(ds or ""):
        if not (generic_ok and ds in ("~", "") and not prop_text(sym, "Footprint", "")):
            c["CRAFT-140"].fail(f"{name}: Datasheet {ds!r} is not an https URL")


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


# ---------------------------------------------------------------- the schematic

class Sheet:
    def __init__(self, path, node, inst_path, page, block_field=None, sheet_node=None):
        self.path, self.node, self.inst_path, self.page = path, node, inst_path, page
        self.block_field, self.sheet_node = block_field, sheet_node
        self.name = os.path.basename(path)
        tb = kid(node, "title_block")
        self.tb = {}
        if tb is not None:
            for k in tb[1:]:
                if k[0] == "comment":
                    self.tb[f"comment{k[1]}"] = k[2]
                else:
                    self.tb[k[0]] = k[1] if len(k) > 1 else ""
        self.role = self.tb.get("comment1", "")
        self.lib = {s[1]: s for s in kids(kid(node, "lib_symbols") or ["lib_symbols"], "symbol")}

    @property
    def is_block(self):
        return self.role.startswith("blk.")

    def symbols(self):
        return kids(self.node, "symbol")

    def reference(self, sym):
        for proj in kids(kid(sym, "instances") or ["instances"], "project"):
            for p in kids(proj, "path"):
                if p[1] == self.inst_path:
                    return val(p, "reference", prop_text(sym, "Reference"))
        return prop_text(sym, "Reference", "?")


class Design:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.dir = os.path.dirname(self.root)
        self.sheets = []
        self.sheet_symbols = []  # (parent Sheet, sheet node, child Sheet)
        node = self.load(self.root)
        page = "1"
        for inst in kids(kid(node, "sheet_instances") or ["x"], "path"):
            if inst[1] == "/":
                page = val(inst, "page", "1")
        self.walk(self.root, node, "/" + val(node, "uuid", ""), page, None, None, set())
        pro = os.path.splitext(self.root)[0] + ".kicad_pro"
        self.pro_path = pro if os.path.exists(pro) else None
        self.pro = {}
        if self.pro_path:
            with open(pro, encoding="utf-8") as fh:
                self.pro = json.load(fh)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as fh:
            return parse(fh.read())

    def walk(self, path, node, inst_path, page, block_field, sheet_node, seen):
        sheet = Sheet(path, node, inst_path, page, block_field, sheet_node)
        self.sheets.append(sheet)
        if path in seen:
            return
        for sn in kids(node, "sheet"):
            fname = prop_text(sn, "Sheetfile") or prop_text(sn, "Sheet file")
            child_path = os.path.join(os.path.dirname(path), fname)
            uuid = val(sn, "uuid", "")
            child_inst = inst_path.rstrip("/") + "/" + uuid if inst_path != "/" else "/" + uuid
            cpage = None
            for proj in kids(kid(sn, "instances") or ["x"], "project"):
                for p in kids(proj, "path"):
                    cpage = val(p, "page", cpage)
            if not os.path.exists(child_path):
                continue
            child_node = self.load(child_path)
            before = len(self.sheets)
            self.walk(child_path, child_node, child_inst, cpage or "?", prop_text(sn, "Block"), sn,
                      seen | {path})
            self.sheet_symbols.append((sheet, sn, self.sheets[before]))


def project_vars(design):
    return design.pro.get("text_variables", {}) or {}


def rails_of(design, cfg):
    rails = set(cfg.get("rails", []))
    found = set()
    for s in design.sheets:
        for sym in s.symbols():
            lib = s.lib.get(val(sym, "lib_id", ""))
            if lib is not None and flag(lib, "power") and not s.reference(sym).startswith("#FLG"):
                found.add(prop_text(sym, "Value", ""))
    return (rails or found), found


def interface_of(cfg, cfg_dir):
    blocks = {}
    src = cfg.get("blocks")
    if src:
        extra = load_toml(os.path.join(cfg_dir, src))
        for b in extra.get("block", []):
            blocks[b["id"]] = list(b.get("interface", []))
    for b in cfg.get("block", []):
        blocks[b["id"]] = list(b.get("interface", []))
    return blocks


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

    rails, power_found = rails_of(design, cfg)
    interfaces = interface_of(cfg, cfg_dir)
    root = design.sheets[0]

    # ---- project file
    pro_s = design.pro.get("schematic", {})
    wks = pro_s.get("page_layout_descr_file", "")
    if not design.pro_path:
        c["CRAFT-100"].fail("no .kicad_pro next to the root sheet")
        c["CRAFT-158"].fail("no .kicad_pro next to the root sheet")
    elif os.path.basename(wks) not in DRAWING_SHEETS:
        c["CRAFT-100"].fail(f"drawing sheet {wks!r} is not one of {', '.join(DRAWING_SHEETS)} from kicad-lib")
    else:
        resolved = wks.replace("${KIPRJMOD}", design.dir)
        if not os.path.isabs(resolved):
            resolved = os.path.join(design.dir, resolved)
        if "${" in resolved or not os.path.exists(resolved):
            # kicad-cli then plots KiCad's default sheet without a word (KiCad 10.0.7)
            c["CRAFT-100"].fail(f"drawing sheet {wks!r} not found (KiCad would fall back to its default silently)")
        c["CRAFT-100"].info.append(wks)
    tv = project_vars(design)
    for v in TEXT_VARS:
        if not tv.get(v):
            c["CRAFT-101"].fail(f"project text variable {v} not set")
    if tv.get("OPENDAP_LICENCE") and tv["OPENDAP_LICENCE"] not in LICENCES:
        c["CRAFT-101"].fail(f"OPENDAP_LICENCE {tv['OPENDAP_LICENCE']!r} not in {LICENCES}")
    if tv.get("OPENDAP_SOURCE") and not re.match(r"^(https://)?github\.com/opendap-project/[A-Za-z0-9._-]+", tv["OPENDAP_SOURCE"]):
        c["CRAFT-101"].fail(f"OPENDAP_SOURCE {tv['OPENDAP_SOURCE']!r} is not a github.com/opendap-project repository")
    sev = design.pro.get("erc", {}).get("rule_severities", {})
    if design.pro_path:
        for k in ERC_ERRORS:
            if sev.get(k) != "error":
                c["CRAFT-158"].fail(f"ERC check {k} is {sev.get(k, 'default')!r}, not error")

    # ---- per sheet
    revs = {}
    for s in design.sheets:
        where = f"{s.name} (page {s.page})"
        n = s.node
        # title block
        tb = s.tb
        for k in ("title", "date", "rev", "company", "comment1", "comment2"):
            if not tb.get(k, "").strip():
                c["CRAFT-80"].fail(f"{where}: title block {k} empty")
        if tb.get("company") and tb["company"] != HOLDER:
            c["CRAFT-80"].fail(f"{where}: company {tb['company']!r}, not {HOLDER!r}")
        if tb.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tb["date"]):
            c["CRAFT-80"].fail(f"{where}: date {tb['date']!r} is not ISO 8601")
        role = s.role
        if role and not (role in ROLES or re.fullmatch(r"blk\.[a-z0-9][a-z0-9-]*", role)):
            c["CRAFT-80"].fail(f"{where}: comment 1 {role!r} is not a block ID (blk.<id>) or root/power/interconnect")
        if tb.get("rev"):
            revs.setdefault(tb["rev"], []).append(s.name)
        # paper
        paper = kid(n, "paper")
        size = paper[1] if paper else "?"
        portrait = paper is not None and "portrait" in paper[2:]
        allowed = ("A4", "A3")
        if size not in allowed or portrait:
            c["CRAFT-81"].fail(f"{where}: paper {size}{' portrait' if portrait else ''} (A4 or A3, landscape)")
        elif size == "A3" and s.is_block:
            c["CRAFT-81"].info.append(f"{where}: A3 block sheet (allowed for dense blocks; review)")
        # one block per sheet
        if s is root:
            parts = [y for y in s.symbols() if not flag(s.lib.get(val(y, "lib_id", ""), ["x"]), "power")
                     and not s.reference(y).startswith("#")]
            if parts:
                c["CRAFT-110"].fail(f"root sheet holds {len(parts)} part(s); parts belong in block sheets")
        if s.is_block:
            stem = os.path.splitext(s.name)[0]
            if stem != role[4:]:
                c["CRAFT-82"].fail(f"{where}: file {s.name} not named after block {role} ({role[4:]}.kicad_sch)")
            if s.sheet_node is not None and s.block_field != role:
                c["CRAFT-82"].fail(f"{where}: sheet symbol Block field {s.block_field!r} differs from title block {role!r}")
        elif s is not root and role not in ROLES:
            c["CRAFT-82"].fail(f"{where}: sheet is neither a block nor an overview")
        # notes
        if s.is_block:
            notes = [t for t in kids(n, "text_box") + kids(n, "text") if re.match(r"\s*Notes\b", t[1])]
            if not notes:
                c["CRAFT-115"].fail(f"{where}: no text box beginning with 'Notes'")
            elif not any("Absent state" in t[1] for t in notes):
                c["CRAFT-115"].fail(f"{where}: notes do not state the block's absent state ('Absent state:')")
        # text
        for t in kids(n, "text") + kids(n, "text_box") + kids(n, "label") + kids(n, "global_label") \
                + kids(n, "hierarchical_label") + kids(n, "directive_label") + kids(n, "netclass_flag"):
            sz = text_size(t)
            f = font_of(t)
            if sz is not None and not any(abs(sz - a) < 1e-3 for a in TEXT_SIZES):
                c["CRAFT-173"].fail(f"{where}: {t[0]} {t[1][:30]!r} at {sz} mm (set {TEXT_SIZES})")
            if f is not None and kid(f, "face") is not None:
                c["CRAFT-102"].fail(f"{where}: {t[0]} {t[1][:30]!r} in {val(f, 'face')!r}")
            if f is not None and kid(f, "color") is not None and any(float(x) for x in kid(f, "color")[1:4]):
                c["CRAFT-103"].fail(f"{where}: {t[0]} {t[1][:30]!r} has a hand-set colour")
            if t[0] in ("text", "text_box") and len(re.findall(r"\|", t[1])) >= 4 and "\n" in t[1]:
                c["CRAFT-176"].fail(f"{where}: text drawn as a table ({t[1][:30]!r}); use a KiCad table")
        for g in kids(n, "rectangle") + kids(n, "polyline") + kids(n, "wire") + kids(n, "bus") + kids(n, "text_box"):
            st = kid(g, "stroke")
            col = kid(st, "color") if st is not None else None
            fill = kid(g, "fill")
            fcol = kid(fill, "color") if fill is not None else None
            ftype = val(fill, "type") if fill is not None else None
            if (col is not None and any(float(x) for x in col[1:5])) or \
               (fcol is not None and any(float(x) for x in fcol[1:5])) or ftype == "color":
                c["CRAFT-103"].fail(f"{where}: {g[0]} with a hand-set colour")
        # grid and geometry
        def grid(obj, what):
            p = xy(obj)
            if p and not (on_grid(p[0]) and on_grid(p[1])):
                c["CRAFT-86"].fail(f"{where}: {what} at {fmt(p[0], p[1])} off the 1.27 mm grid")
            if p and round(p[2]) % 90:
                c["CRAFT-86"].fail(f"{where}: {what} rotated {p[2]:g}°")
        for sym in s.symbols():
            grid(sym, f"symbol {s.reference(sym)}")
        for kind in ("label", "global_label", "hierarchical_label", "directive_label", "netclass_flag",
                     "junction", "no_connect"):
            for o in kids(n, kind):
                grid(o, f"{kind} {o[1] if isinstance(o[1], str) else ''}".strip())
        for sn in kids(n, "sheet"):
            grid(sn, f"sheet {prop_text(sn, 'Sheetname')}")
            for pin in kids(sn, "pin"):
                grid(pin, f"sheet pin {pin[1]}")
        ends = {}
        for w in kids(n, "wire") + kids(n, "bus"):
            pts = [(float(p[1]), float(p[2])) for p in kids(kid(w, "pts"), "xy")]
            for (x1, y1) in pts:
                if not (on_grid(x1) and on_grid(y1)):
                    c["CRAFT-86"].fail(f"{where}: {w[0]} end {fmt(x1, y1)} off the 1.27 mm grid")
            if len(pts) == 2:
                (x1, y1), (x2, y2) = pts
                if abs(x1 - x2) > 1e-3 and abs(y1 - y2) > 1e-3:
                    c["CRAFT-154"].fail(f"{where}: diagonal {w[0]} {fmt(x1, y1)}–{fmt(x2, y2)}")
                for p in pts:
                    key = (round(p[0], 2), round(p[1], 2))
                    ends[key] = ends.get(key, 0) + 1
            st = kid(w, "stroke")
            if st is not None and float(val(st, "width", "0")) != 0:
                c["CRAFT-154"].fail(f"{where}: {w[0]} with a hand-set width {val(st, 'width')}")
        wires = [[(float(p[1]), float(p[2])) for p in kids(kid(w, "pts"), "xy")] for w in kids(n, "wire")]
        for j in kids(n, "junction"):
            x, y, _ = xy(j)
            count = ends.get((round(x, 2), round(y, 2)), 0)
            for seg in wires:
                if len(seg) != 2:
                    continue
                (x1, y1), (x2, y2) = seg
                inside = (min(x1, x2) + 1e-3 < x < max(x1, x2) - 1e-3 and abs(y1 - y) < 1e-3 and abs(y2 - y) < 1e-3) or \
                         (min(y1, y2) + 1e-3 < y < max(y1, y2) - 1e-3 and abs(x1 - x) < 1e-3 and abs(x2 - x) < 1e-3)
                if inside:
                    count += 2
            if count >= 4:
                c["CRAFT-153"].fail(f"{where}: four-way junction at {fmt(x, y)}")
        # symbols on the sheet
        hundreds = {}
        for sym in s.symbols():
            lib_id = val(sym, "lib_id", "")
            lib = s.lib.get(lib_id)
            ref = s.reference(sym)
            pr = props(sym)
            is_power = lib is not None and flag(lib, "power")
            if is_power and ref.startswith("#FLG"):
                continue  # PWR_FLAG marks a driven net for ERC; it names no rail
            if is_power:
                net = prop_text(sym, "Value", "")
                if net not in rails:
                    c["CRAFT-85"].fail(f"{where}: power symbol {net!r} is not a rail of the power tree")
                ang = round(xy(sym)[2]) % 360
                if GROUND.match(net) and ang != 0:
                    c["CRAFT-85"].fail(f"{where}: ground symbol {ref} rotated {ang}° (points down at 0°)")
                continue
            if ref.startswith("#"):
                continue
            pfx = ref_prefix(ref)
            m = re.match(r"[A-Za-z]+(\d+)$", ref)
            if m:
                hundreds.setdefault(int(m.group(1)) // 100, []).append(ref)
            if pfx == "U" and kid(sym, "mirror") is not None:
                c["CRAFT-133"].fail(f"{where}: {ref} mirrored")
            in_bom = flag(sym, "in_bom") if kid(sym, "in_bom") is not None else True
            on_board = flag(sym, "on_board") if kid(sym, "on_board") is not None else True
            required = [f for f in REQUIRED_FIELDS
                        if (f not in ("Manufacturer", "MPN") or in_bom) and (f != "Footprint" or on_board)]
            for f in required:
                v = prop_text(sym, f, None)
                if v is None or not v.strip() or (f == "Datasheet" and v.strip() == "~" and in_bom):
                    c["CRAFT-140"].fail(f"{where}: {ref} field {f} missing or empty")
            ds = prop_text(sym, "Datasheet", "")
            if in_bom and ds and ds != "~" and not URL.match(ds):
                c["CRAFT-140"].fail(f"{where}: {ref} Datasheet {ds!r} is not an https URL")
            v = prop_text(sym, "Value", "")
            if pfx in VALUE_UNIT and v.upper() != "DNP":
                problem = value_problem(v, VALUE_UNIT[pfx])
                if problem:
                    c["CRAFT-141"].fail(f"{where}: {ref} value {v!r}: {problem}")
            for fname, p in pr.items():
                shown = not hidden(p)
                if fname in SHOWN_FIELDS and fname != "Spec" and not shown:
                    c["CRAFT-143"].fail(f"{where}: {ref} {fname} hidden")
                if fname not in SHOWN_FIELDS and shown and p[2].strip():
                    c["CRAFT-143"].fail(f"{where}: {ref} {fname} shown")
            if bool(prop_text(sym, "MPN_2")) != bool(prop_text(sym, "Manufacturer_2")):
                c["CRAFT-144"].fail(f"{where}: {ref} MPN_2 and Manufacturer_2 must come together")
            blk = prop_text(sym, "Block")
            if s.is_block and blk and blk != s.role:
                c["CRAFT-145"].fail(f"{where}: {ref} Block {blk!r} on the sheet of {s.role}")
            if not s.is_block and blk is None:
                pass
            cons = prop_text(sym, "Constraints")
            if cons and not CONSTRAINT.match(cons):
                c["CRAFT-146"].fail(f"{where}: {ref} Constraints {cons!r} (space-separated IDs such as PCB-A-07)")
            if flag(sym, "dnp") and not prop_text(sym, "Alternate"):
                c["CRAFT-147"].fail(f"{where}: {ref} DNP in the base design without an Alternate field")
            lib_path = prop_text(sym, "Sim.Library")
            if lib_path and (os.path.isabs(lib_path) and "${" not in lib_path):
                c["CRAFT-148"].fail(f"{where}: {ref} Sim.Library {lib_path!r} is an absolute path")
        if len(hundreds) > 1 and s.is_block:
            c["CRAFT-116"].fail(f"{where}: references in {len(hundreds)} hundred-blocks: "
                                + ", ".join(f"{h * 100}–{h * 100 + 99}" for h in sorted(hundreds)))
        # labels
        for gl in kids(n, "global_label"):
            if gl[1] not in rails:
                c["CRAFT-150"].fail(f"{where}: global label {gl[1]!r} is not a rail")
        for lab in kids(n, "label") + kids(n, "global_label") + kids(n, "hierarchical_label"):
            name = lab[1]
            base = re.sub(r"\{.*\}$", "", name)
            if base in rails:
                continue
            problem = net_name_problem(name, aliases)
            if problem:
                c["CRAFT-84"].fail(f"{where}: net {name!r} {problem}")
        # hierarchical labels by side
        if s.is_block:
            xs = [xy(o)[0] for o in s.symbols()] or [0]
            mid = (min(xs) + max(xs)) / 2
            for hl in kids(n, "hierarchical_label"):
                shape = val(hl, "shape")
                x = xy(hl)[0]
                if shape == "input" and x > mid and hl[1] not in rails:
                    c["CRAFT-114"].fail(f"{where}: input {hl[1]} on the right half")
                if shape == "output" and x < mid and hl[1] not in rails:
                    c["CRAFT-114"].fail(f"{where}: output {hl[1]} on the left half")
        # rail table
        decoupling = 0
        for sym in s.symbols():
            if ref_prefix(s.reference(sym)) == "C":
                decoupling += 1
        tables = kids(n, "table")
        if s.is_block and decoupling >= 2:
            heads = []
            for t in tables:
                cells = kids(kid(t, "cells") or ["cells"], "table_cell")
                if cells:
                    heads.append(cells[0][1].strip())
            if not any(h.lower().startswith("rail") for h in heads):
                c["CRAFT-172"].fail(f"{where}: {decoupling} capacitors and no rail table (a KiCad table headed 'Rail')")
    if len(revs) > 1:
        c["CRAFT-80"].fail("revisions differ between sheets: " + "; ".join(f"{r}: {', '.join(v)}" for r, v in revs.items()))

    # ---- hierarchy: overviews, block interfaces, sheet pins
    roles = [s.role for s in design.sheets]
    for r in ("power", "interconnect"):
        if r not in roles:
            c["CRAFT-110"].fail(f"no {r} overview sheet (title block comment 1 = {r!r})")
    if root.role != "root":
        c["CRAFT-110"].fail(f"root sheet's comment 1 is {root.role!r}, not 'root'")
    on_root = [(sn, child) for parent, sn, child in design.sheet_symbols if parent is root]
    if on_root:
        def reading(item):
            x, y, _ = xy(item[0])
            return (round(y / 25.4), x)
        ordered = [child.page for _, child in sorted(on_root, key=reading)]
        nums = [int(p) for p in ordered if str(p).isdigit()]
        if nums != sorted(nums):
            c["CRAFT-111"].fail("page numbers do not follow the root's reading order (rows top to bottom, "
                                f"left to right): {', '.join(map(str, ordered))}")
    for parent, sn, child in design.sheet_symbols:
        where = f"{parent.name}: sheet {prop_text(sn, 'Sheetname')}"
        sx, sy, _ = xy(sn)
        size = kid(sn, "size")
        w, h = float(size[1]), float(size[2])
        pins = {p[1]: p for p in kids(sn, "pin")}
        labels = {hl[1]: hl for hl in kids(child.node, "hierarchical_label")}
        if set(pins) != set(labels):
            only_p = sorted(set(pins) - set(labels))
            only_l = sorted(set(labels) - set(pins))
            c["CRAFT-112"].fail(f"{where}: sheet pins and hierarchical labels differ "
                                f"(pins only: {only_p}; labels only: {only_l})")
        if child.is_block:
            iface = interfaces.get(child.role)
            if iface is None:
                c["CRAFT-112"].fail(f"{where}: no interface declared for {child.role} in the configuration")
            elif set(iface) != set(pins):
                c["CRAFT-112"].fail(f"{where}: sheet pins differ from the {child.role} interface "
                                    f"(missing: {sorted(set(iface) - set(pins))}; extra: {sorted(set(pins) - set(iface))})")
        for name, p in pins.items():
            px, py, _ = xy(p)
            side = ("left" if abs(px - sx) < 1e-3 else "right" if abs(px - (sx + w)) < 1e-3
                    else "top" if abs(py - sy) < 1e-3 else "bottom" if abs(py - (sy + h)) < 1e-3 else "?")
            shape = p[2] if len(p) > 2 else "?"
            is_rail = rail_like(name, rails)
            if GROUND.match(name):
                want = ("bottom",)
            elif is_rail and shape == "output":
                want = ("right",)
            elif is_rail:
                want = ("top",)
            elif shape == "input":
                want = ("left",)
            elif shape == "output":
                want = ("right",)
            else:
                want = ("left", "right")
            if side not in want:
                c["CRAFT-113"].fail(f"{where}: pin {name} ({shape}) on the {side} edge, not {'/'.join(want)}")
            lab = labels.get(name)
            if lab is not None and val(lab, "shape") != shape:
                c["CRAFT-151"].fail(f"{where}: pin {name} is {shape}, its hierarchical label {val(lab, 'shape')}")
        if prop_text(sn, "Block") is None and child.is_block:
            c["CRAFT-82"].fail(f"{where}: sheet symbol without a Block field")

    # ---- rails and nets the configuration names
    if cfg.get("rails"):
        unknown = power_found - set(cfg["rails"]) - {"GND"}
        for r in sorted(unknown):
            c["CRAFT-85"].fail(f"power symbol {r!r} is not in the configured rails")
    nc = cfg.get("netclasses")
    if nc:
        toml_classes = {k["name"] for k in load_toml(os.path.join(cfg_dir, nc)).get("class", [])}
        ns = design.pro.get("net_settings", {})
        pro_classes = {k["name"] for k in ns.get("classes", [])} - {"Default"}
        used = {p.get("netclass") for p in ns.get("netclass_patterns", []) or []}
        for s in design.sheets:
            for d in kids(s.node, "directive_label") + kids(s.node, "netclass_flag"):
                v = prop_text(d, "Netclass")
                if v:
                    used.add(v)
        for k in sorted(toml_classes - pro_classes):
            c["CRAFT-155"].fail(f"class {k} of netclasses.toml missing from the project")
        for k in sorted((pro_classes | used) - toml_classes - {None}):
            c["CRAFT-155"].fail(f"class {k} is not in netclasses.toml")
    else:
        c["CRAFT-155"].applicable = False
        c["CRAFT-155"].info.append("no netclasses file configured")

    # ---- embedded library symbols (each once)
    seen = set()
    for s in design.sheets:
        for name, sym in s.lib.items():
            if name not in seen:
                seen.add(name)
                check_symbol(name, sym, c)
    return c


# ---------------------------------------------------------------- kicad-cli

def kicad_cli():
    bundled = os.path.join(KICAD_APP, "Contents", "MacOS", "kicad-cli")
    return bundled if os.path.exists(bundled) else shutil.which("kicad-cli")


def netlist_names(root, c):
    cli = kicad_cli()
    if not cli:
        c.applicable = False
        c.info.append("kicad-cli not found")
        return
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "net")
        subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "--output", out, root],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out, encoding="utf-8") as fh:
            net = parse(fh.read())
    for n in kids(kid(net, "nets") or ["nets"], "net"):
        name = val(n, "name", "")
        refs = {val(node, "ref") for node in kids(n, "node")}
        ics = sorted(r for r in refs if ref_prefix(r) == "U")
        if len(ics) >= 2 and (name.startswith("Net-(") or name.startswith("/Net-(")):
            c.fail(f"unnamed net {name} joins {', '.join(ics)}")


def fold_erc(root, c, out):
    cli = kicad_cli()
    if not cli:
        k = Check("ERC", "Q0", "kicad-cli ERC", "ERC")
        k.fail("kicad-cli not found (set KICAD_APP or use --no-erc)")
        out.append(k)
        return
    with tempfile.TemporaryDirectory() as tmp:
        rep = os.path.join(tmp, "erc.json")
        subprocess.run([cli, "sch", "erc", "--format", "json", "--severity-all", "--units", "mm",
                        "--output", rep, root], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        with open(rep, encoding="utf-8") as fh:
            report = json.load(fh)
    err = Check("ERC", "Q0", "Electrical rules (ERC errors not tied to a CRAFT rule)", "ERC")
    warn = Check("ERC-W", "Q4", "ERC warnings (listed, gate nothing)", "ERC")
    warn.applicable = False
    for sheet in report.get("sheets", []):
        for v in sheet.get("violations", []):
            where = f"{sheet.get('path', '/')}: " + ", ".join(i["description"] for i in v.get("items", []))
            rule = ERC_RULES.get(v["type"])
            if rule and rule in c:
                c[rule].fail(f"ERC {v['type']}: {where}")
                if c[rule].enforcement == "CHK":
                    c[rule].enforcement = "CHK+ERC"
            elif v.get("severity") == "error":
                err.fail(f"{v['type']}: {where}")
            else:
                warn.info.append(f"{v['type']}: {where}")
    out += [err, warn]


# ---------------------------------------------------------------- report

def load_toml(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        import tomllib  # Python 3.11+
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ImportError:
        py = os.environ.get("ODAPKIT_PYTHON3", "python3")
        res = subprocess.run([py, "-c", "import json,sys,tomllib;print(json.dumps(tomllib.load(open(sys.argv[1],'rb'))))", path],
                             capture_output=True, text=True)
        if res.returncode:
            sys.exit(f"sch-craft-check: cannot read {path}: {res.stderr.strip()}")
        return json.loads(res.stdout)


def reached(results):
    level = None
    for lv in LEVELS:
        if any(r.status == "fail" and r.level == lv for r in results):
            break
        level = lv
    return level


def report(path, results, target, as_json, cfg_path, erc):
    level = reached(results)
    gate = [r for r in results if r.status == "fail" and LEVELS.index(r.level) <= LEVELS.index(target)]
    debt = [r for r in results if r.status == "fail" and LEVELS.index(r.level) > LEVELS.index(target)]
    if as_json:
        print(json.dumps({
            "schema": "opendap.craft-check/v1", "kind": "schematic" if path.endswith(".kicad_sch") else "symbols",
            "file": path, "spec": SPEC, "config": cfg_path if cfg_path and os.path.exists(cfg_path) else None,
            "erc": erc, "target": target, "reached": level, "target_met": not gate,
            "debt": [r.id for r in debt],
            "levels": {lv: [r.as_dict() for r in results if r.level == lv] for lv in LEVELS},
        }, indent=2))
    else:
        print(f"odapkit craft check: {os.path.relpath(path)}")
        for lv in LEVELS:
            group = [r for r in results if r.level == lv]
            if not group:
                continue
            print(lv)
            for r in group:
                mark = {"pass": "pass", "fail": "FAIL", "n/a": "n/a "}[r.status]
                print(f"  {mark}  {r.id:9} {r.title}  [{r.enforcement}]")
                for f in r.findings[:12]:
                    print(f"          - {f}")
                if len(r.findings) > 12:
                    print(f"          - ... {len(r.findings) - 12} more")
                for n in r.info[:6]:
                    print(f"          {n}")
        print(f"Level reached: {level or 'none (a Q0 item fails)'}. Target {target}: "
              f"{'met' if not gate else 'NOT met'}"
              f"{f'; {len(debt)} item(s) of debt above it' if debt else ''}.")
    return 1 if gate else 0


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
