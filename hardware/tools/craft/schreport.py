"""The ERC fold, the netlist names, the configuration and the report of the schematic craft check
(tools/sch-craft-check.py). bind(names) gives it the check script's constants. Python 3.9."""
import json
import os
import shutil
import subprocess
import tempfile

from schmodel import Check, ref_prefix
from sexpr import kid, kids, parse, val

# Bound from the check script (bind):
ERC_RULES = KICAD_APP = LEVELS = SPEC = None


def bind(names):
    """Take the check script's upper-case constants (its limits and sets, read from tools/craft/rules.toml)."""
    globals().update({k: v for k, v in names.items() if k.isupper()})


# ---------------------------------------------------------------- kicad-cli

def kicad_cli():
    bundled = os.path.join(KICAD_APP, "Contents", "MacOS", "kicad-cli")
    return bundled if os.path.exists(bundled) else shutil.which("kicad-cli")


def netlist_names(root, c):
    cli = kicad_cli()
    if not cli:
        c.applicable = False
        c.info.append("kicad-cli not found")
        return
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "net")
        subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "--output", out, root],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out, encoding="utf-8") as fh:
            net = parse(fh.read())
    for n in kids(kid(net, "nets") or ["nets"], "net"):
        name = val(n, "name", "")
        refs = {val(node, "ref") for node in kids(n, "node")}
        ics = sorted(r for r in refs if ref_prefix(r) == "U")
        if len(ics) >= 2 and (name.startswith("Net-(") or name.startswith("/Net-(")):
            c.fail(f"unnamed net {name} joins {', '.join(ics)}")


def fold_erc(root, c, out):
    cli = kicad_cli()
    if not cli:
        k = Check("ERC", "Q0", "kicad-cli ERC", "ERC")
        k.fail("kicad-cli not found (set KICAD_APP or use --no-erc)")
        out.append(k)
        return
    with tempfile.TemporaryDirectory() as tmp:
        rep = os.path.join(tmp, "erc.json")
        subprocess.run([cli, "sch", "erc", "--format", "json", "--severity-all", "--units", "mm",
                        "--output", rep, root], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        with open(rep, encoding="utf-8") as fh:
            report = json.load(fh)
    err = Check("ERC", "Q0", "Electrical rules (ERC errors not tied to a CRAFT rule)", "ERC")
    warn = Check("ERC-W", "Q4", "ERC warnings (listed, gate nothing)", "ERC")
    warn.applicable = False
    for sheet in report.get("sheets", []):
        for v in sheet.get("violations", []):
            where = f"{sheet.get('path', '/')}: " + ", ".join(i["description"] for i in v.get("items", []))
            rule = ERC_RULES.get(v["type"])
            if rule and rule in c:
                c[rule].fail(f"ERC {v['type']}: {where}")
                if c[rule].enforcement == "CHK":
                    c[rule].enforcement = "CHK+ERC"
            elif v.get("severity") == "error":
                err.fail(f"{v['type']}: {where}")
            else:
                warn.info.append(f"{v['type']}: {where}")
    out += [err, warn]


# ---------------------------------------------------------------- report

def reached(results):
    level = None
    for lv in LEVELS:
        if any(r.status == "fail" and r.level == lv for r in results):
            break
        level = lv
    return level


def report(path, results, target, as_json, cfg_path, erc):
    level = reached(results)
    gate = [r for r in results if r.status == "fail" and LEVELS.index(r.level) <= LEVELS.index(target)]
    debt = [r for r in results if r.status == "fail" and LEVELS.index(r.level) > LEVELS.index(target)]
    if as_json:
        print(json.dumps({
            "schema": "opendap.craft-check/v1", "kind": "schematic" if path.endswith(".kicad_sch") else "symbols",
            "file": path, "spec": SPEC, "config": cfg_path if cfg_path and os.path.exists(cfg_path) else None,
            "erc": erc, "target": target, "reached": level, "target_met": not gate,
            "debt": [r.id for r in debt],
            "levels": {lv: [r.as_dict() for r in results if r.level == lv] for lv in LEVELS},
        }, indent=2))
    else:
        print(f"odapkit craft check: {os.path.relpath(path)}")
        for lv in LEVELS:
            group = [r for r in results if r.level == lv]
            if not group:
                continue
            print(lv)
            for r in group:
                mark = {"pass": "pass", "fail": "FAIL", "n/a": "n/a "}[r.status]
                print(f"  {mark}  {r.id:9} {r.title}  [{r.enforcement}]")
                for f in r.findings[:12]:
                    print(f"          - {f}")
                if len(r.findings) > 12:
                    print(f"          - ... {len(r.findings) - 12} more")
                for n in r.info[:6]:
                    print(f"          {n}")
        print(f"Level reached: {level or 'none (a Q0 item fails)'}. Target {target}: "
              f"{'met' if not gate else 'NOT met'}"
              f"{f'; {len(debt)} item(s) of debt above it' if debt else ''}.")
    return 1 if gate else 0
