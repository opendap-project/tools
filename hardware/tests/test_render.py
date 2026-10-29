"""Tests for odw render (tools/render/): normalisation, manifests, stamps and the check, on a stub adapter without
any CAD program; then the KiCad adapters on tests/fixtures/render/component, the FreeCAD and Blender adapters on
models made at test time (each skipped when its program is missing).

Run from the repository root: python3 -m unittest tests.test_render
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

# Tests never inherit a hook's GIT_* variables (HL-64): under pre-commit, git exports GIT_DIR, GIT_INDEX_FILE and
# others, and a fixture's git would act on the repository being committed.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "tools", "render", "__main__.py")
FIXTURE = os.path.join(HERE, "fixtures", "render")
sys.path.insert(0, os.path.join(ROOT, "tools", "render"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import cad  # noqa: E402
import core  # noqa: E402
import kicad_render as kicad  # noqa: E402
import normalize  # noqa: E402


def run(*argv, cwd=None):
    return subprocess.run([sys.executable, TOOL, *argv], capture_output=True, text=True, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def png_bytes(w, h, pixel, extra=b""):
    """An 8-bit RGB PNG of one colour, `pixel` a function (x, y) -> (r, g, b)."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\0" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + extra
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Normalize(unittest.TestCase):
    def test_pdf_date_fixed_at_the_same_length(self):
        a = b"1 0 obj\n<< /Producer (KiCad PDF)\n/CreationDate (D:2026:10:29:02:31:25)\n/ModDate (D:20261029)\n>>"
        b = normalize.pdf(a)
        self.assertEqual(len(a), len(b))
        self.assertIn(b"/CreationDate (D:2000:01:01:00:00:00)", b)
        self.assertEqual(normalize.pdf(a.replace(b"02:31:25", b"09:12:00")), b)

    def test_svg_title_date_dropped_and_glyph_paths_merged(self):
        a = ('<title>SVG Image created as x.svg date 2026-10-29T02:31:28 </title>\n'
             '<g class="stroked-text"><desc>1</desc>\n<path d="M1 2\nL3 4\n" />\n<path d="M3 4\nL5 6\n" />\n</g>'
             '<g style="fill:#000">\n<path d="M0 0\nL1 0\nL1 1\nZ" />\n<path d="M2 2\nL3 2\nL3 3\nZ" />\n</g>')
        b = normalize.svg(a.encode()).decode()
        self.assertIn("<title>SVG Image created as x.svg</title>", b)
        self.assertIn('<path d="M1 2 L3 4M3 4 L5 6" />', b)
        self.assertEqual(b.count("<path"), 3)  # filled shapes keep their own paths

    def test_png_drops_time_and_text(self):
        t = struct.pack(">I", 7) + b"tIME" + b"\x07\xea\x0a\x1d\x02\x1f\x19" + b"\0\0\0\0"
        a = png_bytes(2, 2, lambda x, y: (1, 2, 3), extra=t)
        self.assertNotIn(b"tIME", normalize.png(a))
        self.assertEqual(normalize.png(normalize.png(a)), normalize.png(a))

    def test_png_close_within_the_tolerance(self):
        base = png_bytes(100, 100, lambda x, y: (50, 60, 70))
        few = png_bytes(100, 100, lambda x, y: (58, 60, 70) if (x, y) in {(1, 1), (5, 5)} else (50, 60, 70))
        many = png_bytes(100, 100, lambda x, y: (52, 60, 70) if x < 20 else (50, 60, 70))
        strong = png_bytes(100, 100, lambda x, y: (90, 60, 70) if (x, y) == (3, 3) else (50, 60, 70))
        self.assertTrue(normalize.png_close(base, few))       # 2 of 10,000 pixels, by 8
        self.assertFalse(normalize.png_close(base, many))     # 20 % of the pixels
        self.assertFalse(normalize.png_close(base, strong))   # one pixel, by 40
        self.assertFalse(normalize.png_close(base, png_bytes(50, 100, lambda x, y: (50, 60, 70))))


