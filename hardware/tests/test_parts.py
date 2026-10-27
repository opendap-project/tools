"""Tests for odapkit parts (its engine, tools/sourcing/partscli.py) and tools/sourcing on synthetic fixtures (tests/fixtures/parts).

The channel fixtures copy the structure of each source (Digi-Key's __NEXT_DATA__, FindChips'
result rows, the JLCPCB parts API, and the documented responses of the keyed APIs) with made-up
part numbers and values; they are named *.synthetic.txt because they are not saved web pages
(toolkit-hooks' no-stray-files keeps HTML out of the repository). No network access: the end-to-end test fills the fetch cache and runs
the tool with --offline.

Run from the repository root: python3 -m unittest discover tests
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "parts")
TOOL = os.path.join(HERE, "..", "tools", "sourcing", "__main__.py")
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

from sourcing import channels, matrix, rkddr  # noqa: E402
from sourcing.model import Listing, lifecycle_class, matches, norm  # noqa: E402


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class Model(unittest.TestCase):
    def test_norm_and_match(self):
        self.assertEqual(norm("MT53E1G32D2FW-046 IT:C"), "MT53E1G32D2FW046ITC")
        self.assertTrue(matches("MT53E1G32D2FW-046-IT-C-TR", ["MT53E1G32D2FW-046 IT:C TR"]))
        self.assertFalse(matches("MT53E1G32D2FW-046 AIT:C", ["MT53E1G32D2FW-046 IT:C"]))
        self.assertTrue(matches("K4UBE3D4AB-MGCL", ["K4UBE3D4AB-MG*"]))

    def test_lifecycle_words(self):
        self.assertEqual(lifecycle_class("Active"), "active")
        self.assertEqual(lifecycle_class("Obsolete"), "obsolete")
        self.assertEqual(lifecycle_class("EOL"), "obsolete")
        self.assertEqual(lifecycle_class("Discontinued at Digi-Key"), "nrnd")
        self.assertEqual(lifecycle_class("Last Time Buy"), "last-time-buy")
        self.assertEqual(lifecycle_class("Not For New Designs"), "nrnd")
        self.assertEqual(lifecycle_class(""), "unknown")

    def test_price_breaks(self):
        r = Listing("x", "x", True, "P", breaks=[(1280, 100.5)])
        self.assertIsNone(r.price_at(1000))
        self.assertEqual(r.price_for_build(1000), (100.5, 1280))
        r = Listing("x", "x", True, "P", breaks=[(1, 10.0), (100, 8.0), (1000, 6.0)])
        self.assertEqual((r.price_at(1), r.price_at(99), r.price_at(100), r.price_at(5000)), (10.0, 10.0, 8.0, 6.0))


class Parsers(unittest.TestCase):
    def test_digikey_listing(self):
        rows, problem = channels.parse_digikey_listing(read("digikey-listing.synthetic.txt"), "T")
        self.assertEqual(problem, "")
        self.assertEqual([r.mpn for r in rows], ["XM4X1G32-046 IT:C", "XM4X1G32-046 IT:C TR", "XM4X1G32-046 WT:A"])
        self.assertEqual(rows[0].breaks, [(1280, 100.5)])
        self.assertEqual(rows[0].stock, 0)
        self.assertEqual(rows[2].stock, 1500)
        self.assertEqual(rows[2].lifecycle, "Obsolete")
        self.assertTrue(rows[0].url.startswith("https://www.digikey.com/en/products/detail/"))

    def test_digikey_listing_without_data(self):
        rows, problem = channels.parse_digikey_listing("<html>bot check</html>")
        self.assertEqual(rows, [])
        self.assertIn("no product data", problem)

    def test_digikey_detail(self):
        d = channels.parse_digikey_detail(read("digikey-detail.synthetic.txt"), "T")
        self.assertEqual(d.mpn, "XM4X1G32-046 IT:C")
        self.assertEqual(d.stock, 0)
        self.assertEqual(d.lifecycle, "Active")
        self.assertEqual(d.breaks, [(1280, 100.5)])
        self.assertIn("no backorders", d.note)

    def test_findchips(self):
        rows = channels.parse_findchips(read("findchips.synthetic.txt"), "T")
        by = {(r.seller, r.mpn): r for r in rows}
        m = by[("Mouser Electronics", "XM4X1G32-046 AAT:C")]
        self.assertEqual((m.channel, m.authorized, m.stock, m.breaks), ("mouser", True, 1000, [(1, 120.0)]))
        n = by[("Newark", "XM4X1G32-046 AAT:C")]
        self.assertEqual((n.channel, n.authorized), ("newark", True))   # known franchised, no marker
        self.assertEqual(n.breaks, [(1, 130.0), (100, 450.0)])
        v = by[("Vyrian", "XM4X1G32-046-IT-C-TR")]
        self.assertEqual((v.channel, v.authorized, v.stock), ("findchips:Vyrian", False, 5000))

    def test_jlc(self):
        rows = channels.parse_jlc(read("jlc.json"), "T")
        self.assertEqual(rows[0].stock, 0)
        self.assertIn("not actionable", rows[0].note)
        self.assertEqual(rows[1].breaks, [(1, 30.0), (10, 28.0)])
        self.assertIsNone(rows[1].authorized)

    def test_keyed_api_shapes(self):
        m = channels.parse_mouser_api(read("mouser-api.json"))[0]
        self.assertEqual((m.stock, m.breaks, m.note), (1000, [(1, 120.0)], "lead time 182 Days"))
        e = channels.parse_element14_api(read("element14-api.json"))[0]
        self.assertEqual((e.channel, e.stock, e.breaks[0]), ("newark", 1100, (1, 130.0)))
        t = channels.parse_tti_api(read("tti-api.json"))[0]
        self.assertEqual((t.stock, t.breaks, t.lifecycle), (0, [(1280, 90.0)], "Active"))
        a = channels.parse_arrow_api(read("arrow-api.json"))[0]
        self.assertEqual((a.stock, a.breaks), (40, [(1, 250.0), (100, 240.0)]))

    def test_keys_are_optional(self):
        self.assertEqual(channels.keyed_available({}), {"mouser": False, "newark": False, "arrow": False, "tti": False})
        self.assertTrue(channels.keyed_available({"ARROW_API_KEY": "k", "ARROW_LOGIN": "l"})["arrow"])
        self.assertFalse(channels.keyed_available({"ARROW_API_KEY": "k"})["arrow"])


def cand(mpn, **attrs):
    return {"mpn": mpn, "query": mpn, "match": [mpn], "attrs": attrs, "_gates": []}


class Evaluate(unittest.TestCase):
    def ev(self, c, rows, gates=()):
        c["_gates"] = list(gates)
        return matrix.evaluate(c, rows, {}, 1000, [1, 100, 1000])

    def test_availability_classes(self):
        stocked = Listing("mouser", "Mouser", True, "P", stock=1200, breaks=[(1, 5.0)], lifecycle="Active")
        self.assertEqual(self.ev(cand("P"), [stocked])["availability"], "A")
        stocked.stock = 10
        self.assertEqual(self.ev(cand("P"), [stocked])["availability"], "B")
        stocked.stock = 0
        self.assertEqual(self.ev(cand("P"), [stocked])["availability"], "D")
        lc = Listing("lcsc", "LCSC", None, "P", stock=234, breaks=[(1, 82.0)])
        self.assertEqual(self.ev(cand("P"), [lc])["availability"], "C")
        broker = Listing("findchips:Vyrian", "Vyrian", False, "P", stock=9999)
        r = self.ev(cand("P"), [broker])
        self.assertEqual((r["availability"], r["verdict"], r["broker_stock"]), ("E", "fail", 9999))

    def test_lifecycle_worst_statement_wins(self):
        a = Listing("digikey", "Digi-Key", True, "P", stock=5000, breaks=[(1, 1.0)], lifecycle="Active")
        c = cand("P")
        c["lifecycle"] = "discontinued"
        r = self.ev(c, [a])
        self.assertEqual((r["lifecycle"], r["verdict"]), ("obsolete", "fail"))

    def test_build_price_prefers_stock(self):
        cheap_no_stock = Listing("tme", "TME", True, "P", stock=0, breaks=[(1360, 100.0)])
        dear_stock = Listing("mouser", "Mouser", True, "P", stock=1500, breaks=[(1, 400.0)])
        r = self.ev(cand("P"), [cheap_no_stock, dear_stock])
        self.assertEqual(r["build_price"]["channel"], "mouser")
        self.assertEqual(r["prices"]["1000"]["usd"], 400.0)
        self.assertEqual(r["list_prices"]["1000"]["usd"], 400.0)
        r = self.ev(cand("P"), [cheap_no_stock])
        self.assertEqual((r["build_price"]["usd"], r["build_price"]["quantity"], r["build_price"]["basis"]),
                         (100.0, 1360, "list price, no stock"))

    def test_gates(self):
        gates = [{"attr": "soc", "pass": ["T/A"], "conditional": ["S/A"], "unknown": "fail"},
                 {"attr": "ranks", "pass": [1], "conditional": [2, "unknown"]}]
        ok = Listing("mouser", "Mouser", True, "P", stock=5000, breaks=[(1, 1.0)], lifecycle="Active")
        self.assertEqual(self.ev(cand("P", soc="T/A [7]", ranks=1), [ok], gates)["verdict"], "pass")
        self.assertEqual(self.ev(cand("P", soc="S/A", ranks=1), [ok], gates)["verdict"], "conditional")
        self.assertEqual(self.ev(cand("P", soc="T/A"), [ok], gates)["verdict"], "conditional")
        r = self.ev(cand("P", soc="N/A", ranks=1), [ok], gates)
        self.assertEqual((r["verdict"], r["gate_ok"]), ("fail", False))
        self.assertEqual(self.ev(cand("P", ranks=1), [ok], gates)["verdict"], "fail")

    def test_score_order(self):
        a = self.ev(cand("A"), [Listing("mouser", "M", True, "A", stock=5000, breaks=[(1, 100.0)], lifecycle="Active")])
        b = self.ev(cand("B"), [Listing("mouser", "M", True, "B", stock=5000, breaks=[(1, 50.0)], lifecycle="")])
        c = self.ev(cand("C"), [Listing("mouser", "M", True, "C", stock=0, breaks=[(1, 1.0)], lifecycle="Active")])
        ranked = matrix.score([c, b, a])
        # availability first, then lifecycle, then price
        self.assertEqual([r["mpn"] for r in ranked], ["A", "B", "C"])
        self.assertEqual(ranked[0]["score"], 4 * 100 + 3 * 10 + 9)
        self.assertEqual(ranked[1]["score"], 4 * 100 + 2 * 10 + 9)
        d = self.ev(cand("D"), [Listing("mouser", "M", True, "D", stock=5000, breaks=[(1, 200.0)], lifecycle="Active")])
        ranked = matrix.score([a, d])
        self.assertEqual([r["mpn"] for r in ranked], ["A", "D"])
        self.assertEqual(ranked[1]["score_parts"]["price"], 4)   # 9 × 100 / 200, rounded


class RockchipList(unittest.TestCase):
    def test_parse_synthetic_list(self):
        rows = rkddr.parse(read("rk-ddr-list.bbox.synthetic.txt"), "RK3566")
        got = {r["part"]: r for r in rows}
        self.assertEqual(set(got), {"XM4X1G32-046 WT:B", "XM4X2G32-046 WT:A", "XS4X32-MG**", "XS4X64-MGCL", "XA4X32-046 AAT:B"})
        self.assertEqual(got["XM4X1G32-046 WT:B"]["soc_status"], "T/A")
        self.assertEqual(got["XM4X2G32-046 WT:A"]["soc_status"], "T/A [7][11]")
        self.assertEqual(got["XS4X32-MG**"]["soc_status"], "")          # RK3568 only
        self.assertEqual(got["XS4X64-MGCL"]["soc_status"], "S/A")
        self.assertEqual(got["XS4X64-MGCL"]["list_status"], "EOL")
        self.assertEqual(got["XM4X1G32-046 WT:B"]["maker"], "ExampleMicro")
        self.assertEqual(got["XS4X64-MGCL"]["maker"], "ExampleSemi")
        self.assertEqual(got["XS4X32-MG**"]["density_gbit"], 32)
        self.assertEqual(got["XA4X32-046 AAT:B"]["soc_status"], "not in this table")
        self.assertEqual(got["XA4X32-046 AAT:B"]["section"], "Automotive")
        self.assertEqual(rkddr.derived(got["XM4X2G32-046 WT:A"])["lpddr4x_mode"], "LPDDR4 only")
        self.assertEqual(rkddr.derived(got["XM4X1G32-046 WT:B"])["lpddr4x_mode"], "yes")

    def test_search_terms(self):
        self.assertEqual(rkddr.search_terms("K4UBE3D4AB-MG**"), ("K4UBE3D4AB-MG", ["K4UBE3D4AB-MG*"]))
        self.assertEqual(rkddr.search_terms("MT53E1G32D2FW-046 IT:C"),
                         ("MT53E1G32D2FW-046", ["MT53E1G32D2FW-046 IT:C", "MT53E1G32D2FW-046 IT:C TR"]))
        self.assertEqual(rkddr.search_terms("H9HCNNNCPMMLXR"), ("H9HCNNNCPMMLXR", ["H9HCNNNCPMMLXR*"]))


class MarkdownCells(unittest.TestCase):
    def test_urls_become_autolinks(self):
        self.assertEqual(matrix._cell("not cached: https://a.example/x?k=1|y"), "not cached: <https://a.example/x?k=1>\\|y")
        self.assertEqual(matrix._cell("see https://a.example/b."), "see <https://a.example/b>.")
        self.assertEqual(matrix._cell("x (https://a.example/b), y"), "x (<https://a.example/b>), y")
        self.assertEqual(matrix._cell("q https://a.example/p?q=(1)"), "q <https://a.example/p?q=(1)>")
        for kept in ("already <https://a.example>", "[t](https://a.example)"):
            self.assertEqual(matrix._cell(kept), kept)


class EndToEnd(unittest.TestCase):
    """The CLI on a candidates file, offline, from a cache filled with the fixtures."""

    def test_matrix_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "cache")
            f = channels.Fetcher(cache_dir=cache)

            def put(url, body, method="GET", data=None):
                key = f._key(method, url, data)
                os.makedirs(os.path.dirname(key), exist_ok=True)
                with open(key + ".body", "w") as fh:
                    fh.write(body)
                with open(key + ".json", "w") as fh:
                    json.dump({"url": url, "fetched": "2026-10-25T00:00:00+00:00", "epoch": time.time()}, fh)

            q = "XM4X1G32-046"
            put("https://www.digikey.com/en/products/result?keywords=XM4X1G32-046", read("digikey-listing.synthetic.txt"))
            put("https://www.digikey.com/en/products/detail/example/XM4X1G32-046-IT-C/1001", read("digikey-detail.synthetic.txt"))
            put("https://www.digikey.com/en/products/detail/example/XM4X1G32-046-IT-C-TR/1002", read("digikey-detail.synthetic.txt"))
            put("https://www.findchips.com/search/XM4X1G32-046", read("findchips.synthetic.txt"))
            put(channels.JLC_URL, read("jlc.json"), "POST",
                json.dumps({"keyword": q, "currentPage": 1, "pageSize": 100}))
            spec = os.path.join(tmp, "c.toml")
            with open(spec, "w") as fh:
                fh.write('version = 1\n[matrix]\ntitle = "t"\ncolumns = ["soc"]\n'
                         '[[gate]]\nattr = "soc"\npass = ["T/A"]\n'
                         f'[[candidate]]\nmpn = "XM4X1G32-046 AAT:C"\nquery = "{q}"\n[candidate.attrs]\nsoc = "T/A"\n'
                         f'[[candidate]]\nmpn = "XM4X1G32-046 IT:C"\nquery = "{q}"\n[candidate.attrs]\nsoc = "T/A"\n')
            out = os.path.join(tmp, "out")
            env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_LOGIN"))}
            r = subprocess.run([sys.executable, TOOL, "matrix", spec, "--offline", "--cache", cache, "--out", out],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            for name in ("matrix.md", "matrix.csv", "listings.csv", "matrix.json"):
                self.assertTrue(os.path.isfile(os.path.join(out, name)), name)
            with open(os.path.join(out, "matrix.json")) as fh:
                res = {x["mpn"]: x for x in json.load(fh)["results"]}
            aat, itc = res["XM4X1G32-046 AAT:C"], res["XM4X1G32-046 IT:C"]
            self.assertEqual((aat["rank"], aat["availability"], aat["authorized_stock"]), (1, "A", 2100))
            self.assertEqual((itc["availability"], itc["verdict"]), ("D", "conditional"))
            self.assertEqual(itc["broker_stock"], 5000)        # Vyrian's TR listing, never counted as authorized
            self.assertEqual(itc["coverage"]["digikey"], "page")
            self.assertEqual(itc["coverage"]["mouser"], "findchips")
            self.assertIn("| 1 | XM4X1G32-046 AAT:C |", r.stdout)

    def test_offline_without_cache_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = os.path.join(tmp, "c.toml")
            with open(spec, "w") as fh:
                fh.write('version = 1\n[[candidate]]\nmpn = "NOPE"\n')
            r = subprocess.run([sys.executable, TOOL, "matrix", spec, "--offline", "--cache", os.path.join(tmp, "c"),
                                "--out", os.path.join(tmp, "o")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            with open(os.path.join(tmp, "o", "matrix.json")) as fh:
                cov = json.load(fh)["results"][0]["coverage"]
            self.assertTrue(cov["digikey"].startswith("error: offline and not cached"))
            with open(os.path.join(tmp, "o", "matrix.md")) as fh:
                md = fh.read()
            self.assertIn("<https://", md)                      # URLs are autolinks (markdownlint MD034)
            self.assertEqual(re.findall(r"(?<!<)https?://", md), [])

    def test_usage(self):
        r = subprocess.run([sys.executable, TOOL, "matrix"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class Render(unittest.TestCase):
    """--render: matrix.md, matrix.csv and listings.csv rebuilt from a saved matrix.json, no fetch."""

    def saved(self, tmp):
        """A matrix written by write_all from synthetic listings; returns its directory."""
        a = cand("XM-A", soc="T/A", density_gbit=32)
        a["comment"] = "see https://a.example/note."
        rows = [Listing("mouser", "Mouser", True, "XM-A", stock=5000, breaks=[(1, 3.5), (1000, 2.25)],
                        lifecycle="Active", url="https://a.example/xm-a", fetched="2026-10-25T00:00:00+00:00")]
        b = cand("XM-B", soc="S/A", density_gbit=16)
        spec = {"title": "Synthetic", "build": 1000, "quantities": [1, 1000], "columns": ["soc", "density_gbit"],
                "gates": [], "candidates": [a, b], "notes": ""}
        results = matrix.score([matrix.evaluate(dict(a, _gates=[]), rows, {"digikey": "page"}, 1000, [1, 1000]),
                                matrix.evaluate(dict(b, _gates=[]), [], {"digikey": "error: https://b.example/x"},
                                                1000, [1, 1000])])
        out = os.path.join(tmp, "saved")
        matrix.write_all(results, spec, out, generated="2026-10-25T00:00:00+00:00")
        return out

    def files(self, d):
        out = {}
        for name in ("matrix.md", "matrix.csv", "listings.csv"):
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                out[name] = fh.read()
        return out

    def run_tool(self, *args):
        return subprocess.run([sys.executable, TOOL, "matrix", *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def test_json_records_the_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(self.saved(tmp), "matrix.json")) as fh:
                self.assertEqual(json.load(fh)["columns"], ["soc", "density_gbit"])

    def test_render_reproduces_every_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self.saved(tmp)
            out = os.path.join(tmp, "again")
            r = self.run_tool("--render", os.path.join(src, "matrix.json"), "--out", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.files(out), self.files(src))
            self.assertFalse(os.path.exists(os.path.join(out, "matrix.json")))   # the input, never rewritten
            self.assertIn("| 1 | XM-A |", r.stdout)
            md = self.files(out)["matrix.md"]
            self.assertIn("2026-10-25T00:00:00+00:00", md)                       # the saved date, not today
            self.assertIn("<https://a.example/note>.", md)
            self.assertEqual(re.findall(r"(?<!<)https?://", md), [])

    def test_json_without_columns_needs_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self.saved(tmp)
            path = os.path.join(src, "matrix.json")
            with open(path) as fh:
                doc = json.load(fh)
            del doc["columns"]
            with open(path, "w") as fh:
                json.dump(doc, fh)
            out = os.path.join(tmp, "again")
            r = self.run_tool("--render", path, "--out", out)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--columns", r.stderr)
            r = self.run_tool("--render", path, "--out", out, "--columns", "soc,density_gbit")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.files(out), self.files(src))

    def test_columns_must_be_attributes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(self.saved(tmp), "matrix.json")
            r = self.run_tool("--render", path, "--out", os.path.join(tmp, "o"), "--columns", "soc,nope")
            self.assertEqual(r.returncode, 2)
            self.assertIn("nope", r.stderr)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(self.saved(tmp), "matrix.json")
            r = self.run_tool("--render", path, "--out", os.path.join(tmp, "o"), "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual((doc["schema"], doc["columns"]), ("opendap.sourcing-matrix/v1", ["soc", "density_gbit"]))

    def test_usage_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(self.saved(tmp), "matrix.json")
            o = os.path.join(tmp, "o")
            toml = os.path.join(tmp, "c.toml")
            with open(toml, "w") as fh:
                fh.write('version = 1\n[[candidate]]\nmpn = "X"\n')
            for args in (["--render", path],                                  # no --out
                         ["--render", path, "--out", o, toml],                # and a candidates file
                         ["--render", path, "--out", o, "--offline"],         # fetch options
                         ["--render", os.path.join(tmp, "missing.json"), "--out", o],
                         [toml, "--out", o, "--offline", "--cache", os.path.join(tmp, "c"),
                          "--columns", "soc"]):                               # --columns without --render
                self.assertEqual(self.run_tool(*args).returncode, 2, args)
            with open(os.path.join(tmp, "bad.json"), "w") as fh:
                fh.write('{"schema": "other"}')
            self.assertEqual(self.run_tool("--render", os.path.join(tmp, "bad.json"), "--out", o).returncode, 2)


if __name__ == "__main__":
    unittest.main()
