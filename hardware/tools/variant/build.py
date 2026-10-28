"""`odapkit variant apply`: one variant's schematic and PCB outputs in their own tree, never touching the base design.

OUT/
  design/     copy of the KiCad project with this variant's KiCad 10 variant data written from the variant file
              (schematic instances, board footprints and the board's variant list) and VARIANT in its text variables
  checks/     erc-base.json, erc.json (the variant's ERC copy: DNP symbols removed), drc-base.json, drc.json (the
              variant's DRC copy: DNP attributes set as the variant resolves them)
  sch/        <project>.pdf and svg/ (schematic plots with --variant, title block ${VARIANT})
  pcb/        fab-front.pdf/.svg, fab-back.pdf/.svg (Fab layers, DNP crossed out), pos.csv (placement, DNP excluded)
  bom.json    opendap.variant-bom/v1; bom.csv; bom-kicad.csv (kicad-cli's per-reference BOM, the parity reference)
  absent-state.json  opendap.variant-absent-state/v1: the absent-state check of the variant (absent.py)
  software/   capabilities.json (opendap.variant-capabilities/v1), devicetree.json (opendap.variant-devicetree/v1)
              and variant.dtso, the devicetree overlay fragment (software.py)
  manifest.json  opendap.variant-build/v1: inputs' and outputs' sha256, KiCad version, tool hash, checks, findings

Dates written by KiCad (PDF CreationDate, SVG title, report date) are zeroed and absolute paths in reports are made
relative, so two runs over the same inputs give identical files (`odapkit variant check`).
"""
import csv
import glob
import hashlib
import io
import json
import os
import re
import shutil

import absent
import kicad
import resolve
import software

DESIGN_EXT = (".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_dru")
DESIGN_FILES = ("sym-lib-table", "fp-lib-table")
TOOL_FILES = ("bin/odapkit-variant", "tools/variant/*.py")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BuildError(Exception):
    pass


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tool_hash():
    h = hashlib.sha256()
    for pat in TOOL_FILES:
        for p in sorted(glob.glob(os.path.join(ROOT, pat))):
            h.update(os.path.relpath(p, ROOT).encode() + b"\0")
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def design_files(project_dir):
    return sorted(f for f in os.listdir(project_dir)
                  if (f.endswith(DESIGN_EXT) or f in DESIGN_FILES) and not f.startswith(("_autosave", "~")))