class Stub:
    """An adapter without a program: one text output and one PNG, made from the source's bytes."""
    type = "stub"
    defaults = {"views": ["top"]}

    def program(self):
        return ("stub", "1")

    def inputs(self, comp, src):
        return {src["path"]: os.path.join(comp, src["path"])}

    def generate(self, comp, src, work):
        data = open(os.path.join(comp, src["path"]), "rb").read()
        shade = data[0] if data else 0
        out = {"text/%s.txt" % v: data.upper() for v in src["views"]}
        out["render/top.png"] = png_bytes(40, 40, lambda x, y: (shade, 0, 0))
        return out


class Stamps(unittest.TestCase):
    def setUp(self):
        core.ADAPTERS["stub"] = Stub()
        self.tmp = tempfile.mkdtemp()
        self.comp = os.path.join(self.tmp, "comp")
        os.makedirs(os.path.join(self.comp, "src"))
        self.write("src/model.txt", "abc\n")
        self.write("outputs.toml", 'schema = "opendap.outputs/v1"\n[[source]]\ntype = "stub"\npath = "src/model.txt"\n')

    def tearDown(self):
        del core.ADAPTERS["stub"]
        shutil.rmtree(self.tmp)

    def write(self, rel, text):
        with open(os.path.join(self.comp, rel), "w") as fh:
            fh.write(text)

    def verdicts(self):
        return {i: v for i, v, _ in core.check(self.comp)}

    def test_never_generated_fails(self):
        self.assertEqual(self.verdicts(), {"stamp": "fail"})

    def test_update_then_current(self):
        acts = dict(core.update(self.comp, lambda s: None))
        self.assertEqual(acts["model/text/top.txt"], "written")
        self.assertEqual(open(os.path.join(self.comp, "outputs", "model", "text", "top.txt")).read(), "ABC\n")
        self.assertEqual(self.verdicts(), {"current": "pass"})
        again = dict(core.update(self.comp, lambda s: None))
        self.assertEqual(set(again.values()), {"unchanged"})

    def test_dry_run_writes_nothing(self):
        acts = dict(core.update(self.comp, lambda s: None, dry_run=True))
        self.assertEqual(acts["stamp.json"], "written")
        self.assertFalse(os.path.exists(os.path.join(self.comp, "outputs")))

    def test_stale_after_each_kind_of_change(self):
        core.update(self.comp, lambda s: None)
        self.write("src/model.txt", "abd\n")
        self.assertEqual(self.verdicts(), {"model/inputs": "fail"})
        core.update(self.comp, lambda s: None)
        self.write("outputs/model/text/top.txt", "edited\n")
        self.assertEqual(self.verdicts(), {"model/text/top.txt": "fail"})
        core.update(self.comp, lambda s: None)
        self.write("outputs/stray.pdf", "%PDF")
        self.assertEqual(self.verdicts(), {"stray.pdf": "fail"})
        core.update(self.comp, lambda s: None)  # removes the stray file
        self.assertFalse(os.path.exists(os.path.join(self.comp, "outputs", "stray.pdf")))
        self.write("outputs.toml", 'schema = "opendap.outputs/v1"\n[[source]]\ntype = "stub"\npath = "src/model.txt"\n'
                   'views = ["top", "side"]\n')
        self.assertEqual(self.verdicts(), {"model/options": "fail"})
        core.update(self.comp, lambda s: None)
        self.write("outputs/README.md", "# edited\n")
        self.assertEqual(self.verdicts(), {"README.md": "fail"})

    def test_close_render_keeps_the_committed_bytes(self):
        core.update(self.comp, lambda s: None)
        png = os.path.join(self.comp, "outputs", "model", "render", "top.png")
        near = png_bytes(40, 40, lambda x, y: (ord("a") + (4 if (x, y) == (0, 0) else 0), 0, 0))
        with open(png, "wb") as fh:
            fh.write(near)
        core.update(self.comp, lambda s: None)
        self.assertEqual(open(png, "rb").read(), near)
        self.assertEqual(self.verdicts(), {"current": "pass"})

    def test_bad_manifests_are_usage_errors(self):
        cases = {
            'schema = "x"\n': "schema",
            'schema = "opendap.outputs/v1"\n[[source]]\ntype = "nope"\npath = "src/model.txt"\n': "type",
            'schema = "opendap.outputs/v1"\n[[source]]\ntype = "stub"\npath = "../model.txt"\n': "no file",
            'schema = "opendap.outputs/v1"\n[[source]]\ntype = "stub"\npath = "src/model.txt"\ncolour = 1\n': "unknown keys",
            'schema = "opendap.outputs/v1"\n[[source]]\ntype = "stub"\npath = "src/model.txt"\n'
            '[[source]]\ntype = "stub"\npath = "src/model.txt"\n': "two sources",
            'schema = [\n': "TOML",
        }
        for text, word in cases.items():
            with self.subTest(word=word):
                self.write("outputs.toml", text)
                with self.assertRaises(core.Problem) as e:
                    core.load(self.comp)
                self.assertEqual((e.exception.code, e.exception.status), ("outputs.invalid", 2))
                self.assertIn(word, e.exception.message)

    def test_too_large_is_refused(self):
        old = core.MAX_FILE
        core.MAX_FILE = 3
        try:
            with self.assertRaises(core.Problem) as e:
                core.update(self.comp, lambda s: None)
            self.assertEqual(e.exception.code, "render.too_large")
        finally:
            core.MAX_FILE = old

    def test_components_owner_and_below(self):
        nested = os.path.join(self.tmp, "repo", "a", "b")
        os.makedirs(nested)
        open(os.path.join(self.tmp, "repo", "a", "outputs.toml"), "w").close()
        open(os.path.join(self.tmp, "repo", "c.txt"), "w").close()
        a = os.path.join(self.tmp, "repo", "a")
        self.assertEqual(core.components(nested), [a])                       # the component that holds it
        self.assertEqual(core.components(os.path.join(self.tmp, "repo")), [a])  # below a folder
        self.assertEqual(core.components(os.path.join(a, "gone.kicad_sch")), [a])  # a deleted file
        self.assertEqual(core.components(os.path.join(self.tmp, "repo", "c.txt")), [])


