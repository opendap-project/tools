"""Build the `odapkit variant` fixture: a tiny KiCad 10 project with three feature blocks.

Run with KiCad's Python from any directory, then commit what it writes:
  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \
      tests/fixtures/variant/make_fixture.py

It writes board/hardware/tiny/: a root sheet with one hierarchical sheet per block (`blk.core`,
`blk.sensor`, `blk.mem`), every symbol carrying a `Block` field, and a placed board. The symbols are
KiCad's own (Device:R, Device:C, power:+3V3, power:GND, power:PWR_FLAG), copied from the stock
libraries of the KiCad that runs the script. What the variants select:

- `blk.sensor` holds the pull-up R201, its capacitor C201 and the measurement link R202, whose field
  `Alt.zero-ohm.Value` (and MPN) is the 0 ohm fitted when the variant's links are `zero-ohm`;
- `blk.mem` holds the memory stand-in R301 (`Alt.mem-16.*` fields: the alternate's value and MPN)
  and R302, an alternate-footprint twin that is DNP in the base design and fitted by `mem-16`
  (`Alt.mem-16.DNP = no`).

No variant data is written into the schematic or the board: `odapkit variant` generates it.
UUIDs come from uuid5 over fixed names, so the output is reproducible.
"""
import os
import re
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "board", "hardware", "tiny")
PROJECT = "tiny"
NS = uuid.UUID("6f1d0c2e-6b8a-4c55-9a41-0d1a5e0f7a11")
STOCK = "/Applications/KiCad/KiCad.app/Contents/SharedSupport"


def uid(*parts):
    return str(uuid.uuid5(NS, "/".join(parts)))


def stock_symbol(lib, name):
    """The S-expression of `name` in a stock .kicad_sym, renamed to `lib:name` for lib_symbols."""
    text = open(os.path.join(STOCK, "symbols", f"{lib}.kicad_sym"), encoding="utf-8").read()
    start = text.index(f'(symbol "{name}"')
    depth, i, in_str = 0, start, False
    while True:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = text[start:i + 1]
    return body.replace(f'(symbol "{name}"', f'(symbol "{lib}:{name}"', 1)


LIBS = {"Device:R": ("Device", "R"), "Device:C": ("Device", "C"), "power:+3V3": ("power", "+3V3"),
        "power:GND": ("power", "GND"), "power:PWR_FLAG": ("power", "PWR_FLAG")}
FP = {"Device:R": "Resistor_SMD:R_0603_1608Metric", "Device:C": "Capacitor_SMD:C_0402_1005Metric"}

ROOT_UUID = uid("root")
# sheet file, block, title, page
SHEETS = [("core", "blk.core", "Core", "2"), ("sensor", "blk.sensor", "Sensor", "3"),
          ("mem", "blk.mem", "Memory", "4")]
# block sheet -> parts: (ref, lib_id, value, x, y, extra fields, base dnp)
PARTS = {
    "core": [("R101", "Device:R", "10 kΩ", 63.5, 60.96, {"MPN": "RC0603FR-0710KL", "Manufacturer": "YAGEO"}, False),
             ("C101", "Device:C", "100 nF", 88.9, 60.96, {"MPN": "GRM155R71C104KA88D", "Manufacturer": "Murata"},
              False)],
    "sensor": [("R201", "Device:R", "4.7 kΩ", 63.5, 60.96, {"MPN": "RC0603FR-074K7L", "Manufacturer": "YAGEO"}, False),
               ("C201", "Device:C", "100 nF", 88.9, 60.96, {"MPN": "GRM155R71C104KA88D", "Manufacturer": "Murata"},
                False),
               ("R202", "Device:R", "1 Ω", 114.3, 60.96,
                {"MPN": "RL0603FR-7W1RL", "Manufacturer": "YAGEO", "Alt.zero-ohm.Value": "0 Ω",
                 "Alt.zero-ohm.MPN": "RC0603JR-070RL"}, False)],
    "mem": [("R301", "Device:R", "MEM-32", 63.5, 60.96,
             {"MPN": "MEM32-X", "Manufacturer": "Acme", "Alt.mem-16.Value": "MEM-16", "Alt.mem-16.MPN": "MEM16-X"},
             False),
            ("R302", "Device:R", "MEM-16", 88.9, 60.96,
             {"MPN": "MEM16-Y", "Manufacturer": "Acme", "Alt.mem-16.DNP": "no"}, True)],
}


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def prop(name, value, x, y, hide=True):
    h = " (hide yes)" if hide else ""
    return f'  (property {q(name)} {q(value)} (at {x} {y} 0) (effects (font (size 1.27 1.27)){h}))\n'