def anchor(path):
    """The component root a variant file is reported against, the same in a repository of its own and in a merged
    monorepo: the nearest folder holding component.toml, searched from the variant file's folder up to the
    repository root (.git) at most; else the folder that holds variants/ (or hardware/variants/)."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.isfile(os.path.join(d, "component.toml")):
            return d
        if os.path.exists(os.path.join(d, ".git")) or os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    d = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    return os.path.dirname(d) if os.path.basename(d) == "hardware" else d


def normalize(path):
    """Zero the dates KiCad writes and make report paths relative, in place."""
    with open(path, "rb") as f:
        b = f.read()
    if path.endswith(".pdf"):
        b = re.sub(rb"/(CreationDate|ModDate) \((D:[^)]*)\)",
                   lambda m: b"/" + m.group(1) + b" (" + re.sub(rb"\d", b"0", m.group(2)) + b")", b)
    elif path.endswith(".svg"):
        b = re.sub(rb"date \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", b"date 0000-00-00T00:00:00", b)
    elif path.endswith(".json"):
        d = json.loads(b)
        if isinstance(d, dict):
            d.pop("date", None)
            if isinstance(d.get("source"), str):
                d["source"] = os.path.basename(d["source"])
            # Missing connections: KiCad names one representative pair of items per gap, and the pair it picks
            # changes from run to run; the net and the number of gaps do not. They are kept per net.
            if isinstance(d.get("unconnected_items"), list):
                per_net = {}
                for v in d["unconnected_items"]:
                    m = re.search(r"\[([^\]]*)\]", (v.get("items") or [{}])[0].get("description", ""))
                    key = (m.group(1) if m else "", v.get("severity"), v.get("type"))
                    per_net[key] = per_net.get(key, 0) + 1
                d["unconnected_items"] = [{"net": n, "severity": s, "type": ty, "count": c}
                                          for (n, s, ty), c in sorted(per_net.items(), key=lambda x: str(x[0]))]
            # Failed assertions: KiCad reports a capped, thread-dependent subset of the items that fail a
            # custom-rule assertion (which footprints, and which graphic item of each, change from run to
            # run); the rule and the number reported do not. They are kept per rule, with a count.
            if isinstance(d.get("violations"), list):
                per_rule, rest = {}, []
                for v in d["violations"]:
                    if isinstance(v, dict) and v.get("type") == "assertion_failure":
                        key = (v.get("severity"), v.get("description"))
                        per_rule[key] = per_rule.get(key, 0) + 1
                    else:
                        rest.append(v)
                d["violations"] = rest + [{"type": "assertion_failure", "severity": sv, "description": de,
                                           "items": [], "count": c}
                                          for (sv, de), c in sorted(per_rule.items(), key=lambda x: str(x[0]))]
            # DRC runs in threads: the order of its findings changes from run to run, their content does not.
            for k in ("violations", "schematic_parity"):
                if isinstance(d.get(k), list):
                    d[k].sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
            for sheet in d.get("sheets", []) if isinstance(d.get("sheets"), list) else []:
                if isinstance(sheet, dict) and isinstance(sheet.get("violations"), list):
                    sheet["violations"].sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
            b = (json.dumps(d, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with open(path, "wb") as f:
        f.write(b)


def _violations(report):
    """(severity, type, description, items) of every ERC/DRC violation in a kicad-cli JSON report."""
    out = []
    for sheet in report.get("sheets", [{"violations": report.get("violations", [])}]):
        for v in sheet.get("violations", []):
            row = (v.get("severity"), v.get("type"), v.get("description"),
                   tuple(sorted(i.get("description", "") for i in v.get("items", []))))
            out += [row] * v.get("count", 1)        # failed assertions: per rule, with a count (normalize())
    for v in report.get("unconnected_items", []):
        if "net" in v:       # as normalize() keeps them: per net, with a count
            out += [(v.get("severity"), v.get("type"), "unconnected", (f"net {v['net']}",))] * v.get("count", 1)
        else:
            out.append((v.get("severity"), v.get("type"), v.get("description"),
                        tuple(sorted(i.get("description", "") for i in v.get("items", [])))))
    return out


def delta(base, variant):
    pool = list(base)
    new = []
    for v in variant:
        if v in pool:
            pool.remove(v)
        else:
            new.append(v)
    return new, pool


def _check(kcli, kind, target, out_json, variant_name, strip=False):
    """The check's violations; with strip=True the removal artifacts are taken out of the report and counted in
    its `removal_artifacts`: KiCad reports one of several orphaned power symbols of a net, and which one changes
    from run to run, so they would make the report differ between two identical builds."""
    args = ["sch", "erc"] if kind == "erc" else ["pcb", "drc"]
    r = kicad.run(kcli, args + ["--format", "json", "--severity-all", "-D", f"VARIANT={variant_name}",
                                "-o", out_json, target])
    if not os.path.isfile(out_json):
        raise BuildError(f"kicad-cli {' '.join(args)} failed on {target}: {(r.stderr or r.stdout).strip()[-400:]}")
    normalize(out_json)
    with open(out_json) as f:
        report = json.load(f)
    count = 0
    if strip:
        for sheet in report.get("sheets", []):
            keep = [v for v in sheet.get("violations", []) if not artifact(_violations({"violations": [v]})[0])]
            count += len(sheet.get("violations", [])) - len(keep)
            sheet["violations"] = keep
        report["removal_artifacts"] = count
        with open(out_json, "w", encoding="utf-8") as f:
            f.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return _violations(report), count


def _set_dnp_attr(text, plan):
    """The DRC copy: each planned footprint's (attr …) carries dnp exactly when the variant leaves it unfitted."""
    root, fps = kicad.footprint_refs(text)
    edits = []
    for span, ref in fps:
        if ref not in plan or plan[ref][0] is None:
            continue
        want = plan[ref][0]
        attr = next((c for c in kicad.children(text, *span) if c[2] == "attr"), None)
        if attr:
            toks = text[attr[0] + 1:attr[1] - 1].split()
            toks = [t for t in toks if t != "dnp"] + (["dnp"] if want else [])
            edits.append((attr[0], attr[1], "(" + " ".join(toks) + ")"))
        elif want:
            edits.append((span[1] - 1, span[1] - 1, "\n\t\t(attr dnp)\n\t"))
    return kicad._apply(text, edits)