class Cli(unittest.TestCase):
    def test_check_with_no_components_passes_as_check_result(self):
        r = run("check", "--json", os.path.join(FIXTURE, "no-components"))
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["schema"], "opendap.check-result/v1")
        self.assertEqual(doc["results"][0]["verdict"], "pass")

    def test_usage_errors_exit_2(self):
        for argv in (["--no-such-flag"], ["frob"], ["list", "--regenerate"], ["check", "--dry-run"]):
            with self.subTest(argv=argv):
                r = run(*argv)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("error input.invalid", r.stderr)

    def test_list_json_names_the_adapters(self):
        r = run("list", "--json", os.path.join(FIXTURE, "component"))
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["schema"], "opendap.render-list/v1")
        self.assertEqual({a["type"] for a in doc["adapters"]}, {"kicad-sch", "kicad-pcb", "freecad", "blender"})
        self.assertEqual([s["type"] for s in doc["components"][0]["sources"]], ["kicad-sch", "kicad-pcb"])


def copy_fixture(tmp):
    comp = os.path.join(tmp, "component")
    shutil.copytree(os.path.join(FIXTURE, "component"), comp)
    return comp


def tree(folder):
    out = {}
    for d, _, names in os.walk(folder):
        for n in names:
            p = os.path.join(d, n)
            out[os.path.relpath(p, folder)] = normalize.read_bytes(p)
    return out