def symbol(lib_id, ref, value, x, y, fields, dnp, path, unit_pins):
    u = uid("sym", path, ref)
    in_bom = "no" if lib_id.startswith("power:") else "yes"
    out = (f'(symbol (lib_id {q(lib_id)}) (at {x} {y} 0) (unit 1) (body_style 1) (exclude_from_sim no) '
           f'(in_bom {in_bom}) (on_board {in_bom}) (dnp {"yes" if dnp else "no"}) (uuid {q(u)})\n')
    out += prop("Reference", ref, x + 2.54, y - 1.27, hide=False)
    out += prop("Value", value, x + 2.54, y + 1.27, hide=False)
    out += prop("Footprint", FP.get(lib_id, ""), x, y)
    out += prop("Datasheet", "", x, y)
    for k, v in fields.items():
        out += prop(k, v, x, y)
    for p in unit_pins:
        out += f'  (pin {q(p)} (uuid {q(uid("pin", path, ref, p))}))\n'
    out += (f'  (instances (project {q(PROJECT)} (path {q(path)} (reference {q(ref)}) (unit 1))))\n)\n')
    return out


def header(title, comment1, paper="A4"):
    return (f'(kicad_sch (version 20260306) (generator "eeschema") (generator_version "10.0")\n'
            f'(uuid {q(ROOT_UUID if comment1 == "root" else uid("sheetfile", comment1))})\n(paper {q(paper)})\n'
            f'(title_block (title {q(title)}) (date "2026-10-27") (rev "A") (company "The OpenDAP Project") '
            f'(comment 1 {q(comment1)}) (comment 2 "Variant ${{VARIANT}}"))\n')


def lib_symbols(ids):
    return "(lib_symbols " + "\n".join(stock_symbol(*LIBS[i]) for i in ids) + ")\n"


def block_sheet(name, block, title):
    sheet_uuid = uid("sheet", name)
    path = f"/{ROOT_UUID}/{sheet_uuid}"
    ids = sorted({p[1] for p in PARTS[name]} | {"power:+3V3", "power:GND"} |
                 ({"power:PWR_FLAG"} if name == "core" else set()))
    text = header(title, block) + lib_symbols(ids)
    n = 0
    for ref, lib_id, value, x, y, fields, dnp in PARTS[name]:
        f = {"Block": block, **fields}
        text += symbol(lib_id, ref, value, x, y, f, dnp, path, ["1", "2"])
        # Device:R and Device:C have their pins at y -/+ 3.81: +3V3 above, GND below.
        n += 1
        text += symbol("power:+3V3", f"#PWR0{SHEETS_INDEX[name]}{n}1", "+3V3", x, y - 3.81, {}, False, path, ["1"])
        text += symbol("power:GND", f"#PWR0{SHEETS_INDEX[name]}{n}2", "GND", x, y + 3.81, {}, False, path, ["1"])
    if name == "core":
        text += symbol("power:PWR_FLAG", "#FLG0101", "PWR_FLAG", 50.8, 57.15, {}, False, path, ["1"])
        text += symbol("power:+3V3", "#PWR01901", "+3V3", 50.8, 57.15, {}, False, path, ["1"])
        text += symbol("power:PWR_FLAG", "#FLG0102", "PWR_FLAG", 50.8, 64.77, {}, False, path, ["1"])
        text += symbol("power:GND", "#PWR01902", "GND", 50.8, 64.77, {}, False, path, ["1"])
    text += '(sheet_instances (path "/" (page "1")))\n)\n'
    return text


SHEETS_INDEX = {"core": 1, "sensor": 2, "mem": 3}


