"""odapkit variant: catalog readers, variant validation, part resolution, KiCad edits and the command line.

The first classes run without KiCad; `KiCadFixture` and `Commands` drive kicad-cli on the tiny fixture project
(tests/fixtures/variant/board, built by make_fixture.py) and are skipped when kicad-cli is not installed.

Run from the repository root: python3 -m unittest tests.test_variant
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "variant"))
import catalog  # noqa: E402
import kicad  # noqa: E402
import resolve  # noqa: E402
import variantfile  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "variant")
BOARD = os.path.join(FIX, "board")
VARIANTS = os.path.join(BOARD, "variants")
TOOL = os.path.join(ROOT, "bin", "odapkit-variant")
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
HAS_KICAD = kicad.cli() is not None


def run(*args, cwd=ROOT):
    return subprocess.run([TOOL, *args], capture_output=True, text=True, cwd=cwd, env=ENV, timeout=900,
                          stdin=subprocess.DEVNULL)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def codes(findings, level=None):
    return sorted({f["code"] for f in findings if level is None or f["level"] == level})


class Variants(unittest.TestCase):
    """Validation of opendap.board-variant/v1 against a block-record catalog."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copytree(os.path.join(BOARD, "blocks"), os.path.join(self.tmp, "blocks"))
        os.makedirs(os.path.join(self.tmp, "variants"))
        self.base = read(os.path.join(VARIANTS, "variant-b.toml"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def check(self, text, name="variant-b"):
        path = os.path.join(self.tmp, "variants", f"{name}.toml")
        with open(path, "w") as f:
            f.write(text)
        v = variantfile.load(path)
        return variantfile.validate(v, catalog.load(v.catalog_path()))

    def test_fixture_variants_are_valid(self):
        for name in ("full", "variant-b"):
            v = variantfile.load(os.path.join(VARIANTS, f"{name}.toml"))
            self.assertEqual(variantfile.validate(v, catalog.load(v.catalog_path())), [], name)

    def test_every_rule_has_its_code(self):
        cases = [
            (self.base.replace('"blk.core" = { state = "fit" }\n', ""), "block.missing"),
            (self.base + '"blk.nope" = { state = "fit" }\n', "block.unknown"),
            (self.base + '"blk.radio" = { state = "fit" }\n', "block.removed"),
            (self.base.replace('target_level = "Q1"', 'target_level = "Q1"\ncolour = "red"'), "variant.field-unknown"),
            (self.base.replace('{ state = "fit" }', '{ state = "fit", colour = "red" }'), "variant.field-unknown"),
            (self.base.replace('"blk.core" = { state = "fit" }', '"blk.core" = { state = "maybe" }'), "block.state"),
            (self.base.replace('"blk.core" = { state = "fit" }',
                               '"blk.core" = { state = "alternate", alternate = "x" }'), "alternate.not-allowed"),
            (self.base.replace('alternate = "mem-16"', 'alternate = "mem-8"'), "alternate.unknown"),
            (self.base.replace('alternate = "mem-16", ', ""), "alternate.missing"),
            (self.base.replace('"blk.core" = { state = "fit" }', '"blk.core" = { state = "fit", alternate = "x" }'),
             "alternate.unexpected"),
            (self.base.replace("capacity_gb = 16", "speed = 3"), "param.undeclared"),
            (self.base.replace('status = "proposed"', 'status = "accepted"').replace(
                'note = "fixture: the should block left off"', 'open = "undecided"'), "open.accepted"),
            (self.base.replace('"blk.mem" = { state = "alternate", alternate = "mem-16", params = { capacity_gb = 16 } }',
                               '"blk.mem" = { state = "dnp" }'), "promise.unmet"),
            (self.base.replace('name = "variant-b"', 'name = "other"'), "variant.name"),
            (self.base.replace("board-variant/v1", "board-variant/v9"), "variant.schema"),
            (self.base.replace('links = "zero-ohm"', 'links = "wire"'), "variant.instrumentation"),
            (self.base.replace('promise = ["FIX-001", "FIX-003"]', 'promise = ["nope"]'), "variant.promise"),
            (self.base.replace('target_level = "Q1"\n', ""), "variant.field-missing"),
        ]
        for text, code in cases:
            with self.subTest(code=code):
                self.assertIn(code, codes(self.check(text), "error"))

    def test_dnp_in_a_full_variant_is_a_warning(self):
        text = read(os.path.join(VARIANTS, "full.toml")).replace(
            '"blk.sensor" = { state = "fit" }', '"blk.sensor" = { state = "dnp" }')
        found = self.check(text, "full")
        self.assertEqual(codes(found, "warning"), ["variant.full-dnp"])
        self.assertEqual(codes(found, "error"), [])

    def test_unknown_record_fields_are_rejected(self):
        with open(os.path.join(self.tmp, "blocks", "core.toml"), "a") as f:
            f.write('colour = "red"\n')
        self.assertIn("catalog.field-unknown", codes(catalog.load(os.path.join(self.tmp, "blocks")).findings))


class Schemas(unittest.TestCase):
    """The JSON Schemas accept the fixtures and reject what the validator rejects without the catalog."""

    def validate(self, schema, path):
        code = ("import json,sys,tomllib,jsonschema;"
                "jsonschema.Draft202012Validator(json.load(open(sys.argv[1]))).validate(tomllib.load(open(sys.argv[2],'rb')))")
        return subprocess.run(["uv", "run", "--quiet", "--no-project", "--python", ">=3.11", "--with",
                               "jsonschema>=4.18", "python", "-c", code, os.path.join(ROOT, "schemas", schema), path],
                              capture_output=True, text=True, env=ENV, timeout=300)

    def test_fixtures_validate(self):
        if not shutil.which("uv"):
            self.skipTest("uv not found")
        for name in ("full", "variant-b"):
            r = self.validate("board-variant.schema.json", os.path.join(VARIANTS, f"{name}.toml"))
            self.assertEqual(r.returncode, 0, r.stderr)
        for f in sorted(os.listdir(os.path.join(BOARD, "blocks"))):
            r = self.validate("block.schema.json", os.path.join(BOARD, "blocks", f))
            self.assertEqual(r.returncode, 0, r.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "variant-b.toml")
            with open(bad, "w") as f:
                f.write(read(os.path.join(VARIANTS, "variant-b.toml")).replace('alternate = "mem-16", ', ""))
            self.assertNotEqual(self.validate("board-variant.schema.json", bad).returncode, 0)


class MarkdownCatalog(unittest.TestCase):
    def setUp(self):
        self.cat = catalog.load(os.path.join(FIX, "md", "docs", "feature-blocks.md"))

    def test_blocks_requirements_alternates_and_params(self):
        c = self.cat
        self.assertEqual(sorted(c.active()), ["blk.core", "blk.mem", "blk.mem-supply", "blk.sensor"])
        self.assertEqual(c.blocks["blk.radio"].status, "removed")
        self.assertEqual(c.blocks["blk.core"].must, {"FIX-001", "FIX-004"})
        self.assertEqual(c.blocks["blk.sensor"].requirements, [("FIX-002", "should"), ("FIX-005", None)])
        self.assertEqual(c.blocks["blk.mem-supply"].must, {"FIX-003"})      # "as `blk.mem`"
        self.assertEqual(c.blocks["blk.sensor"].alternates, ["sensor-b"])    # `fit` is a state, not an alternate
        self.assertEqual(c.blocks["blk.mem-supply"].alternates, ["mem-16"])  # one record, two blocks
        self.assertEqual(c.blocks["blk.mem"].params, ["capacity_gb"])
        self.assertEqual(c.blocks["blk.core"].alternates, [])

    def test_summary_table_disagreement_is_a_warning(self):
        v = variantfile.load(os.path.join(FIX, "md", "variants", "prod.toml"))
        found = variantfile.validate(v, self.cat)
        self.assertEqual([(f["level"], f["code"]) for f in found], [("warning", "catalog.table-state")])
        self.assertIn("blk.mem", found[0]["where"])

    def test_prefix_owner(self):
        self.assertEqual(self.cat.owner("blk.mem-supply"), ("blk.mem-supply", "catalog"))
        self.assertEqual(self.cat.owner("blk.core-ldo"), ("blk.core", "prefix"))
        self.assertEqual(self.cat.owner("blk.other"), (None, "unknown"))


def comp(ref, block, value="1k", dnp=False, fields=None, variants=None, footprint="R_0603"):
    return {"ref": ref, "value": value, "footprint": footprint, "fields": {"Block": block, **(fields or {})},
            "dnp": dnp, "in_bom": True, "sheetfile": "", "sheet": "/", "variants": variants or {}}


class Resolution(unittest.TestCase):
    """Part states from the variant file, the Alt.* field sets and KiCad's own variant data."""

    def setUp(self):
        self.cat = catalog.load(os.path.join(BOARD, "blocks"))
        self.vb = variantfile.load(os.path.join(VARIANTS, "variant-b.toml"))

    def rows(self, comps, v=None):
        rows, found, mode = resolve.components(v or self.vb, self.cat, comps)
        return {r["ref"]: r for r in rows}, found, mode

    def test_generated_mode(self):
        rows, found, mode = self.rows([
            comp("R1", "blk.core"), comp("R2", "blk.sensor"),
            comp("R3", "blk.sensor", fields={"Alt.zero-ohm.Value": "0R"}),
            comp("R4", "blk.mem", "MEM-32", fields={"Alt.mem-16.Value": "MEM-16", "Alt.mem-16.MPN": "M16"}),
            comp("R5", "blk.mem", dnp=True, fields={"Alt.mem-16.DNP": "no"}),
            comp("R6", "blk.core", fields={"Alt.zero-ohm.Value": "0R"})])
        self.assertEqual(mode, "generated")
        self.assertEqual(codes(found), [])
        self.assertFalse(rows["R1"]["dnp"])
        self.assertTrue(rows["R2"]["dnp"] and rows["R3"]["dnp"])
        self.assertEqual((rows["R4"]["value"], rows["R4"]["mpn"]), ("MEM-16", "M16"))
        self.assertFalse(rows["R5"]["dnp"])
        self.assertEqual(rows["R6"]["value"], "0R")              # instrumentation links follow the variant

    def test_full_keeps_the_base_design(self):
        full = variantfile.load(os.path.join(VARIANTS, "full.toml"))
        rows, found, _ = self.rows([comp("R4", "blk.mem", "MEM-32", fields={"Alt.mem-16.Value": "MEM-16"}),
                                    comp("R5", "blk.mem", dnp=True, fields={"Alt.mem-16.DNP": "no"}),
                                    comp("R6", "blk.core", fields={"Alt.zero-ohm.Value": "0R"})], full)
        self.assertEqual((rows["R4"]["value"], rows["R5"]["dnp"], rows["R6"]["value"]), ("MEM-32", True, "1k"))

    def test_kicad_mode_mismatches(self):
        kd = lambda dnp=None, **f: {"variant-b": {"dnp": dnp, "in_bom": None, "fields": f}}  # noqa: E731
        rows, found, mode = self.rows([
            comp("R1", "blk.core", variants=kd(dnp=True)),                   # DNP flip in a fit block
            comp("R2", "blk.sensor", variants=kd(Value="x")),                # dnp block that KiCad fits
            comp("R3", "blk.mem", "MEM-32", fields={"Alt.mem-16.Value": "MEM-16"}, variants=kd(Value="MEM-8"))])
        self.assertEqual(mode, "kicad")
        self.assertEqual(codes(found, "error"), ["kicad.dnp-mismatch", "kicad.field-mismatch"])
        self.assertFalse(rows["R1"]["dnp"])       # the variant file wins
        self.assertTrue(rows["R2"]["dnp"])

    def test_alternate_without_data_and_footprint_swap(self):
        _, found, _ = self.rows([comp("R1", "blk.core"), comp("R4", "blk.mem")])
        self.assertIn("alternate.no-data", codes(found, "error"))
        _, found, _ = self.rows([comp("R4", "blk.mem", fields={"Alt.mem-16.Footprint": "R_0402"})])
        self.assertIn("alternate.footprint", codes(found, "error"))

    def test_schematic_block_names(self):
        _, found, _ = self.rows([comp("R1", "blk.core-ldo"), comp("R2", "blk.nope"), comp("R3", ""),
                                 comp("R4", "blk.mem", fields={"Alt.mem-16.Value": "x"})])
        self.assertEqual(codes(found, "warning"), ["sch.block-empty", "sch.block-field-missing",
                                                   "sch.block-undeclared"])
        self.assertEqual(codes(found, "error"), ["sch.block-unknown"])

    def test_bom_and_diffs(self):
        comps = [comp("R1", "blk.core"), comp("R2", "blk.core"), comp("R3", "blk.sensor"),
                 comp("R4", "blk.mem", "MEM-32", fields={"Alt.mem-16.Value": "MEM-16"})]
        full = variantfile.load(os.path.join(VARIANTS, "full.toml"))
        ra, _, _ = resolve.components(full, self.cat, comps)
        rb, _, _ = resolve.components(self.vb, self.cat, comps)
        lines = resolve.bom(rb)
        self.assertEqual([(ln["refs"], ln["dnp"]) for ln in lines],
                         [(["R1", "R2"], False), (["R4"], False), (["R3"], True)])
        self.assertEqual([d["block"] for d in resolve.diff_blocks(full, self.vb, self.cat)], ["blk.mem", "blk.sensor"])
        self.assertEqual([d["ref"] for d in resolve.diff_parts(ra, rb)], ["R3", "R4"])
        self.assertEqual(resolve.diff_lines(resolve.bom(ra), lines),
                         [{"part": "1k", "footprint": "R_0603", "quantity": [3, 2]},
                          {"part": "MEM-16", "footprint": "R_0603", "quantity": [0, 1]},
                          {"part": "MEM-32", "footprint": "R_0603", "quantity": [1, 0]}])


SCH = '''(kicad_sch (version 20260306)
(symbol (lib_id "Device:R") (at 1 1 0) (dnp no) (uuid "a")
  (property "Reference" "R1" (at 0 0 0))
  (instances (project "p" (path "/x" (reference "R1") (unit 1) (variant (name "old") (dnp yes))) (path "/y" (reference "R9") (unit 1)))))
(symbol (lib_id "Device:R") (at 2 2 0) (dnp no) (uuid "b")
  (instances (project "p" (path "/x" (reference "R2") (unit 1) (variant (name "v") (dnp yes))))))
)
'''
PCB = '''(kicad_pcb
\t(version 20260206)
\t(footprint "R"
\t\t(property "Reference" "R1" (at 0 0 0))
\t\t(attr smd)
\t)
\t(footprint "R"
\t\t(property "Reference" "R2" (at 0 0 0))
\t)
)
'''


class SExpressions(unittest.TestCase):
    def test_schematic_variant_data_replaces_its_own_name_only(self):
        out = kicad.write_sch_variant(SCH, "v", {"R1": (True, {"Value": 'say "0"'}), "R2": (None, {})})
        self.assertIn('(reference "R1") (unit 1) (variant (name "old") (dnp yes)) (variant (name "v") (dnp yes) '
                      '(field (name "Value") (value "say \\"0\\"")))', out)
        self.assertEqual(kicad.write_sch_variant(out, "v", {"R1": (True, {"Value": 'say "0"'})}), out)
        self.assertIn('(reference "R2") (unit 1))', out)                  # stale data of "v" removed
        self.assertIn('(reference "R9") (unit 1))', out)                  # not planned: untouched
        self.assertEqual(kicad.write_sch_variant(SCH, "v", {}), SCH)

    def test_drop_symbols_needs_every_instance(self):
        out, gone = kicad.drop_symbols(SCH, {"R1", "R2"})
        self.assertEqual(gone, {"R2"})                                    # R1 also has instance R9
        self.assertIn('"R1"', out)
        self.assertNotIn('"R2"', out)

    def test_board_variant_data_and_list(self):
        out = kicad.write_pcb_variant(PCB, "v", "desc", {"R2": (True, {"Value": "0R"})})
        self.assertEqual(kicad.pcb_variant_names(out), ["v"])
        self.assertIn('(variant (name "v") (dnp yes) (field (name "Value") (value "0R")))', out)
        again = kicad.write_pcb_variant(out, "v", "desc", {"R2": (True, {"Value": "0R"})})
        self.assertEqual(again, out)                                      # idempotent
        self.assertEqual(kicad.footprint_refs(out)[1][1][1], "R2")


class Normalize(unittest.TestCase):
    """What makes two builds identical: no dates, no absolute paths, check findings in a fixed order."""

    def test_reports_pdf_and_svg(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build
        with tempfile.TemporaryDirectory() as tmp:
            outs = []
            for n, (order, date) in enumerate([((1, 2), "07:17:42"), ((2, 1), "08:01:09")]):
                rep = os.path.join(tmp, f"r{n}.json")
                with open(rep, "w") as f:
                    pair = ["Pad 1 [GND] of R1 on F.Cu", "Pad 2 [GND] of C1 on F.Cu"][:: 1 if n else -1]
                    json.dump({"date": date, "source": f"/abs/{n}/x.kicad_pcb",
                               "violations": [{"type": f"t{i}"} for i in order],
                               "unconnected_items": [{"type": "unconnected_items", "severity": "error",
                                                      "items": [{"description": d} for d in pair]}]}, f)
                pdf = os.path.join(tmp, f"p{n}.pdf")
                with open(pdf, "wb") as f:
                    f.write(b"%PDF-1.5\n/CreationDate (D:2026:10:27:" + date.encode() + b")\n")
                svg = os.path.join(tmp, f"s{n}.svg")
                with open(svg, "w") as f:
                    f.write(f"<title>SVG Image created as x.svg date 2026-10-27T{date} </title>")
                for p in (rep, pdf, svg):
                    build.normalize(p)
                outs.append([build.sha256(p) for p in (rep, pdf, svg)])
            self.assertEqual(outs[0], outs[1])
            rep = json.loads(read(os.path.join(tmp, "r0.json")))
            self.assertEqual(rep["unconnected_items"], [{"net": "GND", "severity": "error", "type": "unconnected_items",
                                                         "count": 1}])
            self.assertEqual(build._violations(rep)[-1], ("error", "unconnected_items", "unconnected", ("net GND",)))
            self.assertEqual(os.path.getsize(os.path.join(tmp, "p0.pdf")), len(b"%PDF-1.5\n/CreationDate (D:2026:10:27:07:17:42)\n"))


def tree_hash(folder):
    h = hashlib.sha256()
    for d, _, files in sorted(os.walk(folder)):
        for f in sorted(files):
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                h.update(os.path.relpath(p, folder).encode() + fh.read())
    return h.hexdigest()


@unittest.skipUnless(HAS_KICAD, "kicad-cli (KiCad 10) not installed")
class KiCadFixture(unittest.TestCase):
    """`apply` on the fixture: one build shared by the tests of this class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out = os.path.join(cls.tmp, "variant-b")
        cls.before = tree_hash(BOARD)
        cls.r = run("apply", "variant-b", "--dir", VARIANTS, "--out", cls.out, "--json")
        cls.m = json.loads(cls.r.stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_build_passes_and_leaves_the_base_design_alone(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr)
        self.assertEqual(tree_hash(BOARD), self.before)
        self.assertEqual(self.m["schema"], "opendap.variant-build/v1")
        self.assertEqual((self.m["mode"], self.m["bom"]), ("generated", {"lines": 4, "parts": 4, "dnp": 3,
                                                                         "parity": True}))

    def test_outputs(self):
        paths = {o["path"] for o in self.m["outputs"]}
        for p in ("bom.json", "bom.csv", "bom-kicad.csv", "sch/tiny.pdf", "sch/svg/tiny-Sensor.svg",
                  "pcb/fab-front.pdf", "pcb/fab-back.svg", "pcb/pos.csv", "checks/erc.json", "checks/drc.json",
                  "design/tiny.kicad_sch", "design/tiny.kicad_pcb"):
            self.assertIn(p, paths)
        self.assertNotIn("design/tiny.kicad_prl", paths)
        pos = read(os.path.join(self.out, "pcb", "pos.csv"))
        self.assertIn('"R302"', pos)                        # the alternate's twin land is fitted
        self.assertNotIn('"R201"', pos)                     # the dnp block is not placed
        self.assertEqual(json.loads(read(os.path.join(self.out, "design", "tiny.kicad_pro")))["text_variables"]
                         ["VARIANT"], "variant-b")
        inputs = {i["path"] for i in self.m["inputs"]}
        self.assertIn("tests/fixtures/variant/board/variants/variant-b.toml", inputs)
        self.assertTrue(self.m["kicad"] and self.m["tool"]["sha256"])

    def test_variant_erc_and_drc(self):
        erc, drc = self.m["checks"]["erc"], self.m["checks"]["drc"]
        self.assertEqual((erc["new_errors"], erc["symbols_removed"]), (0, 3))
        self.assertGreater(erc["removal_artifacts"], 0)     # the sensor's power symbols, left alone
        self.assertEqual(drc["new"], 0)

    def test_rebuild_into_a_previous_build_and_refusals(self):
        r = run("apply", "variant-b", "--dir", VARIANTS, "--out", self.out, "--no-checks", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        same = {o["path"]: o["sha256"] for o in json.loads(r.stdout)["outputs"] if not o["path"].startswith("checks")}
        first = {o["path"]: o["sha256"] for o in self.m["outputs"] if not o["path"].startswith("checks")}
        self.assertEqual(same, first)                       # reproducible
        r = run("apply", "variant-b", "--dir", VARIANTS, "--out", os.path.join(BOARD, "hardware", "tiny", "out"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("variant.build_failed", r.stderr)
        busy = os.path.join(self.tmp, "busy")
        os.makedirs(busy)
        open(os.path.join(busy, "keep.txt"), "w").close()
        self.assertEqual(run("apply", "variant-b", "--dir", VARIANTS, "--out", busy).returncode, 2)
        self.assertTrue(os.path.isfile(os.path.join(busy, "keep.txt")))


@unittest.skipUnless(HAS_KICAD, "kicad-cli (KiCad 10) not installed")
class Commands(unittest.TestCase):
    def test_list_and_get(self):
        r = run("list", "--dir", VARIANTS, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual([(v["name"], v["errors"]) for v in d["variants"]], [("full", 0), ("variant-b", 0)])
        r = run("get", "variant-b", "--dir", VARIANTS, "--json")
        d = json.loads(r.stdout)
        self.assertEqual(d["schema"], "opendap.variant-get/v1")
        old = run("show", "variant-b", "--dir", VARIANTS, "--json")    # hidden alias: same document, a hint
        self.assertEqual((old.returncode, json.loads(old.stdout)), (0, d))
        self.assertIn("'show' is now 'get'", old.stderr)
        self.assertIn("{list,get,diff,bom,apply,check}", run("--help").stdout)    # show is not listed
        self.assertNotRegex(run("--help").stdout, r"(?m)^\s+show\b")
        self.assertEqual({b["block"]: (b["state"], b["alternate"]) for b in d["blocks"]},
                         {"blk.core": ("fit", None), "blk.sensor": ("dnp", None), "blk.mem": ("alternate", "mem-16")})

    def test_bom(self):
        before = tree_hash(BOARD)
        with tempfile.TemporaryDirectory() as tmp:
            r = run("bom", "variant-b", "--dir", VARIANTS, "--json", "--out", os.path.join(tmp, "bom.csv"))
            self.assertEqual(r.returncode, 0, r.stderr)
            d = json.loads(r.stdout)
            self.assertEqual(d["schema"], "opendap.variant-bom/v1")
            fitted = sorted(ref for ln in d["lines"] if not ln["dnp"] for ref in ln["refs"])
            self.assertEqual(fitted, ["C101", "R101", "R301", "R302"])
            self.assertIn("MEM16-X", read(os.path.join(tmp, "bom.csv")))
        self.assertEqual(tree_hash(BOARD), before)          # reading the schematic writes nothing next to it

    def test_diff(self):
        r = run("diff", "full", "variant-b", "--dir", VARIANTS, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual([b["block"] for b in d["blocks"]], ["blk.mem", "blk.sensor"])
        self.assertEqual([p["ref"] for p in d["parts"]], ["C201", "R201", "R202", "R301", "R302"])

    def test_check_proves_reproducibility(self):
        r = run("check", "--dir", VARIANTS, "--no-checks", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(v["variant"], v["reproducible"]) for v in json.loads(r.stdout)["variants"]],
                         [("full", True), ("variant-b", True)])

    def test_invalid_variant_fails_with_1_and_usage_with_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(BOARD, os.path.join(tmp, "board"))
            path = os.path.join(tmp, "board", "variants", "variant-b.toml")
            with open(path, "a") as f:
                f.write('"blk.nope" = { state = "fit" }\n')
            vdir = os.path.join(tmp, "board", "variants")
            self.assertEqual(run("check", "--dir", vdir, "--no-schematic").returncode, 1)
            r = run("apply", "variant-b", "--dir", vdir, "--out", os.path.join(tmp, "o"), "--json")
            self.assertEqual(r.returncode, 1)
            self.assertIn("block.unknown", codes(json.loads(r.stdout)["findings"]))
            self.assertFalse(os.path.exists(os.path.join(tmp, "o")))
        self.assertEqual(run("get", "nope", "--dir", VARIANTS).returncode, 2)
        self.assertEqual(run("apply", "variant-b", "--dir", VARIANTS).returncode, 2)      # --out is required


if __name__ == "__main__":
    unittest.main()