def prepare(out):
    out = os.path.abspath(out)
    if os.path.isdir(out) and os.listdir(out):
        if not os.path.isfile(os.path.join(out, "manifest.json")):
            raise BuildError(f"{out} is not empty and holds no variant build (manifest.json): choose an empty folder")
        shutil.rmtree(out)
    os.makedirs(out)
    return out


def snapshot(pro, dest):
    """Copy the project's design files to dest: builds read a snapshot, so a design edited meanwhile cannot mix
    two states into one build."""
    project_dir = os.path.dirname(os.path.abspath(pro))
    os.makedirs(dest)
    for fn in design_files(project_dir):
        shutil.copy2(os.path.join(project_dir, fn), os.path.join(dest, fn))
    return dest


def apply(variant, catalog, pro, out, kcli, checks=True, log=lambda s: None, source=None):
    """source: a snapshot() of the project to read instead of the project itself (paths and ${KIPRJMOD} still
    refer to the project's folder)."""
    project_dir = os.path.dirname(os.path.abspath(pro))
    src = source or project_dir
    stem = os.path.splitext(os.path.basename(pro))[0]
    out_abs = os.path.abspath(out)
    if out_abs == project_dir or out_abs.startswith(project_dir + os.sep):
        raise BuildError("the output folder is inside the KiCad project: a variant build never writes into the base design")
    sch = os.path.join(src, f"{stem}.kicad_sch")
    if not os.path.isfile(sch):
        raise BuildError(f"no root schematic {sch}")
    out = prepare(out)
    findings = []
    log("netlist")
    comps, nets = kicad.netlist(kcli, sch, os.path.join(out, "netlist.xml"), with_nets=True)
    os.remove(os.path.join(out, "netlist.xml"))
    rows, f, mode = resolve.components(variant, catalog, comps)
    findings += f
    log("absent state, software")
    absent_rep, f = absent.check(variant, catalog, rows, comps, nets, *sheet_data(src))
    findings += f
    write_json(os.path.join(out, "absent-state.json"), absent_rep)
    root = anchor(variant.path)
    caps = software.capabilities(variant, catalog)
    dt, dtso = software.devicetree(variant, catalog, os.path.relpath(variant.path, root))
    os.makedirs(os.path.join(out, "software"))
    write_json(os.path.join(out, "software", "capabilities.json"), caps)
    write_json(os.path.join(out, "software", "devicetree.json"), dt)
    with open(os.path.join(out, "software", "variant.dtso"), "w", encoding="utf-8") as fh:
        fh.write(dtso)
    if dt["conflicts"]:
        findings.append({"level": "warning", "code": "software.node-conflict", "where": variant.path,
                         "message": f"devicetree nodes named by blocks the variant both fits and leaves off: "
                                    f"{', '.join(dt['conflicts'])} (written okay)"})
    plan = {}
    for r in rows:
        dnp = r["dnp"] if r["dnp"] != r["base_dnp"] else None
        if dnp is not None or r["overrides"]:
            plan[r["ref"]] = (dnp, r["overrides"])
    # design/: the copy with this variant's KiCad data
    design = os.path.join(out, "design")
    os.makedirs(design)
    files = design_files(src)
    for fn in files:
        with open(os.path.join(src, fn), encoding="utf-8") as fh:
            text = fh.read()
        if fn.endswith(".kicad_pro") or fn in DESIGN_FILES:
            text = text.replace("${KIPRJMOD}", project_dir)
        if fn.endswith(".kicad_pro"):
            pro_data = json.loads(text)
            pro_data.setdefault("text_variables", {})["VARIANT"] = variant.name
            text = json.dumps(pro_data, indent=2, ensure_ascii=False) + "\n"
        elif fn.endswith(".kicad_sch"):
            text = kicad.write_sch_variant(text, variant.name, plan)
        elif fn.endswith(".kicad_pcb"):
            text = kicad.write_pcb_variant(text, variant.name, variant.data.get("description", ""), plan)
        with open(os.path.join(design, fn), "w", encoding="utf-8") as fh:
            fh.write(text)
    dsch, dpcb = os.path.join(design, f"{stem}.kicad_sch"), os.path.join(design, f"{stem}.kicad_pcb")
    has_pcb = os.path.isfile(dpcb)
    shows = any("${VARIANT}" in read(os.path.join(design, fn)) for fn in files if fn.endswith(".kicad_sch"))
    wks = (json.loads(read(os.path.join(design, f"{stem}.kicad_pro"))).get("schematic", {})
           .get("page_layout_descr_file") or "")
    if wks and os.path.isfile(wks):
        shows = shows or "${VARIANT}" in read(wks)
    if not shows:
        findings.append({"level": "warning", "code": "titleblock.no-variant", "where": sch,
                         "message": "neither the title blocks nor the drawing sheet show ${VARIANT}"})
    common = ["--variant", variant.name, "-D", f"VARIANT={variant.name}"]
    log("plots")
    os.makedirs(os.path.join(out, "sch", "svg"))
    _out(kcli, ["sch", "export", "pdf"] + common + ["-o", os.path.join(out, "sch", f"{stem}.pdf"), dsch])
    _out(kcli, ["sch", "export", "svg"] + common + ["-o", os.path.join(out, "sch", "svg"), dsch])
    if has_pcb:
        os.makedirs(os.path.join(out, "pcb"))
        for side, layers in (("front", "F.Fab,Edge.Cuts"), ("back", "B.Fab,Edge.Cuts")):
            _out(kcli, ["pcb", "export", "pdf"] + common + ["-l", layers, "--mode-single", "--cdnp", "--ibt",
                                                           "-o", os.path.join(out, "pcb", f"fab-{side}.pdf"), dpcb])
            _out(kcli, ["pcb", "export", "svg"] + common + ["-l", layers, "--mode-single", "--cdnp",
                                                           "-o", os.path.join(out, "pcb", f"fab-{side}.svg"), dpcb])
        _out(kcli, ["pcb", "export", "pos", "--variant", variant.name, "--format", "csv", "--units", "mm",
                    "--side", "both", "--exclude-dnp", "-o", os.path.join(out, "pcb", "pos.csv"), dpcb])
    log("bom")
    lines = resolve.bom(rows)
    kbom = os.path.join(out, "bom-kicad.csv")
    _out(kcli, ["sch", "export", "bom", "--variant", variant.name, "--fields",
                "Reference,Value,Footprint,MPN,Manufacturer,${DNP}", "-o", kbom, dsch])
    parity = bom_parity(rows, kbom)
    if parity:
        findings.append({"level": "error", "code": "bom.parity", "where": kbom,
                         "message": f"kicad-cli's variant BOM differs from the resolved one: {'; '.join(parity[:5])}"})
    write_bom(out, variant, lines)
    result_checks = {}
    if checks:
        log("erc/drc")
        result_checks = run_checks(kcli, out, design, stem, files, variant, rows, plan, has_pcb, findings)
    for fn in os.listdir(design):
        if fn not in files:      # what KiCad left behind while plotting (.kicad_prl, caches)
            p = os.path.join(design, fn)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    for p in sorted(glob.glob(os.path.join(out, "**", "*"), recursive=True)):
        if os.path.isfile(p) and p.endswith((".pdf", ".svg")):
            normalize(p)
    inputs = [variant.path] + ([os.path.join(catalog.path, f) for f in sorted(os.listdir(catalog.path))]
                               if os.path.isdir(catalog.path) else [catalog.path])
    inputs += [os.path.join(project_dir, fn) for fn in files]
    hashed = {p: sha256(os.path.join(src, os.path.basename(p)) if p.startswith(project_dir + os.sep) else p)
              for p in inputs}
    manifest = {
        "schema": "opendap.variant-build/v1", "variant": variant.name, "board": variant.data.get("board"),
        "kind": variant.data.get("kind"), "status": variant.data.get("status"), "mode": mode,
        "kicad": kicad.version(kcli), "tool": {"name": "odapkit variant", "sha256": tool_hash()},
        "inputs": [{"path": os.path.relpath(p, root), "sha256": hashed[p]} for p in inputs],
        "bom": {"lines": sum(1 for ln in lines if not ln["dnp"]), "parts": sum(ln["quantity"] for ln in lines
                                                                                 if not ln["dnp"]),
                "dnp": sum(ln["quantity"] for ln in lines if ln["dnp"]), "parity": not parity},
        "checks": result_checks,
        "absent_state": {"dnp_blocks": absent_rep["dnp_blocks"], "counts": absent_rep["counts"]},
        "software": {"present": len(caps["present"]), "absent": len(caps["absent"]), "devicetree": dt["counts"]},
        "findings": findings,
    }
    manifest["outputs"] = [{"path": os.path.relpath(p, out), "sha256": sha256(p)}
                           for p in sorted(glob.glob(os.path.join(out, "**", "*"), recursive=True))
                           if os.path.isfile(p) and os.path.basename(p) != "manifest.json"]
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return manifest