def root_sheet():
    text = header("Tiny variant fixture", "root", "A4") + "(lib_symbols )\n"
    for i, (name, block, title, page) in enumerate(SHEETS):
        x = 30 + 50 * i
        text += (f'(sheet (at {x} 50) (size 30 15) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) '
                 f'(fields_autoplaced yes) (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000)) '
                 f'(uuid {q(uid("sheet", name))})\n')
        text += prop("Sheetname", title, x, 49.3, hide=False)
        text += prop("Sheetfile", f"{name}.kicad_sch", x, 65.6, hide=False)
        text += prop("Block", block, x, 66.5)
        text += f'  (instances (project {q(PROJECT)} (path {q("/" + ROOT_UUID)} (page {q(page)}))))\n)\n'
    text += '(sheet_instances (path "/" (page "1")))\n)\n'
    return text


PROJECT_FILE = """{
  "board": {"design_settings": {"defaults": {}, "rules": {}}},
  "meta": {"filename": "tiny.kicad_pro", "version": 3},
  "schematic": {"drawing": {}},
  "sheets": [
    ["%s", "Root"],
    ["%s", "Core"],
    ["%s", "Sensor"],
    ["%s", "Memory"]
  ],
  "text_variables": {"OPENDAP_PROJECT": "variant fixture"}
}
""" % (ROOT_UUID, uid("sheet", "core"), uid("sheet", "sensor"), uid("sheet", "mem"))


def board():
    import pcbnew
    pcbnew.KIID.SeedGenerator(11)
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(2)
    tb = b.GetTitleBlock()
    tb.SetTitle("Tiny variant fixture")
    tb.SetCompany("The OpenDAP Project")
    tb.SetDate("2026-10-27")
    tb.SetRevision("A")
    nets = {}
    for n in ("+3V3", "GND"):
        nets[n] = pcbnew.NETINFO_ITEM(b, n)
        b.Add(nets[n])
    lib = os.path.join(STOCK, "footprints")
    x0 = 10.0
    for name, block, _, _ in SHEETS:
        for ref, lib_id, value, x, y, fields, dnp in PARTS[name]:
            libname, fpname = FP[lib_id].split(":")
            fp = pcbnew.FootprintLoad(os.path.join(lib, libname + ".pretty"), fpname)
            fp.SetFPIDAsString(FP[lib_id])
            fp.SetReference(ref)
            fp.SetValue(value)
            fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(10.0)))
            fp.SetDNP(dnp)
            fp.SetPath(pcbnew.KIID_PATH(f"{uid('sheet', name)}/{uid('sym', '/' + ROOT_UUID + '/' + uid('sheet', name), ref)}"))
            for k, v in [("Block", block)] + list(fields.items()):
                f = pcbnew.PCB_FIELD(fp, 0, k)
                f.SetText(v)
                f.SetVisible(False)
                f.SetLayer(pcbnew.F_Fab)
                fp.Add(f)
            b.Add(fp)
            for pad in fp.Pads():
                pad.SetNet(nets["+3V3"] if pad.GetNumber() == "1" else nets["GND"])
            x0 += 4.0
    for (xa, ya), (xb, yb) in [((5, 5), (45, 5)), ((45, 5), (45, 15)), ((45, 15), (5, 15)), ((5, 15), (5, 5))]:
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(xa), pcbnew.FromMM(ya)))
        s.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(xb), pcbnew.FromMM(yb)))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(pcbnew.FromMM(0.1))
        b.Add(s)
    path = os.path.join(OUT, f"{PROJECT}.kicad_pcb")
    pcbnew.SaveBoard(path, b)
    # The board's own uuids come from KiCad's generator; keep the file stable across runs.
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(re.sub(r"\n\t\(generator_version \"[^\"]*\"\)", "\n\t(generator_version \"10.0\")", text))


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{PROJECT}.kicad_sch"), "w", encoding="utf-8") as f:
        f.write(root_sheet())
    for name, block, title, _ in SHEETS:
        with open(os.path.join(OUT, f"{name}.kicad_sch"), "w", encoding="utf-8") as f:
            f.write(block_sheet(name, block, title))
    with open(os.path.join(OUT, f"{PROJECT}.kicad_pro"), "w", encoding="utf-8") as f:
        f.write(PROJECT_FILE)
    board()


if __name__ == "__main__":
    main()
