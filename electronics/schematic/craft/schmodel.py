"""The model of the schematic craft check (tools/sch-craft-check.py): a check's result (Check, Checks), the
symbol rules applied to one symbol (check_symbol), net names, and the schematic as the checks read it (Sheet,
Design, rails, block interfaces). bind(names) gives it the check script's constants. Python 3.9."""
import json
import os
import re

from sexpr import flag, fmt, kid, kids, load_toml, on_grid, parse, prop_text, props, text_size, val, xy

# Bound from the check script (bind):
BODY_STROKE = BUS_ALIAS = BUS_GROUP = BUS_PIN = GROUND = NET_SIGNAL = PASSIVE_PREFIXES = PIN_GRID = \
    PIN_LENGTHS = PREFIXES = SYM_TEXT = SYM_TEXT_MIN = TWO_PIN_GRID = UNIT_MAX_PINS = None


def bind(names):
    """Take the check script's upper-case constants (its limits and sets, read from tools/craft/rules.toml)."""
    globals().update({k: v for k, v in names.items() if k.isupper()})


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
