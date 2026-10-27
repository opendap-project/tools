"""odapkit variant: a board's variants (RFC 0011) validated, resolved and built from schematic to PCB.

Reads a component's variant files (opendap.board-variant/v1, `variants/` or `hardware/variants/`), the block
catalog each one names, and the KiCad project of its board. Spec: docs/specs/variant-tool.md.

Exit status: 0 no error (warnings allowed), 1 a finding at error level, 2 usage, file or tool error.
"""
import json
import os
import sys
import tempfile

if sys.version_info < (3, 11):
    sys.exit("odapkit variant: Python 3.11 or later is needed (tomllib)")

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)
from clihost import load  # noqa: E402

TOOL = "odapkit variant"
clikit = load(TOOL)
import build  # noqa: E402
import catalog as catalogs  # noqa: E402
import kicad  # noqa: E402
import resolve  # noqa: E402
import variantfile  # noqa: E402

SKIP_DIRS = {".git", "fab", "node_modules", "__pycache__"}


def fail(code, message, hint, fix, status=2):
    raise clikit.CliError(code, message, hint, fix, status)


# ------------------------------------------------------------------ locating inputs


def variants_dir(given):
    if given:
        if not os.path.isdir(given):
            fail("variant.dir_not_found", f"no folder {given}", "the variant files live in variants/",
                 f"{TOOL} list --dir <component>/variants")
        return given
    for cand in ("variants", os.path.join("hardware", "variants")):
        if os.path.isdir(cand):
            return cand
    fail("variant.dir_not_found", "no variants/ or hardware/variants/ here",
         "run it at a component's root, or name the folder", f"{TOOL} list --dir PATH")


def load_variant(arg, folder):
    path = arg if arg.endswith(".toml") else os.path.join(variants_dir(folder), f"{arg}.toml")
    if not os.path.isfile(path):
        fail("variant.not_found", f"no variant file {path}", "a variant is a file variants/<name>.toml",
             f"{TOOL} list")
    try:
        return variantfile.load(path)
    except variantfile.VariantError as e:
        fail("variant.invalid", str(e), "the file must be TOML (opendap.board-variant/v1)", f"{TOOL} get {arg}")


def load_catalog(v):
    path = v.catalog_path()
    if not path:
        return None
    try:
        return catalogs.load(path)
    except catalogs.CatalogError as e:
        fail("catalog.not_found", str(e), "catalog is a path from the component root to its block catalog",
             f"{TOOL} get {v.name}")


def find_project(v, given):
    if given:
        if not given.endswith(".kicad_pro") or not os.path.isfile(given):
            fail("project.not_found", f"no KiCad project {given}", "--project names a .kicad_pro file",
                 f"{TOOL} bom {v.name} --project PATH.kicad_pro")
        return given
    root = build.anchor(v.path)
    want = f"{v.data.get('board')}.kicad_pro"
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".") and not x.endswith("-backups")]
        found += [os.path.join(d, f) for f in files if f == want]
    if len(found) != 1:
        fail("project.not_found", f"{len(found)} files named {want} under {root}",
             "the project is found by the variant's board name; name it when that is not unique",
             f"{TOOL} bom {v.name} --project PATH.kicad_pro")
    return found[0]


