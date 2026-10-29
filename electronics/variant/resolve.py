"""Per-component resolution of a variant, its BOM and the differences between two variants.

A component belongs to the catalog block named by its `Block` field (a sub-sheet's own ID resolves to its parent
block when the catalog declares it in `sub_blocks`, or by prefix, with a warning). Its state in the variant:

- DNP when its block is `dnp`; otherwise the KiCad variant data of the same name (when the schematic has any for
  this variant: "kicad" mode) or the alternate field set (`Alt.<selector>.<Field>`, "generated" mode) decides, and
  the base design's DNP flag stays when neither does. Selectors are the block's chosen alternate and the variant's
  `instrumentation.links` value. A field named `DNP` in an alternate set (`Alt.<sel>.DNP = yes|no`) fits or removes
  the part (alternate-footprint pairs).
- In kicad mode the KiCad data is checked against the variant file and the alternate fields: a part of a `dnp`
  block that KiCad fits, a DNP flip in a `fit` block, or an override that disagrees with an `Alt.*` field is an
  error, and the variant file wins.
"""
import re

ALT = re.compile(r"Alt\.([^.]+)\.(.+)")
BOM_FIELDS = ("MPN", "Manufacturer")


def _f(level, code, where, message):
    return {"level": level, "code": code, "where": where, "message": message}


def natural(ref):
    m = re.match(r"(\D*)(\d*)(.*)", ref)
    return (m.group(1), int(m.group(2) or 0), m.group(3))


def _yes(v):
    return str(v).strip().lower() in ("yes", "true", "1", "dnp")


def components(variant, catalog, comps):
    """(rows, findings, mode) for one variant over the netlist components `comps`."""
    findings, rows = [], []
    name, links = variant.name, variant.links
    mode = "kicad" if any(name in c["variants"] for c in comps) else "generated"
    owners, seen_blocks, overridden = {}, set(), {}
    undeclared, unknown, missing = {}, {}, []
    dnp_mismatch, flip_mismatch, field_mismatch, footprint = {}, {}, [], []
    for c in sorted(comps, key=lambda c: natural(c["ref"])):
        bf = c["fields"].get("Block")
        if not bf:
            owner = None
            missing.append(c["ref"])
        else:
            if bf not in owners:
                owners[bf] = catalog.owner(bf) if catalog else (None, "unknown")
            owner, how = owners[bf]
            if how == "prefix":
                undeclared.setdefault((bf, owner), []).append(c["ref"])
            elif how == "unknown":
                unknown.setdefault(bf, []).append(c["ref"])
        if owner:
            seen_blocks.add(owner)
        entry = variant.blocks.get(owner) if owner and isinstance(variant.blocks.get(owner), dict) else {}
        state = entry.get("state", "fit")
        selectors = [s for s in (entry.get("alternate") if state == "alternate" else None, links) if s]
        alt_fields, alt_dnp = {}, None
        for k, val in c["fields"].items():
            m = ALT.fullmatch(k)
            if m and m.group(1) in selectors:
                if m.group(2) == "DNP":
                    alt_dnp = _yes(val)
                else:
                    alt_fields[m.group(2)] = val
        kd = c["variants"].get(name) if mode == "kicad" else None
        overrides = dict(alt_fields)
        if kd:
            for k, val in kd["fields"].items():
                if k in alt_fields and alt_fields[k] != val:
                    field_mismatch.append(f"{c['ref']}.{k}: KiCad {val!r}, Alt field {alt_fields[k]!r}")
                overrides.setdefault(k, val)
        if state == "dnp":
            dnp = True
            if kd is not None and kd["dnp"] is not True and owner:
                dnp_mismatch.setdefault(owner, []).append(c["ref"])
            if mode == "kicad" and kd is None and owner and not c["dnp"]:   # KiCad omits an entry equal to the base
                dnp_mismatch.setdefault(owner, []).append(c["ref"])
        elif alt_dnp is not None:
            dnp = alt_dnp
        elif kd and kd["dnp"] is not None and kd["dnp"] != c["dnp"]:
            if state == "alternate":
                dnp = kd["dnp"]
            else:
                dnp = c["dnp"]
                flip_mismatch.setdefault(owner or "(no block)", []).append(c["ref"])
        else:
            dnp = c["dnp"]
        in_bom = c["in_bom"] if not (kd and kd["in_bom"] is not None) else kd["in_bom"]
        if "Footprint" in overrides and overrides["Footprint"] != c["footprint"]:
            footprint.append(c["ref"])
        if owner and state == "alternate" and (overrides or dnp != c["dnp"]):
            overridden[owner] = overridden.get(owner, 0) + 1
        rows.append({"ref": c["ref"], "block": owner, "block_field": bf, "state": state,
                     "value": overrides.get("Value", c["value"]), "footprint": c["footprint"],
                     "mpn": overrides.get("MPN", c["fields"].get("MPN", "")),
                     "manufacturer": overrides.get("Manufacturer", c["fields"].get("Manufacturer", "")),
                     "dnp": dnp, "base_dnp": c["dnp"], "in_bom": in_bom, "overrides": overrides,
                     "sheet": c["sheet"]})
    where = f"{variant.path} (schematic)"
    for (bf, owner), refs in sorted(undeclared.items()):
        findings.append(_f("warning", "sch.block-undeclared", where,
                           f"Block {bf} ({len(refs)} parts) is not in the catalog: resolved to {owner} by prefix "
                           "(declare it as a sub-block)"))
    for bf, refs in sorted(unknown.items()):
        findings.append(_f("error", "sch.block-unknown", where,
                           f"Block {bf} ({len(refs)} parts: {', '.join(refs[:5])}) matches no catalog block"))
    if missing:
        findings.append(_f("warning", "sch.block-field-missing", where,
                           f"{len(missing)} parts have no Block field: {', '.join(missing[:8])}"))
    for bid in sorted(set(catalog.active() if catalog else {}) - seen_blocks):
        findings.append(_f("warning", "sch.block-empty", where, f"{bid} has no part in the schematic"))
    for bid, refs in sorted(dnp_mismatch.items()):
        findings.append(_f("error", "kicad.dnp-mismatch", where,
                           f"{bid} is dnp in the variant file but KiCad's variant '{name}' fits {len(refs)} of its "
                           f"parts ({', '.join(refs[:5])}{'…' if len(refs) > 5 else ''}); the variant file wins"))
    for bid, refs in sorted(flip_mismatch.items()):
        findings.append(_f("error", "kicad.dnp-mismatch", where,
                           f"KiCad's variant '{name}' changes the DNP flag of {len(refs)} parts of {bid}, which the "
                           f"variant file fits ({', '.join(refs[:5])}); the variant file wins"))
    if field_mismatch:
        findings.append(_f("error", "kicad.field-mismatch", where,
                           f"KiCad's variant data disagrees with the Alt fields: {'; '.join(field_mismatch[:5])}"))
    if footprint:
        findings.append(_f("error", "alternate.footprint", where,
                           f"an override changes the footprint of {', '.join(footprint)}: an alternate on another "
                           "land is a DNP footprint pair, never a footprint swap (copper is shared)"))
    for bid, e in sorted(variant.blocks.items()):
        if isinstance(e, dict) and e.get("state") == "alternate" and bid in seen_blocks and not overridden.get(bid):
            findings.append(_f("error", "alternate.no-data", where,
                               f"{bid} selects {e.get('alternate')!r} but no part of the block changes: write its "
                               f"Alt.{e.get('alternate')}.<Field> fields or KiCad variant data"))
    return rows, findings, mode


