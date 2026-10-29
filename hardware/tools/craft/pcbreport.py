"""The DRC fold, the configuration and the report of the PCB craft check (tools/pcb-craft-check.py).

bind(names) gives this module the check script's constants (LEVELS, SPEC, KICAD_APP…). Python 3.9.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pcbmodel import Check
from sexpr import load_toml

KICAD_APP = LEVELS = SPEC = None  # bound from the check script (bind)


def bind(names):
    """Take the check script's upper-case constants."""
    globals().update({k: v for k, v in names.items() if k.isupper()})


# ---------------------------------------------------------------- DRC fold

def kicad_cli():
    bundled = os.path.join(KICAD_APP, "Contents", "MacOS", "kicad-cli")
    return bundled if os.path.exists(bundled) else shutil.which("kicad-cli")


def fold_drc(path, out):
    cli = kicad_cli()
    if not cli:
        c = Check("DRC", "Q0", "kicad-cli DRC", "DRC")
        c.fail("kicad-cli not found (set KICAD_APP or use --no-drc)")
        out.append(c)
        return
    with tempfile.TemporaryDirectory() as tmp:
        rep = os.path.join(tmp, "drc.json")
        subprocess.run([cli, "pcb", "drc", "--format", "json", "--severity-all", "--units", "mm",
                        "--output", rep, path], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        with open(rep, encoding="utf-8") as fh:
            report = json.load(fh)
    tagged = {}
    dru = path[: -len(".kicad_pcb")] + ".kicad_dru"
    if os.path.exists(dru):
        with open(dru, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'\s*\(rule\s+"([^"]*\[(Q[0-4])\][^"]*)"', line)
                if m:
                    tagged.setdefault(m.group(1), m.group(2))
    rules = {}
    for name, level in tagged.items():
        cid = re.search(r"([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+)", name)
        rules[name] = Check(cid.group(1) if cid else name, level, name.split(": ", 1)[-1], "DRC")
    untagged_err = Check("DRC", "Q0", "Fab and electrical DRC (untagged errors, unconnected items)", "DRC")
    warn = Check("DRC-W", "Q4", "Untagged DRC warnings (listed, gate nothing)", "DRC")
    warn.applicable = False
    for v in report.get("violations", []):
        m = re.search(r"\(rule '([^']+)'", v["description"])
        where = ", ".join(i["description"] for i in v.get("items", []))
        name = m.group(1) if m else None
        level = re.search(r"\[(Q[0-4])\]", name).group(1) if name and re.search(r"\[Q[0-4]\]", name) else None
        if level:
            if name not in rules:
                rules[name] = Check(name, level, name, "DRC")
            rules[name].fail(where)
        elif v.get("severity") == "error":
            untagged_err.fail(f"{v['type']}: {where}")
        else:
            warn.info.append(f"{v['type']}: {where}")
    for u in report.get("unconnected_items", []):
        untagged_err.fail("unconnected: " + ", ".join(i["description"] for i in u.get("items", [])))
    out += list(rules.values()) + [untagged_err, warn]


# ---------------------------------------------------------------- report

def load_config(path):
    return load_toml(path, "pcb-craft-check")


def reached(results):
    level = None
    for lv in LEVELS:
        if any(r.status == "fail" and r.level == lv for r in results):
            break
        level = lv
    return level


def order(r):
    """Report order: the craft checks before DRC, then by rule number."""
    m = re.search(r"-(\d+)$", r.id)
    return (r.enforcement != "CHK", int(m.group(1)) if m else 0, r.id)


def render(path, results, target, as_json, cfg_path, no_drc):
    """Print the report (text, or opendap.craft-check/v1 with --json); returns the failures that gate."""
    level = reached(results)
    gate = [r for r in results if r.status == "fail" and LEVELS.index(r.level) <= LEVELS.index(target)]
    debt = [r for r in results if r.status == "fail" and LEVELS.index(r.level) > LEVELS.index(target)]
    if as_json:
        print(json.dumps({
            "schema": "opendap.craft-check/v1", "board": path, "spec": SPEC,
            "config": cfg_path if os.path.exists(cfg_path) else None, "drc": not no_drc,
            "target": target, "reached": level, "target_met": not gate,
            "debt": [r.id for r in debt],
            "levels": {lv: [r.as_dict() for r in results if r.level == lv] for lv in LEVELS},
        }, indent=2))
    else:
        print(f"odw craft check: {os.path.relpath(path)}")
        for lv in LEVELS:
            group = [r for r in results if r.level == lv]
            if not group:
                continue
            print(f"{lv}")
            for r in group:
                mark = {"pass": "pass", "fail": "FAIL", "n/a": "n/a "}[r.status]
                print(f"  {mark}  {r.id:9} {r.title}  [{r.enforcement}]")
                for f in r.findings[:12]:
                    print(f"          - {f}")
                if len(r.findings) > 12:
                    print(f"          - ... {len(r.findings) - 12} more")
                for e in r.exempt:
                    print(f"          exempt: {e}")
                for n in r.info[:12]:
                    print(f"          {n}")
                if len(r.info) > 12:
                    print(f"          ... {len(r.info) - 12} more (--json lists all)")
        print(f"Level reached: {level or 'none (a Q0 item fails)'}. Target {target}: "
              f"{'met' if not gate else 'NOT met'}"
              f"{f'; {len(debt)} item(s) of debt above it' if debt else ''}.")
    return gate