def kicad_cli():
    k = kicad.cli()
    if not k:
        fail("tool.missing", "kicad-cli not found", "KiCad 10 provides kicad-cli",
             "export KICAD_CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    return k


def netlist(v, project):
    k = kicad_cli()
    sch = project[:-len(".kicad_pro")] + ".kicad_sch"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            return kicad.netlist(k, sch, os.path.join(tmp, "net.xml"))
        except RuntimeError as e:
            fail("kicad.failed", str(e), "the project must open in KiCad 10", f"kicad-cli sch erc {sch}")


def evaluate(arg, folder, project=None, schematic=False):
    """A variant, its catalog, its findings and, with schematic=True, its resolved parts."""
    v = load_variant(arg, folder)
    cat = load_catalog(v)
    findings = (cat.findings if cat else []) + variantfile.validate(v, cat)
    v.file_errors = len(errors(findings))      # the variant file and its catalog, before the schematic
    rows, mode, pro = None, None, None
    if schematic and cat:
        pro = find_project(v, project)
        rows, f, mode = resolve.components(v, cat, netlist(v, pro))
        findings += f
        pcb = pro[:-len(".kicad_pro")] + ".kicad_pcb"
        if mode == "kicad" and os.path.isfile(pcb):
            with open(pcb, encoding="utf-8") as fh:
                if v.name not in kicad.pcb_variant_names(fh.read()):
                    findings.append({"level": "warning", "code": "pcb.variant-missing", "where": pcb,
                                     "message": f"the schematic has KiCad variant data for '{v.name}' but the board "
                                                "does not: its --variant fab outputs are the base population until "
                                                "the board is updated from the schematic (odapkit variant apply "
                                                "writes it into its copy)"})
    return v, cat, findings, rows, mode, pro


# ------------------------------------------------------------------ output


def errors(findings):
    return [f for f in findings if f["level"] == "error"]


def print_findings(findings):
    for f in findings:
        print(f"  {f['level']} {f['code']}: {f['message']}")


def emit(as_json, doc, text):
    if as_json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        text()
    return 1 if errors(doc.get("findings", [])) else 0


# ------------------------------------------------------------------ commands


def cmd_list(a):
    folder = variants_dir(a.dir)
    items = []
    for path in variantfile.discover(folder):
        v, cat, findings, *_ = evaluate(path, folder)
        items.append({**variantfile.summary(v), "errors": len(errors(findings)),
                      "warnings": len(findings) - len(errors(findings)), "findings": findings})
    doc = {"schema": "opendap.variant-list/v1", "dir": folder, "variants": items,
           "findings": [f for i in items for f in i["findings"]]}

    def text():
        print(f"{'variant':<16} {'kind':<12} {'status':<9} {'level':<5} {'fit':>4} {'dnp':>4} {'alt':>4} "
              f"{'open':>4}  result")
        for i in items:
            res = "valid" if not i["errors"] else f"INVALID ({i['errors']} errors)"
            res += f", {i['warnings']} warnings" if i["warnings"] else ""
            print(f"{i['name']:<16} {str(i['kind']):<12} {str(i['status']):<9} {str(i['target_level']):<5} "
                  f"{i['fit']:>4} {i['dnp']:>4} {i['alternate']:>4} {i['open']:>4}  {res}")
    return emit(a.json, doc, text)


def cmd_get(a):
    v, cat, findings, *_ = evaluate(a.variant, a.dir)
    rows = variantfile.resolve(v, cat)
    doc = {"schema": "opendap.variant-get/v1", **variantfile.summary(v),
           "catalog": {"path": cat.path if cat else None, "kind": cat.kind if cat else None},
           "promise": v.data.get("promise", []), "instrumentation": v.data.get("instrumentation", {}),
           "blocks": rows, "findings": findings}

    def text():
        s = variantfile.summary(v)
        print(f"{v.name}: {s['kind']}, {s['status']}, target {s['target_level']}, board {s['board']}")
        print(f"catalog {doc['catalog']['path']} ({doc['catalog']['kind']}); instrumentation "
              f"{doc['instrumentation']}")
        for r in rows:
            state = r["state"] or "MISSING"
            if r["alternate"]:
                state += f" {r['alternate']}"
            extra = (f"  params {json.dumps(r['params'], ensure_ascii=False)}" if r["params"] else "") + \
                (f"  OPEN: {r['open']}" if r["open"] else "")
            print(f"  {r['block']:<22} {state:<20}{extra}")
        print(f"{s['fit']} fit, {s['dnp']} dnp, {s['alternate']} alternate, {s['open']} open; "
              f"{len(errors(findings))} errors, {len(findings) - len(errors(findings))} warnings")
        print_findings(findings)
    return emit(a.json, doc, text)


def cmd_bom(a):
    v, cat, findings, rows, mode, pro = evaluate(a.variant, a.dir, a.project, schematic=True)
    lines = resolve.bom(rows or [])
    doc = {"schema": "opendap.variant-bom/v1", "board": v.data.get("board"), "variant": v.name, "mode": mode,
           "project": pro, "lines": lines, "findings": findings}
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(build.bom_csv(lines) if a.out.endswith(".csv") else json.dumps(doc, indent=2, ensure_ascii=False))

    def text():
        print(f"{v.name}: {sum(ln['quantity'] for ln in lines if not ln['dnp'])} parts on "
              f"{sum(1 for ln in lines if not ln['dnp'])} lines, {sum(ln['quantity'] for ln in lines if ln['dnp'])} "
              f"DNP (variant data: {mode})")
        for ln in lines:
            refs = ",".join(ln["refs"])
            print(f"  {'DNP ' if ln['dnp'] else ''}{ln['quantity']:>3}  {ln['value'][:24]:<24} {ln['mpn'][:24]:<24} "
                  f"{refs[:60]}")
        print_findings(findings)
    return emit(a.json, doc, text)


def cmd_diff(a):
    va, cat_a, fa, rows_a, _, pro = evaluate(a.a, a.dir, a.project, schematic=not a.blocks_only)
    vb, cat_b, fb, rows_b, _, _ = evaluate(a.b, a.dir, pro, schematic=not a.blocks_only)
    findings = fa + fb
    doc = {"schema": "opendap.variant-diff/v1", "a": va.name, "b": vb.name,
           "blocks": resolve.diff_blocks(va, vb, cat_a), "findings": findings}
    if rows_a is not None:
        doc["parts"] = resolve.diff_parts(rows_a, rows_b)
        doc["bom"] = resolve.diff_lines(resolve.bom(rows_a), resolve.bom(rows_b))

    def text():
        print(f"{va.name} -> {vb.name}: {len(doc['blocks'])} blocks differ")
        for d in doc["blocks"]:
            print(f"  {d['block']:<22} " + "; ".join(f"{k} {json.dumps(x[0], ensure_ascii=False)} -> "
                                                     f"{json.dumps(x[1], ensure_ascii=False)}"
                                                     for k, x in d["changes"].items()))
        if "parts" in doc:
            print(f"{len(doc['parts'])} parts differ; BOM quantity changes:")
            for d in doc["bom"]:
                print(f"  {d['part'][:30]:<30} {d['footprint'][:36]:<36} {d['quantity'][0]} -> {d['quantity'][1]}")
        print_findings(findings)
    return emit(a.json, doc, text)


def cmd_apply(a):
    v, cat, findings, *_ = evaluate(a.variant, a.dir)
    if errors(findings):
        doc = {"schema": "opendap.variant-build/v1", "variant": v.name, "findings": findings}
        return emit(a.json, doc, lambda: (print(f"{v.name}: not built, the variant file is invalid"),
                                          print_findings(findings)))
    pro = find_project(v, a.project)
    if a.dry_run:
        print(f"{TOOL}: would build {v.name} from {pro} into {a.out}", file=sys.stderr)
        return 0
    log = (lambda s: print(f"{TOOL}: {v.name}: {s}", file=sys.stderr)) if not a.json else (lambda s: None)
    try:
        m = build.apply(v, cat, pro, a.out, kicad_cli(), checks=not a.no_checks, log=log)
    except build.BuildError as e:
        fail("variant.build_failed", str(e), "the base design is left untouched", f"{TOOL} apply {v.name} --out DIR")
    m["findings"] = findings + m["findings"]

    def text():
        print(f"{v.name}: built in {a.out} ({len(m['outputs'])} files, variant data: {m['mode']}, {m['kicad']})")
        print(f"  BOM {m['bom']['parts']} parts on {m['bom']['lines']} lines, {m['bom']['dnp']} DNP; "
              f"kicad-cli parity {'ok' if m['bom']['parity'] else 'FAILED'}")
        for k, s in m["checks"].items():
            print(f"  {k.upper()}: base {s['base']}, variant {s['variant']} ({s['new']} new, "
                  f"{s['new_errors']} new errors, {s['resolved']} gone, {s['removal_artifacts']} removal artifacts)")
        print_findings(m["findings"])
    return emit(a.json, m, text)


def cmd_check(a):
    folder = variants_dir(a.dir)
    items, k = [], None
    for path in variantfile.discover(folder):
        v, cat, findings, rows, mode, pro = evaluate(path, folder, a.project, schematic=not a.no_schematic)
        item = {"variant": v.name, "mode": mode, "findings": findings}
        if not a.no_build and not v.file_errors:     # schematic findings are reported, and the build still runs
            k = k or kicad_cli()
            hashes, inputs = [], []
            with tempfile.TemporaryDirectory() as tmp:
                src = build.snapshot(pro, os.path.join(tmp, "src"))
                for n in (1, 2):
                    try:
                        m = build.apply(v, cat, pro, os.path.join(tmp, str(n)), k, checks=not a.no_checks, source=src)
                    except build.BuildError as e:
                        fail("variant.build_failed", str(e), "the base design is left untouched",
                             f"{TOOL} apply {v.name} --out DIR")
                    hashes.append({o["path"]: o["sha256"] for o in m["outputs"]})
                    inputs.append(m["inputs"])
                    item.setdefault("build", {"outputs": len(m["outputs"]), "checks": m["checks"],
                                              "bom": m["bom"]})
                    item["findings"] = findings + [f for f in m["findings"] if f not in findings]
            differ = sorted(p for p in set(hashes[0]) | set(hashes[1]) if hashes[0].get(p) != hashes[1].get(p))
            item["reproducible"] = not differ
            if inputs[0] != inputs[1]:      # the variant file or its catalog changed between the two builds
                item["reproducible"] = None
                item["findings"].append({"level": "warning", "code": "build.inputs-changed", "where": v.path,
                                         "message": "the variant file or its catalog changed during the check: "
                                                    "reproducibility not established; run it again"})
            elif differ:
                item["findings"].append({"level": "error", "code": "build.not-reproducible", "where": v.path,
                                         "message": f"two builds differ in {len(differ)} files: {', '.join(differ[:5])}"})
        items.append(item)
    doc = {"schema": "opendap.variant-check/v1", "dir": folder, "variants": items,
           "findings": [f for i in items for f in i["findings"]]}

    def text():
        for i in items:
            e = len(errors(i["findings"]))
            rep = {True: ", reproducible", False: ", NOT reproducible"}.get(i.get("reproducible"), "")
            print(f"{i['variant']}: {'ok' if not e else f'{e} errors'}, "
                  f"{len(i['findings']) - e} warnings{rep}" + (f" (variant data: {i['mode']})" if i["mode"] else ""))
            print_findings(i["findings"])
    return emit(a.json, doc, text)


def parser():
    ap = clikit.Parser(prog=TOOL, summary=clikit.summary("variant", os.path.join(ROOT, "tools.toml")),
                       description=__doc__.split("\n", 2)[2].split("\nExit status")[0].strip()
                       + "\n\nExit status: 0 no error (warnings allowed), 1 a finding at error level, 2 usage, "
                         "file or tool error.",
                       examples=["odapkit variant list", "odapkit variant get production --json",
                                 "odapkit variant diff full production", "odapkit variant bom production --out bom.csv",
                                 "odapkit variant apply production --out /tmp/t1-production",
                                 "odapkit variant check --no-build"])
    ap.formatter_class = __import__("argparse").RawDescriptionHelpFormatter
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=clikit.Parser)

    def common(p, schematic=False):
        p.add_argument("--dir", help="the variants folder (default: variants/ or hardware/variants/)")
        p.add_argument("--json", action="store_true", help="print a JSON document")
        if schematic:
            p.add_argument("--project", help="the board's .kicad_pro (default: <board>.kicad_pro in the component)")
    common(sub.add_parser("list", help="the variants and whether each is valid"))
    p = sub.add_parser("get", help="one variant's resolved block states and its findings")
    p.add_argument("variant", help="a variant name or file")
    common(p)
    p = sub.add_parser("diff", help="block-level and BOM-level differences between two variants")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--blocks-only", action="store_true", help="compare the variant files only (no KiCad)")
    common(p, True)
    p = sub.add_parser("bom", help="the variant's BOM from the schematic, DNP and alternates applied")
    p.add_argument("variant")
    p.add_argument("--out", help="write the BOM to FILE (.csv or .json)")
    common(p, True)
    p = sub.add_parser("apply", help="build the variant's schematic and PCB outputs into a separate tree")
    p.add_argument("variant")
    p.add_argument("--out", required=True, help="the output folder (empty, or a previous build of this tool)")
    p.add_argument("--no-checks", action="store_true", help="skip the variant's ERC and DRC")
    p.add_argument("--dry-run", action="store_true", help="say what would be built; write nothing")
    common(p, True)
    p = sub.add_parser("check", help="every variant valid and consistent with the schematic; builds reproducible")
    p.add_argument("--no-schematic", action="store_true", help="validate the variant files only (no KiCad)")
    p.add_argument("--no-build", action="store_true", help="skip the two builds that prove reproducibility")
    p.add_argument("--no-checks", action="store_true", help="builds without ERC and DRC")
    common(p, True)
    return ap


def body(argv):
    if argv and argv[0] == "show":      # the verb before the command standard's closed set: kept, not listed
        print(f"{TOOL}: 'show' is now 'get': {TOOL} get {' '.join(argv[1:])}".rstrip(), file=sys.stderr)
        argv = ["get"] + list(argv[1:])
    a = parser().parse_args(argv)
    if a.cmd == "check" and a.no_schematic:
        a.no_build = True
    return {"list": cmd_list, "get": cmd_get, "bom": cmd_bom, "diff": cmd_diff, "apply": cmd_apply,
            "check": cmd_check}[a.cmd](a)


def main(argv):
    return clikit.main(TOOL, body, argv)