def write_json(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def sheet_data(folder):
    """({sheet file: interface table rows}, {power net names}) over every schematic sheet in folder: the absent-state
    check's declarations and the rails its power symbols create."""
    tables, power = {}, set()
    for fn in sorted(os.listdir(folder)):
        if fn.endswith(".kicad_sch") and not fn.startswith(("_autosave", "~")):
            text = read(os.path.join(folder, fn))
            rows = kicad.interface_tables(text)
            if rows:
                tables[fn] = rows
            power |= kicad.power_nets(text)
    return tables, power


def _out(kcli, args):
    r = kicad.run(kcli, args)
    if r.returncode != 0:
        raise BuildError(f"kicad-cli {' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[-400:]}")


def bom_parity(rows, kbom):
    """Per-reference differences between kicad-cli's --variant BOM and the resolved rows."""
    with open(kbom, encoding="utf-8") as f:
        k = list(csv.reader(f))
    if not k:
        return ["empty BOM"]
    head = k[0]
    got = {}
    for line in k[1:]:
        d = dict(zip(head, line))
        for ref in d.get("Reference", "").split(","):
            if ref.strip():
                got[ref.strip()] = (d.get("Value", ""), d.get("MPN", ""), d.get("DNP", "") != "")
    want = {r["ref"]: (r["value"], r["mpn"], r["dnp"]) for r in rows if r["in_bom"]}
    out = []
    for ref in sorted(set(want) | set(got), key=resolve.natural):
        if want.get(ref) != got.get(ref):
            out.append(f"{ref}: resolved {want.get(ref)}, kicad-cli {got.get(ref)}")
    return out


def write_bom(out, variant, lines):
    doc = {"schema": "opendap.variant-bom/v1", "board": variant.data.get("board"), "variant": variant.name,
           "lines": lines}
    with open(os.path.join(out, "bom.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    with open(os.path.join(out, "bom.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(bom_csv(lines))


def bom_csv(lines):
    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(["Item", "References", "Quantity", "Value", "Footprint", "MPN", "Manufacturer", "Blocks", "DNP"])
    for n, ln in enumerate(lines, 1):
        w.writerow([n, ",".join(ln["refs"]), ln["quantity"], ln["value"], ln["footprint"], ln["mpn"],
                    ln["manufacturer"], " ".join(ln["blocks"]), "DNP" if ln["dnp"] else ""])
    return s.getvalue()


def run_checks(kcli, out, design, stem, files, variant, rows, plan, has_pcb, findings):
    chk = os.path.join(out, "checks")
    os.makedirs(chk)
    res = {}
    # ERC: the base copy, then the variant's ERC copy with its DNP symbols removed
    base, _ = _check(kcli, "erc", os.path.join(design, f"{stem}.kicad_sch"), os.path.join(chk, "erc-base.json"),
                     variant.name)
    ercdir = os.path.join(out, "checks", "erc-design")
    os.makedirs(ercdir)
    dnp = {r["ref"] for r in rows if r["dnp"]}
    removed = set()
    for fn in files:
        src = os.path.join(design, fn)
        if fn.endswith(".kicad_sch"):
            text, gone = kicad.drop_symbols(read(src), dnp)
            removed |= gone
            with open(os.path.join(ercdir, fn), "w", encoding="utf-8") as fh:
                fh.write(text)
        elif not fn.endswith(".kicad_pcb"):
            shutil.copy(src, os.path.join(ercdir, fn))
    var, stripped = _check(kcli, "erc", os.path.join(ercdir, f"{stem}.kicad_sch"), os.path.join(chk, "erc.json"),
                           variant.name, strip=True)
    res["erc"] = _summary(base, var, len(removed), "erc", findings, os.path.join(chk, "erc.json"), stripped)
    shutil.rmtree(ercdir)
    if has_pcb:
        dpcb = os.path.join(design, f"{stem}.kicad_pcb")
        base, _ = _check(kcli, "drc", dpcb, os.path.join(chk, "drc-base.json"), variant.name)
        drcdir = os.path.join(out, "checks", "drc-design")
        os.makedirs(drcdir)
        for fn in files:
            src = os.path.join(design, fn)
            if fn.endswith(".kicad_pcb"):
                with open(os.path.join(drcdir, fn), "w", encoding="utf-8") as fh:
                    fh.write(_set_dnp_attr(read(src), plan))
            elif not fn.endswith(".kicad_sch"):
                shutil.copy(src, os.path.join(drcdir, fn))
        var, _ = _check(kcli, "drc", os.path.join(drcdir, f"{stem}.kicad_pcb"), os.path.join(chk, "drc.json"),
                        variant.name)
        res["drc"] = _summary(base, var, None, "drc", findings, os.path.join(chk, "drc.json"))
        shutil.rmtree(drcdir)
    return res


ARTIFACT_TYPES = {"label_dangling", "wire_dangling", "unconnected_wire_endpoint", "global_label_dangling",
                  "no_connect_dangling"}


def artifact(v):
    """A violation that only says a removed symbol left something behind: a power symbol or flag with no pin to
    meet, a dangling label or wire. A real pin left alone (a floating input) is not one."""
    return v[1] in ARTIFACT_TYPES or (v[3] and all(i.startswith("Symbol #") for i in v[3]))


def _summary(base, var, removed, kind, findings, where, stripped=0):
    new, gone = delta(base, var)
    artifacts = [None] * stripped + [v for v in new if artifact(v)]
    new = [v for v in new if not artifact(v)]
    new_err = [v for v in new if v[0] == "error"]
    if new_err:
        findings.append({"level": "error", "code": f"{kind}.new", "where": where,
                         "message": f"the variant adds {len(new_err)} {kind.upper()} errors: " +
                                    "; ".join(f"{v[1]}: {', '.join(v[3][:2])}" for v in new_err[:4])})
    if len(new) > len(new_err):
        findings.append({"level": "warning", "code": f"{kind}.new", "where": where,
                         "message": f"the variant adds {len(new) - len(new_err)} {kind.upper()} warnings: " +
                                    "; ".join(f"{v[1]}: {', '.join(v[3][:2])}" for v in
                                              [v for v in new if v[0] != "error"][:4])})
    s = {"base": len(base), "variant": len(var), "new": len(new), "new_errors": len(new_err), "resolved": len(gone),
         "removal_artifacts": len(artifacts)}
    if removed is not None:
        s["symbols_removed"] = removed
    return s