def bom(rows):
    """Lines grouped by value, footprint, MPN and manufacturer; fitted lines first, then DNP lines."""
    groups = {}
    for r in rows:
        if not r["in_bom"]:
            continue
        key = (r["dnp"], r["value"], r["footprint"], r["mpn"], r["manufacturer"])
        groups.setdefault(key, []).append(r)
    lines = []
    for (dnp, value, fp, mpn, mfr), rs in groups.items():
        refs = sorted((r["ref"] for r in rs), key=natural)
        lines.append({"refs": refs, "quantity": len(refs), "value": value, "footprint": fp, "mpn": mpn,
                      "manufacturer": mfr, "blocks": sorted({r["block"] or "" for r in rs}), "dnp": dnp})
    lines.sort(key=lambda ln: (ln["dnp"], natural(ln["refs"][0])))
    return lines


def diff_blocks(a, b, catalog):
    out = []
    for bid in sorted(set(a.blocks) | set(b.blocks)):
        ea = a.blocks.get(bid) if isinstance(a.blocks.get(bid), dict) else {}
        eb = b.blocks.get(bid) if isinstance(b.blocks.get(bid), dict) else {}
        changes = {k: [ea.get(k), eb.get(k)] for k in ("state", "alternate", "params", "open")
                   if ea.get(k) != eb.get(k)}
        if changes:
            out.append({"block": bid, "changes": changes})
    return out


def diff_parts(rows_a, rows_b):
    ra, rb = {r["ref"]: r for r in rows_a}, {r["ref"]: r for r in rows_b}
    out = []
    for ref in sorted(set(ra) | set(rb), key=natural):
        x, y = ra.get(ref), rb.get(ref)
        if not x or not y:
            out.append({"ref": ref, "change": "only in " + ("b" if y else "a")})
            continue
        ch = {}
        for k in ("dnp", "value", "mpn", "manufacturer", "in_bom"):
            if x[k] != y[k]:
                ch[k] = [x[k], y[k]]
        if ch:
            out.append({"ref": ref, "block": x["block"], "changes": ch})
    return out


def diff_lines(bom_a, bom_b):
    def counts(lines):
        c = {}
        for ln in lines:
            if not ln["dnp"]:
                key = (ln["mpn"] or ln["value"], ln["footprint"])
                c[key] = c.get(key, 0) + ln["quantity"]
        return c
    ca, cb = counts(bom_a), counts(bom_b)
    return [{"part": k[0], "footprint": k[1], "quantity": [ca.get(k, 0), cb.get(k, 0)]}
            for k in sorted(set(ca) | set(cb)) if ca.get(k, 0) != cb.get(k, 0)]