@unittest.skipIf(kicad.kicad_cli() is None, "kicad-cli not found")
class KiCad(unittest.TestCase):
    def test_outputs_complete_and_deterministic(self):
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            a, b = copy_fixture(t1), copy_fixture(t2)
            for comp in (a, b):
                r = run("update", comp)
                self.assertEqual(r.returncode, 0, r.stderr)
            ta, tb = tree(os.path.join(a, "outputs")), tree(os.path.join(b, "outputs"))
            self.assertEqual(sorted(ta), sorted(tb))
            for k in ta:
                if k.endswith(".png"):
                    self.assertTrue(normalize.png_close(ta[k], tb[k]), k)
                else:
                    self.assertEqual(ta[k], tb[k], k)
            for k in ("load-switch/schematic.pdf", "load-switch/schematic/root.svg", "board/pcb/F_Cu.svg",
                      "board/pcb/B_Cu.svg", "board/pcb/F_Silkscreen.svg", "board/render/top.png", "README.md",
                      "stamp.json"):
                self.assertIn(k, ta)
            self.assertNotIn(b"CreationDate (D:2026", ta["load-switch/schematic.pdf"])
            self.assertEqual(run("check", a).returncode, 0)
            stamp = json.loads(ta["stamp.json"])
            self.assertIn("hardware/drawing-sheets/opendap.kicad_wks", stamp["sources"][0]["inputs"])

    def test_regenerate_fails_on_a_difference_from_git(self):
        with tempfile.TemporaryDirectory() as t:
            comp = copy_fixture(t)
            git = lambda *a: subprocess.run(["git", "-C", comp, *a], check=True, capture_output=True)  # noqa: E731
            git("init", "-q")
            self.assertEqual(run("update", comp).returncode, 0)
            git("add", "-A")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "outputs")
            r = run("check", "--regenerate", os.path.join(comp, "hardware", "demo", "board.kicad_pcb"), cwd=comp)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(comp, "outputs.toml"), "a") as fh:
                fh.write('\n[[source]]\ntype = "kicad-pcb"\npath = "hardware/demo/board.kicad_pcb"\nname = "b2"\n'
                         'layers = ["F.Cu"]\nviews = []\n')
            r = run("check", "--regenerate", "outputs.toml", cwd=comp)
            self.assertEqual(r.returncode, 1)
            self.assertIn("commit the regenerated outputs", r.stderr)


@unittest.skipIf(cad.blender() is None, "blender not found")
class Blender(unittest.TestCase):
    def test_views_and_glb_deterministic(self):
        with tempfile.TemporaryDirectory() as t:
            comp = os.path.join(t, "comp")
            os.makedirs(comp)
            blend = os.path.join(comp, "cube.blend")
            subprocess.run([cad.blender(), "--background", "--factory-startup", "--python-expr",
                            "import bpy; bpy.ops.wm.save_as_mainfile(filepath=%r)" % blend],
                           check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
            with open(os.path.join(comp, "outputs.toml"), "w") as fh:
                fh.write('schema = "opendap.outputs/v1"\n[[source]]\ntype = "blender"\npath = "cube.blend"\n'
                         'views = ["iso"]\nsize = [160, 120]\n')
            self.assertEqual(run("update", comp).returncode, 0)
            first = tree(os.path.join(comp, "outputs"))
            self.assertIn("cube/render/iso.png", first)
            self.assertIn("cube/cube.glb", first)
            os.remove(os.path.join(comp, "outputs", "cube", "cube.glb"))
            self.assertEqual(run("update", comp).returncode, 0)
            self.assertEqual(tree(os.path.join(comp, "outputs"))["cube/cube.glb"], first["cube/cube.glb"])
            self.assertEqual(run("check", comp).returncode, 0)


@unittest.skipIf(cad.freecadcmd() is None, "freecadcmd not found")
class FreeCAD(unittest.TestCase):
    def test_glb_deterministic_and_views_refused(self):
        with tempfile.TemporaryDirectory() as t:
            comp = os.path.join(t, "comp")
            os.makedirs(comp)
            model = os.path.join(comp, "box.FCStd")
            script = os.path.join(t, "make.py")
            with open(script, "w") as fh:
                fh.write("import FreeCAD, Part\nd = FreeCAD.newDocument('box')\n"
                         "d.addObject('Part::Box', 'Box')\nd.recompute()\nd.saveAs(%r)\n" % model)
            subprocess.run([cad.freecadcmd(), script], check=True, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=300)
            with open(os.path.join(comp, "outputs.toml"), "w") as fh:
                fh.write('schema = "opendap.outputs/v1"\n[[source]]\ntype = "freecad"\npath = "box.FCStd"\n')
            r = run("update", comp)
            self.assertEqual(r.returncode, 0, r.stderr)
            first = tree(os.path.join(comp, "outputs"))["box/box.glb"]
            os.remove(os.path.join(comp, "outputs", "box", "box.glb"))
            self.assertEqual(run("update", comp).returncode, 0)
            self.assertEqual(tree(os.path.join(comp, "outputs"))["box/box.glb"], first)
            with open(os.path.join(comp, "outputs.toml"), "a") as fh:
                fh.write('views = ["top"]\n')
            r = run("check", comp)
            self.assertEqual(r.returncode, 2)
            self.assertIn("freecadcmd has not", r.stderr)


if __name__ == "__main__":
    unittest.main()
