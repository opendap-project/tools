"""Sourcing evaluation matrix: stock, price breaks and lifecycle per part and channel, scored.

Usage:
    odapkit parts matrix CANDIDATES.toml --out DIR [--offline] [--max-age HOURS]
                         [--cache DIR] [--no-findchips] [--json] [--dry-run]
    odapkit parts matrix --render MATRIX.json --out DIR [--columns a,b,c] [--json] [--dry-run]
    odapkit parts rk-ddr-list LIST.pdf [--soc RK3566] [--type LPDDR4] [--package 200ball]
                         [--density 16,32,64] [--supported] [--format csv|json|toml]
    odapkit parts channels

matrix       reads the candidates (docs/specs/sourcing-matrix.md), collects every candidate's
             listings per channel (Digi-Key, Mouser, Newark, Arrow, TTI, LCSC; FindChips for the
             distributors without a keyless source), and writes DIR/matrix.md, matrix.csv,
             listings.csv and matrix.json. DIR is required: sourcing data (stock, prices) is
             never written into a public repository, so choose a private location. Prints
             the Markdown matrix, or the JSON with --json. Fetches are cached with their date
             under ~/.cache/opendap/sourcing (--cache); --max-age (default 24 h) re-reads older
             entries; --offline uses the cache only.
             --render rebuilds matrix.md, matrix.csv and listings.csv in DIR from a saved
             matrix.json, with its date, without fetching anything; the JSON is not rewritten.
             It uses the column list the JSON records; --columns supplies it for a JSON that
             does not record one (or replaces it), in the candidates file's order.
rk-ddr-list  reads Rockchip's DDR SDRAM Support List (PDF, through poppler's pdftotext -bbox, or
             a saved pdftotext -bbox output) and prints one SoC's status for every row; --format toml
             prints [[candidate]] stubs for a matrix file. --supported keeps √, T/A and S/A.
channels     prints how each channel is read on this host (keys present or not).

Keyed channels (optional; skipped, and reported as such, when unset): MOUSER_API_KEY,
ELEMENT14_API_KEY (Newark), ARROW_API_KEY with ARROW_LOGIN, TTI_API_KEY.

--dry-run (matrix) reads the candidates or the saved JSON, names the files it would write, and fetches
and writes nothing. Errors carry a stable dotted code, a hint and a fix (toolkit-host's odw.clikit).
Example: odapkit parts matrix candidates.toml --out ../private/sourcing --dry-run

Exit status: 0 done, 1 a fetch or parse failed for every channel, 2 usage or file error
(including a --render JSON without columns, or a column no candidate has).
Standard library only (Python 3.11 or later); rk-ddr-list on a PDF needs pdftotext.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from sourcing import channels, matrix, rkddr  # noqa: E402
from clihost import load, summary  # noqa: E402

TOOL = "odapkit parts"
clikit = load(TOOL)
FILES = ("matrix.md", "matrix.csv", "listings.csv")


def usage(msg=None):
    clikit.report(TOOL, clikit.CliError("input.invalid", msg or "no command", "the usage is in odapkit parts --help",
                                        f"{TOOL} --help", 2))
    print(__doc__.split("\n", 2)[2].split("\n\n")[0], file=sys.stderr)
    return 2


def fail(code, e, hint, fix):
    return clikit.report(TOOL, clikit.CliError(code, str(e), hint, fix, 2))


def would_write(out, names):
    for n in names:
        print(f"{TOOL}: would write {os.path.join(out, n)}", file=sys.stderr)
    return 0


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            raise SystemExit(usage(f"{name} needs a value"))
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def flag(args, name):
    if name in args:
        args.remove(name)
        return True
    return False


def cmd_render(args, out, as_json, dry_run):
    path = opt(args, "--render")
    cols = opt(args, "--columns")
    fetch_only = [a for a in ("--cache", "--max-age", "--offline", "--no-findchips") if a in args]
    if args:
        return usage("matrix --render takes no candidates file and no fetch options: "
                     + " ".join(fetch_only or args))
    if out is None:
        return usage("matrix --render needs --out DIR, a private location")
    try:
        doc, spec = matrix.load_saved(path, None if cols is None else [c for c in cols.split(",") if c])
    except (OSError, matrix.MatrixError, ValueError) as e:
        return fail("matrix.invalid", e, "a saved matrix.json from odapkit parts matrix, with its columns",
                    f"{TOOL} matrix --render MATRIX.json --out DIR --columns a,b,c")
    if dry_run:
        return would_write(out, FILES)
    md = matrix.render_all(doc["results"], spec, out, doc.get("generated"))
    if as_json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
    else:
        print(md)
    print(f"odapkit parts: wrote {out}/matrix.md, matrix.csv, listings.csv from {path}", file=sys.stderr)
    return 0


def cmd_matrix(args):
    out = opt(args, "--out")
    as_json = flag(args, "--json")
    dry_run = flag(args, "--dry-run")
    if "--render" in args:
        return cmd_render(args, out, as_json, dry_run)
    if "--columns" in args:
        return usage("--columns applies to matrix --render only (a candidates file states its columns)")
    cache = opt(args, "--cache", channels.DEFAULT_CACHE)
    max_age = float(opt(args, "--max-age", "24"))
    offline = flag(args, "--offline")
    no_fc = flag(args, "--no-findchips")
    if len(args) != 1 or args[0].startswith("-"):
        return usage("matrix takes one CANDIDATES.toml")
    if out is None:
        return usage("matrix needs --out DIR, a private location (sourcing data is not committed to public repositories)")
    path = args[0]
    try:
        spec = matrix.load(path)
    except (OSError, matrix.MatrixError, ValueError) as e:
        return fail("candidates.invalid", e, "the candidates format is docs/specs/sourcing-matrix.md",
                    f"{TOOL} --help")
    if dry_run:
        return would_write(out, FILES + ("matrix.json",))
    fetcher = channels.Fetcher(cache_dir=cache, offline=offline, max_age_hours=max_age)

    def progress(mpn, r):
        print(f"  {r['availability']} {r['verdict']:<11} {mpn}", file=sys.stderr)

    results = matrix.run(spec, fetcher, progress=progress, use_findchips=not no_fc)
    md = matrix.write_all(results, spec, out)
    if as_json:
        with open(os.path.join(out, "matrix.json")) as f:
            print(f.read())
    else:
        print(md)
    print(f"odapkit parts: wrote {out}/matrix.md, matrix.csv, listings.csv, matrix.json", file=sys.stderr)
    every = [c for r in results for c in r["coverage"].values()]
    return 1 if every and all(str(c).startswith(("error", "skipped")) for c in every) else 0


def cmd_rk(args):
    soc = opt(args, "--soc", "RK3566")
    typ = opt(args, "--type")
    pkg = opt(args, "--package")
    dens = opt(args, "--density")
    fmt = opt(args, "--format", "csv")
    supported = flag(args, "--supported")
    if len(args) != 1 or fmt not in ("csv", "json", "toml"):
        return usage("rk-ddr-list takes one LIST.pdf (or its pdftotext -bbox output)")
    src = args[0]
    try:
        if src.lower().endswith(".pdf"):
            text = rkddr.bbox_html(src)
        else:
            with open(src, encoding="utf-8") as f:
                text = f.read()
    except (OSError, RuntimeError) as e:
        return fail("ddr_list.unreadable", e, "a Rockchip DDR support list PDF needs poppler's pdftotext",
                    "brew install poppler")
    rows = rkddr.parse(text, soc)
    if typ:
        rows = [r for r in rows if typ.upper() in r["type"].upper()]
    if pkg:
        rows = [r for r in rows if r["package"] == pkg]
    if dens:
        want = {int(x) for x in dens.split(",") if x}
        rows = [r for r in rows if r["density_gbit"] in want]
    if supported:
        rows = [r for r in rows if rkddr.status_class(r["soc_status"]) in ("√", "T/A", "S/A")]
    key = soc.lower()
    if fmt == "json":
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    elif fmt == "csv":
        import csv
        w = csv.writer(sys.stdout)
        w.writerow(["page", "section", "maker", "part", "list_status", "density_gbit", "organization",
                    "type", "package", key])
        for r in rows:
            w.writerow([r["page"], r["section"], r["maker"], r["part"], r["list_status"],
                        r["density_gbit"], r["organization"], r["type"], r["package"], r["soc_status"]])
    else:
        def q(s):
            return json.dumps(s, ensure_ascii=False)
        for r in rows:
            query, match = rkddr.search_terms(r["part"])
            print("[[candidate]]")
            print(f"mpn = {q(r['part'])}")
            print(f"manufacturer = {q(r['maker'])}")
            print(f"query = {q(query)}")
            print(f"match = [{', '.join(q(m) for m in match)}]")
            if r["list_status"]:
                print(f"lifecycle = {q(r['list_status'])}")
                print(f"lifecycle_source = {q('Rockchip DDR support list, product status column')}")
            print("[candidate.attrs]")
            print(f"density_gbit = {r['density_gbit']}")
            print(f"type = {q(r['type'])}")
            print(f"package = {q(r['package'])}")
            print(f"{key} = {q(r['soc_status'])}")
            for k, v in rkddr.derived(r).items():
                print(f"{k} = {q(v)}")
            ref = f"p. {r['page']}, {r['section']}"
            print(f"list_ref = {q(ref)}")
            print()
    return 0


def cmd_channels(_args):
    keyed = channels.keyed_available()
    print("digikey  public pages (no key)")
    print("lcsc     JLCPCB parts API (no key)")
    for ch in ("mouser", "newark", "arrow", "tti"):
        names = " + ".join(channels.KEYED[ch])
        how = "API (" + names + " set)" if keyed[ch] else "FindChips aggregator (" + names + " not set)"
        print(f"{ch:<8} {how}")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(f"{TOOL}: {summary(clikit, 'parts')}\n\n" + __doc__.split("\n", 1)[1].strip())
        return 0 if argv else 2
    cmd, args = argv[0], list(argv[1:])
    if cmd == "matrix":
        return cmd_matrix(args)
    if cmd == "rk-ddr-list":
        return cmd_rk(args)
    if cmd == "channels":
        return cmd_channels(args)
    return usage(f"unknown command {cmd}")

